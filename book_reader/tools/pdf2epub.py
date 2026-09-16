#!/usr/bin/env python3
"""Turn a text-layer PDF into an epub this reader can do something with.

Aimed at the scanned-and-OCR'd scholarly book: the text is all there, but it
arrives as 500 loose pages of hard-wrapped lines wearing a running header. What
comes out the other side is a normal epub with chapters, paragraphs, and — the
part that matters here — an ``<a id="page_N">`` anchor at every page boundary,
carrying the *printed* page number lifted from the header before it is thrown
away. The reader then pages by the numbers in the physical book.

Not for a PDF without a text layer; run OCR first. Images are not carried over:
in a scan every page is an image, and the useful ones cannot be told from the
paper they are printed on.

    python tools/pdf2epub.py book.pdf out.epub [--title T] [--author A]
"""
import argparse
import html
import os
import re
import subprocess
import sys
import unicodedata
import zipfile
from collections import Counter

SOFT_HYPHEN = "­"
# A header/footer line is short, sits at the very top or bottom of the page, and
# says the same thing on many pages. Any one of those alone would eat real text.
EDGE_LINES = 3
HEADER_MIN_REPEATS = 5
HEADER_MAX_LEN = 70


def pdf_pages(path: str) -> list[str]:
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"],
                         capture_output=True, check=True).stdout.decode("utf-8", "replace")
    return out.split("\f")


def outline(path: str) -> list[tuple[int, str, int]]:
    """[(page index, title, depth)] from the PDF's bookmarks, if it has any."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    try:
        reader = PdfReader(path)
    except Exception:
        return []
    found: list[tuple[int, str, int]] = []

    def walk(items, depth=0):
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(item)
            except Exception:
                continue
            title = " ".join((item.title or "").split())
            if title:
                found.append((page, title, depth))

    try:
        walk(reader.outline)
    except Exception:
        return []
    found.sort(key=lambda t: t[0])
    return found


def running_lines(pages: list[str]) -> set[str]:
    """Text that recurs at the edge of many pages: the running head and folio."""
    counts: Counter = Counter()
    for page in pages:
        lines = [ln.strip() for ln in page.split("\n") if ln.strip()]
        for ln in lines[:EDGE_LINES] + lines[-EDGE_LINES:]:
            if len(ln) <= HEADER_MAX_LEN:
                counts[ln] += 1
    return {ln for ln, n in counts.items()
            if n >= HEADER_MIN_REPEATS and not ln.isdigit()}


def strip_edges(page: str, running: set[str]) -> tuple[list[str], list[str]]:
    """Drop the page furniture, returning the body and any bare numbers seen.

    The numbers are candidates for the printed folio; which one is real is
    decided later, across the whole book, because a lone page cannot tell a
    folio from a table cell or a stray note number.
    """
    lines = [ln.rstrip() for ln in page.split("\n")]
    folios: list[str] = []

    def furniture(ln: str) -> bool:
        s = ln.strip()
        if not s or s in running:
            return True
        return len(s) <= 3 and not s.isalnum()     # stray OCR marks: / % *

    head = 0
    for i in range(min(EDGE_LINES, len(lines))):
        s = lines[i].strip()
        if s.isdigit():
            folios.append(s)
            head = i + 1
        elif furniture(lines[i]):
            head = i + 1
        else:
            break
    tail = len(lines)
    for i in range(len(lines) - 1, max(len(lines) - EDGE_LINES, head) - 1, -1):
        s = lines[i].strip()
        if s.isdigit():
            folios.append(s)
            tail = i
        elif furniture(lines[i]):
            tail = i
        else:
            break
    return lines[head:tail], folios


def folio_offset(candidates: list[list[str]]) -> int | None:
    """The constant c where `printed page = pdf page + c`, if there is one.

    A real folio sequence differs from the page index by a fixed offset — front
    matter aside — so the offset shared by the most pages is the book's, and
    every number that disagrees was never a folio. Nothing else distinguishes
    "66" the page number from "66" the sample size in a table.
    """
    votes: Counter = Counter()
    for i, cands in enumerate(candidates):
        for c in cands:
            if len(c) <= 4:                        # a folio is not five digits
                votes[int(c) - i] += 1
    if not votes:
        return None
    offset, n = votes.most_common(1)[0]
    return offset if n >= max(10, len(candidates) // 8) else None
def paragraphs(lines: list[str]) -> list[str]:
    """Rejoin hard-wrapped lines, undoing hyphenation at the break."""
    text = "\n".join(lines)
    # every soft hyphen in this corpus sits at a line break and is pure
    # hyphenation: rejoin with nothing
    text = re.sub(SOFT_HYPHEN + r"\s*\n\s*", "", text)
    # a real hyphen at a break is ambiguous, so keep it — losing "post-1644"
    # is worse than carrying one hyphen too many
    text = re.sub(r"-\s*\n\s*", "-", text)
    out, buf = [], []
    for ln in text.split("\n"):
        if ln.strip():
            buf.append(ln.strip())
        elif buf:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    fixed = []
    for para in out:
        # OCR splits a decorated initial off its word: "T h e conquest elite".
        # Join the loose letters only — the space before the next word stays.
        para = re.sub(r"^([A-Z](?:\s+[a-z]){1,4})\s+(?=[a-z])",
                      lambda m: re.sub(r"\s+", "", m.group(1)) + " ", para)
        fixed.append(unicodedata.normalize("NFC", para))
    return [p for p in fixed if p]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _same_heading(para: str, title: str) -> bool:
    """Whether a paragraph is the chapter title the bookmark already gave us."""
    a, b = _norm(para), _norm(title)
    return bool(a) and len(a) < 120 and (a == b or a in b or b in a)


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def mark_notes(text: str) -> str:
    """Endnote markers come through glued to the word: `additions.26`."""
    return re.sub(r"(?<=[a-z\).,;”])(\d{1,3})(?=\s|$)", r"<sup>\1</sup>", text)


XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><meta charset="utf-8"/><title>{title}</title>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>{body}</body></html>
"""

CSS = """body { margin: 0; }
p { margin: 0 0 0.2em; text-indent: 1.4em; text-align: justify; }
p.first { text-indent: 0; margin-top: 1em; }
h1 { font-size: 1.5em; margin: 1.5em 0 1em; text-align: left; }
h2 { font-size: 1.2em; margin: 1.5em 0 0.5em; text-align: left; }
sup { font-size: 0.75em; vertical-align: super; }
"""


def render_cover(pdf: str, width: int = 600) -> bytes | None:
    """The first page as a cover image, so the shelf has something to show."""
    import tempfile

    # pdftoppm's "-" stdout target is unreliable across poppler builds (it can
    # exit 0 having written nothing); -singlefile to a real path always works
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "cover")
        try:
            subprocess.run(["pdftoppm", "-jpeg", "-singlefile",
                            "-scale-to-x", str(width), "-scale-to-y", "-1",
                            "-f", "1", "-l", "1", pdf, prefix],
                           capture_output=True, check=True, timeout=120)
            with open(prefix + ".jpg", "rb") as fh:
                data = fh.read()
            return data or None
        except Exception:  # noqa: BLE001 — a shelf without a cover is survivable
            return None


def build(pdf: str, out: str, title: str, author: str) -> None:
    pages = pdf_pages(pdf)
    if sum(len(p.strip()) for p in pages) < 1000:
        sys.exit("This PDF has almost no text layer — OCR it first.")
    running = running_lines(pages)

    marks = outline(pdf)
    # a chapter starts at each top-level-or-deeper bookmark; everything before
    # the first one is front matter
    starts = [(0, "Front matter")] + [(pg, t) for pg, t, _ in marks if pg > 0]
    seen, chapters = set(), []
    for pg, t in starts:
        if pg not in seen:
            seen.add(pg)
            chapters.append([pg, t])
    chapters.sort()
    bounds = [(chapters[i][0],
               chapters[i + 1][0] if i + 1 < len(chapters) else len(pages),
               chapters[i][1]) for i in range(len(chapters))]

    # first pass: strip furniture, collect folio candidates
    stripped = [strip_edges(pg, running) for pg in pages]
    offset = folio_offset([c for _, c in stripped])

    def label_for(i: int, cands: list[str]) -> str:
        if offset is None:
            return str(i + 1)
        want = i + offset
        if want < 1:                               # front matter, before page 1
            return str(i + 1)
        for c in cands:                            # prefer the number on the page
            if c.isdigit() and int(c) == want:
                return c
        return str(want)                           # folio omitted on this page

    docs, nav, npages = [], [], 0
    for n, (lo, hi, name) in enumerate(bounds):
        body = [f"<h1>{esc(name)}</h1>"]
        heading_seen = False
        for pi in range(lo, hi):
            lines, cands = stripped[pi]
            paras = paragraphs(lines)
            body.append(f'<a id="page_{esc(label_for(pi, cands))}"></a>')
            npages += 1
            for para in paras:
                # the bookmark already supplied the heading, and the page repeats
                # it — often split over two lines ("Chapter 2" / the title), so
                # keep skipping until real prose starts
                if not heading_seen:
                    if _same_heading(para, name):
                        continue
                    heading_seen = True
                cls = ' class="first"' if not body[-1].startswith("<p") else ""
                body.append(f"<p{cls}>{mark_notes(esc(para))}</p>")
        docs.append((f"ch{n:03d}.xhtml", name, "\n".join(body)))
        nav.append((f"ch{n:03d}.xhtml", name))

    manifest = "\n".join(
        f'    <item id="c{i}" href="{f}" media-type="application/xhtml+xml"/>'
        for i, (f, _, _) in enumerate(docs))
    spine = "\n".join(f'    <itemref idref="c{i}"/>' for i in range(len(docs)))
    navpoints = "\n".join(
        f'    <navPoint id="n{i}" playOrder="{i + 1}"><navLabel><text>{esc(t)}</text>'
        f'</navLabel><content src="{f}"/></navPoint>' for i, (f, t) in enumerate(nav))

    with zipfile.ZipFile(out, "w") as z:
        # the mimetype entry must be first and uncompressed
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?>\n<container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                   '<rootfiles><rootfile full-path="content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("style.css", CSS)
        cover = render_cover(pdf)
        if cover:
            z.writestr("cover.jpg", cover)
        for fn, t, body in docs:
            z.writestr(fn, XHTML.format(title=esc(t), body=body))
        cover_item = ('    <item id="cover" href="cover.jpg" media-type="image/jpeg" '
                      'properties="cover-image"/>\n' if cover else "")
        cover_meta = '    <meta name="cover" content="cover"/>\n' if cover else ""
        z.writestr("content.opf", f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bid">{esc(os.path.basename(pdf))}</dc:identifier>
    <dc:title>{esc(title)}</dc:title>
    <dc:creator>{esc(author)}</dc:creator>
    <dc:language>en</dc:language>
{cover_meta}  </metadata>
  <manifest>
{manifest}
    <item id="css" href="style.css" media-type="text/css"/>
{cover_item}    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
{spine}
  </spine>
</package>""")
        z.writestr("toc.ncx", f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{esc(os.path.basename(pdf))}"/></head>
  <docTitle><text>{esc(title)}</text></docTitle>
  <navMap>
{navpoints}
  </navMap>
</ncx>""")

    print(f"{out}\n  {len(docs)} chapters, {npages} page anchors, "
          f"{len(running)} running-header variants removed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("epub")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    a = ap.parse_args()
    build(a.pdf, a.epub, a.title or os.path.splitext(os.path.basename(a.pdf))[0], a.author)
