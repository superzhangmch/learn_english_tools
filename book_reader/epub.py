"""EPUB reading + markup normalisation.

Reads an epub straight out of the zip (nothing is extracted to disk) and turns
each spine document into HTML the reader can drop into the page.

The approach is deliberately publisher-agnostic — nothing here keys off one
book's class names:

* **Class names are kept as-is and the book's own stylesheet is reused**, scoped
  under ``#article`` and filtered (see :meth:`Epub.stylesheet`). Publishers carry
  real meaning in their classes — a hanging indent for a bibliography entry, a
  figure caption, a chronology line — but every publisher spells them
  differently, so translating names by hand only ever works for one book.
  Letting the epub's CSS style its own classes works for all of them.
* **Print-page anchors are used when present, and absent gracefully.** Many
  epubs embed the printed page boundaries as empty ``<a id="page_N">`` anchors;
  where they exist the reader gets page numbers that match the paper book. Where
  they don't, :attr:`Chapter.pages` is simply empty and the reader falls back to
  paging by screen.
"""
import posixpath
import re
import zipfile
from dataclasses import dataclass, field

import tinycss2
from lxml import etree
from lxml import html as lhtml

import pagination

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
XHTML_NS = "http://www.w3.org/1999/xhtml"

ALLOWED_TAGS = {
    "p", "div", "span", "a", "em", "strong", "b", "i", "u", "small", "sup", "sub",
    "br", "h1", "h2", "h3", "h4", "h5", "h6", "img", "blockquote", "ul", "ol",
    "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
    "col", "colgroup", "caption", "figure", "figcaption", "hr", "cite", "q",
}
# Tags whose *contents* go too (as opposed to being unwrapped).
DROP_TAGS = {"script", "style", "link", "meta", "title", "head", "svg", "iframe", "object"}
# width/height are deliberately absent: the publisher's values are tuned for a
# 6-inch e-reader page (a 15% date column, fixed image pixel sizes) and squeeze
# content at our measure. Intrinsic image sizes survive as data-w/data-h.
ALLOWED_ATTRS = {"id", "class", "href", "src", "alt", "colspan", "rowspan",
                 "data-page", "data-full", "data-w", "data-h", "target", "rel"}

# Figures load as a server-downscaled thumbnail; the full image is fetched only
# when the reader clicks it. Reading over a phone/tunnel, the figures are most of
# the bytes in a chapter, and most of the time you are reading the prose.
THUMB_WIDTH = 96
# Matched to the width the cover is actually displayed at. Set larger than the
# source image (covers here are ~476 px wide) and the "thumbnail" is the original
# re-encoded — no saving at all, which is how this came to cost 79 KB.
COVER_WIDTH = 320

# Block elements that force a paragraph break when extracting plain text.
TEXT_BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
               "figcaption", "td", "th", "dt", "dd"}


def _collapse(text: str) -> str:
    """Squeeze runs of spaces and blank lines, the way prose reads."""
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


# Declarations kept from the book's own stylesheet. Everything to do with layout
# geometry, colour and typeface is dropped: those are the reader's to decide (the
# theme, the measure, the font size, the image sizing) and publisher values fight
# them — a hardcoded colour breaks dark mode, a px font-size ignores the reader's
# choice, a px image width ignores the thumbnail. What is left is exactly the
# structural typography that carries meaning.
CSS_KEEP = {
    "font-size", "font-style", "font-weight", "font-variant", "font-variant-caps",
    "font-feature-settings", "line-height", "letter-spacing", "word-spacing",
    "text-align", "text-indent", "text-transform", "text-decoration",
    "vertical-align", "white-space",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "display", "list-style", "list-style-type", "list-style-position",
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-width", "border-style", "border-collapse", "border-spacing",
    "float", "clear", "quotes",
}
# A px/pt font-size overrides the reader's own size control, so only relative
# units survive for this one property.
RELATIVE_ONLY = {"font-size"}


@dataclass
class Chapter:
    idx: int
    href: str
    title: str = ""
    html: str = ""
    pages: list = field(default_factory=list)   # [{"page": "4", "id": "pg-4"}]
    body_class: str = ""                        # so body-level CSS rules apply


class Epub:
    """A parsed epub, served from an open zip handle."""

    def __init__(self, path: str):
        self.path = path
        self.zf = zipfile.ZipFile(path)
        self._names = set(self.zf.namelist())

        opf_path = self._find_opf()
        self.opf_dir = posixpath.dirname(opf_path)
        opf = etree.fromstring(self.zf.read(opf_path))

        self.meta = self._read_metadata(opf)
        manifest = self._read_manifest(opf)
        self.spine = self._read_spine(opf, manifest)

        # basename -> spine index, so intra-book links can be rewritten to routes
        self._href_to_idx = {}
        for i, href in enumerate(self.spine):
            self._href_to_idx[href] = i
            self._href_to_idx[posixpath.basename(href)] = i

        self._css_files = [i["href"] for i in manifest.values()
                           if i["media_type"] == "text/css" and i["href"] in self._names]
        self._img_dims = self._read_img_dims()
        self.cover = self._find_cover(manifest, opf)
        self.toc = self._read_toc(manifest)
        # optional: a page-list nav declaring the printed pagination
        nav = next((i["href"] for i in manifest.values() if "nav" in i["properties"]), "")
        self._nav_pages = pagination.nav_page_list(self.zf, self._names, nav)

        # TOC label wins as a chapter title; it is what the reader shows
        self._titles = {}
        for entry in self.toc:
            self._titles.setdefault(entry["chapter"], entry["label"])

        self._cache: dict[int, Chapter] = {}
        self._pages: list | None = None

    # ---------- package files ----------

    def _find_opf(self) -> str:
        container = etree.fromstring(self.zf.read("META-INF/container.xml"))
        rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
        if rootfile is not None and rootfile.get("full-path"):
            return rootfile.get("full-path")
        for name in self._names:                       # malformed container
            if name.endswith(".opf"):
                return name
        raise ValueError("no OPF found in epub")

    def _read_metadata(self, opf) -> dict:
        def text(tag):
            el = opf.find(f".//{{{DC_NS}}}{tag}")
            return (el.text or "").strip() if el is not None and el.text else ""

        creators = [
            (el.text or "").strip()
            for el in opf.findall(f".//{{{DC_NS}}}creator")
            if el.text and el.text.strip()
        ]
        return {
            "title": text("title") or "Untitled",
            "author": ", ".join(creators),
            "publisher": text("publisher"),
            "date": text("date")[:10],
            "language": text("language"),
            "description": text("description"),
        }

    def _read_manifest(self, opf) -> dict:
        out = {}
        for item in opf.findall(f".//{{{OPF_NS}}}manifest/{{{OPF_NS}}}item"):
            out[item.get("id")] = {
                "href": self._resolve(item.get("href", "")),
                "media_type": item.get("media-type", ""),
                "properties": item.get("properties", ""),
            }
        return out

    def _read_spine(self, opf, manifest) -> list:
        hrefs = []
        for ref in opf.findall(f".//{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref"):
            item = manifest.get(ref.get("idref"))
            if item and item["href"] in self._names:
                hrefs.append(item["href"])
        return hrefs

    def _resolve(self, href: str) -> str:
        """OPF-relative href -> zip entry name."""
        href = href.split("#")[0]
        return posixpath.normpath(posixpath.join(self.opf_dir, href)) if self.opf_dir else href

    def _find_cover(self, manifest, opf) -> str:
        for item in manifest.values():                 # epub3
            if "cover-image" in item["properties"]:
                return item["href"]
        for m in opf.findall(f".//{{{OPF_NS}}}meta"):  # epub2
            if m.get("name") == "cover":
                item = manifest.get(m.get("content"))
                if item:
                    return item["href"]
        for name in ("cover.jpeg", "cover.jpg", "cover.png"):
            if self._resolve(name) in self._names:
                return self._resolve(name)
        return ""

    def _read_img_dims(self) -> dict:
        """Classes that pin an exact pixel box, e.g. `.x {width:410px;height:295px}`.

        Converters commonly record each figure's intrinsic size this way. Kept as
        the aspect ratio for the thumbnail box, so text does not jump as figures
        load. Harmless when a book has no such classes.
        """
        dims = {}
        for name in self._css_files:
            css = self.zf.read(name).decode("utf-8", "replace")
            for m in re.finditer(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}", css):
                body = m.group(2)
                w = re.search(r"width:\s*(\d+)px", body)
                h = re.search(r"height:\s*(\d+)px", body)
                if w and h:
                    dims[m.group(1)] = (int(w.group(1)), int(h.group(1)))
        return dims

    def stylesheet(self) -> str:
        """The book's own CSS, filtered and scoped under `#article`.

        Reusing the publisher's stylesheet is what keeps this reader general: the
        classes on the markup mean something, but what they mean is spelled
        differently in every book, so the only source that reliably knows is the
        book itself. Declarations are whitelisted (see CSS_KEEP) and at-rules
        dropped, so a stylesheet cannot break the theme or escape the article.
        """
        out = []
        for name in self._css_files:
            try:
                css = self.zf.read(name).decode("utf-8", "replace")
            except KeyError:
                continue
            for rule in tinycss2.parse_stylesheet(
                css, skip_comments=True, skip_whitespace=True
            ):
                if rule.type != "qualified-rule":       # @page, @font-face, @media
                    continue
                selector = self._scope(tinycss2.serialize(rule.prelude))
                body = self._filter_decls(rule.content)
                if selector and body:
                    out.append(f"{selector}{{{body}}}")
        return "\n".join(out)

    @staticmethod
    def _scope(prelude: str) -> str:
        """Confine each selector to the article container."""
        parts = []
        for sel in prelude.split(","):
            sel = " ".join(sel.split())
            if not sel or "#article" in sel:
                continue
            # html/body rules describe the page as a whole; the article element
            # stands in for it, and carries the source body's class
            m = re.match(r"^(?:html|body)\b(.*)$", sel)
            if m:
                rest = m.group(1)
                sel = "#article" + (rest if rest[:1] in (".", "#", "[", ":") else " " + rest.strip())
                parts.append(sel.strip())
            elif re.fullmatch(r"[.\[][^\s>+~]*", sel):
                # a bare class selector may have been meant for <body> (the source
                # body's class is copied onto the container), so match both the
                # container itself and its descendants
                parts.append(f"#article{sel}, #article {sel}")
            else:
                parts.append(f"#article {sel}")
        return ", ".join(parts)

    @staticmethod
    def _filter_decls(content) -> str:
        decls = []
        for d in tinycss2.parse_declaration_list(content, skip_whitespace=True):
            if d.type != "declaration" or d.lower_name not in CSS_KEEP:
                continue
            value = tinycss2.serialize(d.value).strip()
            if d.lower_name in RELATIVE_ONLY and re.search(r"\d\s*(px|pt|pc|in|cm|mm)\b", value):
                continue
            if not value:
                continue
            decls.append(f"{d.lower_name}:{value}" + (" !important" if d.important else ""))
        return ";".join(decls)

    def _read_toc(self, manifest) -> list:
        entries = self._read_ncx(manifest) or self._read_nav(manifest)
        out = []
        for label, target in entries:
            href, _, anchor = target.partition("#")
            idx = self._href_to_idx.get(self._resolve(href))
            if idx is None:
                idx = self._href_to_idx.get(posixpath.basename(href))
            if idx is None:
                continue
            out.append({"label": label, "chapter": idx, "anchor": anchor or None})
        return out

    def _read_ncx(self, manifest) -> list:
        ncx = next((i["href"] for i in manifest.values()
                    if i["media_type"] == "application/x-dtbncx+xml"), None)
        if not ncx or ncx not in self._names:
            return []
        root = etree.fromstring(self.zf.read(ncx))
        out = []
        for nav in root.iter(f"{{{NCX_NS}}}navPoint"):
            text = nav.find(f"{{{NCX_NS}}}navLabel/{{{NCX_NS}}}text")
            content = nav.find(f"{{{NCX_NS}}}content")
            if text is not None and content is not None and content.get("src"):
                out.append(((text.text or "").strip(), content.get("src")))
        return out

    def _read_nav(self, manifest) -> list:
        """epub3 nav document, used when there is no NCX."""
        nav = next((i["href"] for i in manifest.values() if "nav" in i["properties"]), None)
        if not nav or nav not in self._names:
            return []
        doc = lhtml.fromstring(self.zf.read(nav))
        out = []
        for ol in doc.xpath("//*[local-name()='nav']//*[local-name()='ol']"):
            for a in ol.xpath(".//*[local-name()='a'][@href]"):
                label = " ".join(a.itertext()).strip()
                if label:
                    out.append((label, a.get("href")))
            break
        return out

    # ---------- resources ----------

    def has(self, name: str) -> bool:
        return name in self._names

    def open(self, name: str):
        return self.zf.open(name)

    # ---------- chapters ----------

    def chapter(self, idx: int) -> Chapter:
        if idx < 0 or idx >= len(self.spine):
            raise IndexError(idx)
        if idx not in self._cache:
            self._cache[idx] = self._render(idx)
        return self._cache[idx]

    def _render(self, idx: int) -> Chapter:
        href = self.spine[idx]
        ch = Chapter(idx=idx, href=href, title=self._titles.get(idx, ""))
        doc = lhtml.fromstring(self.zf.read(href))
        body = doc.find("body")
        if body is None:
            body = doc
        ch.body_class = body.get("class") or ""

        base = posixpath.dirname(href)
        self._unwrap_svg_cover(body)
        self._transform(body, base, idx, ch)

        parts = [body.text or ""]
        parts += [etree.tostring(c, encoding="unicode", method="html") for c in body]
        ch.html = "".join(parts).strip()
        if not ch.title:
            ch.title = self._first_heading(body)
        return ch

    def _first_heading(self, body) -> str:
        for el in body.iter():
            if isinstance(el.tag, str) and el.tag in ("h1", "h2", "h3"):
                text = " ".join(el.itertext()).strip()
                if text:
                    return re.sub(r"\s+", " ", text)
        return ""

    def _transform(self, body, base: str, idx: int, ch: Chapter) -> None:
        nav_labels = self._nav_pages.get(self.spine[idx], {})

        # The XHTML namespace would otherwise make every tag "{...}p".
        for el in body.iter():
            if isinstance(el.tag, str) and el.tag.startswith(f"{{{XHTML_NS}}}"):
                el.tag = etree.QName(el).localname

        for el in list(body.iter()):
            if not isinstance(el.tag, str):          # comment / PI
                if el.getparent() is not None:
                    el.getparent().remove(el)
                continue
            if el.tag in DROP_TAGS:
                if el.getparent() is not None:
                    el.getparent().remove(el)
                continue
            if el is body:
                continue

            if el.tag in ("a", "span", "div") and self._page_mark(el, ch, nav_labels):
                continue
            if el.tag == "img":
                self._fix_img(el, base)
            elif el.tag == "a":
                self._fix_link(el, idx)

            for attr in set(el.attrib) - ALLOWED_ATTRS:
                del el.attrib[attr]
            if el.tag not in ALLOWED_TAGS:
                el.drop_tag()

        # a heading wrapped entirely in a link back to the contents page reads as
        # a heading, not a link — drop the wrapper but keep the words
        for level in range(1, 7):
            for a in body.xpath(f".//h{level}//a"):
                a.drop_tag()

        self._group_figures(body)

    def _unwrap_svg_cover(self, body) -> None:
        """Title pages wrap the cover in <svg><image xlink:href>; keep the image.

        Runs before the SVG is dropped, otherwise the first page of the book
        renders blank.
        """
        for svg in body.xpath(".//*[local-name()='svg']"):
            image = next(iter(svg.xpath(".//*[local-name()='image']")), None)
            if image is None:
                continue
            # xlink:href in epub2, plain href in epub3; the HTML parser leaves
            # the prefix as a literal "xlink:href" rather than resolving it
            src = next((v for k, v in image.attrib.items()
                        if k.rpartition("}")[2].rpartition(":")[2] == "href"), "")
            if not src:
                continue
            img = etree.Element("img")
            img.set("src", src)
            img.set("alt", "cover")
            img.set("class", "cover-img")
            img.tail = svg.tail
            svg.addprevious(img)
            svg.getparent().remove(svg)

    def _page_mark(self, el, ch: Chapter, nav_labels: dict) -> bool:
        """Turn a printed-page marker into <span class="pgmark" data-page="…">.

        Which elements count is `pagination`'s business, not ours.
        """
        label = pagination.label_of(el, nav_labels)
        if not label:
            return False
        anchor = "pg-" + re.sub(r"[^A-Za-z0-9]+", "-", label)
        el.tag = "span"
        tail = el.tail
        el.attrib.clear()
        el.set("class", "pgmark")
        el.set("id", anchor)
        el.set("data-page", label)
        el.tail = tail
        ch.pages.append({"page": label, "id": anchor})
        return True

    def _fix_img(self, el, base: str) -> None:
        src = el.get("src") or ""
        if src.startswith(("http://", "https://", "data:")):
            return
        target = posixpath.normpath(posixpath.join(base, src)) if base else src
        full = "/api/res/" + target
        is_cover = "cover-img" in (el.get("class") or "").split()
        # point src at the thumbnail so the browser never fetches the full image
        # on its own; the reader swaps in data-full on click
        el.set("src", f"{full}?w={COVER_WIDTH if is_cover else THUMB_WIDTH}")
        el.set("data-full", full)
        # the per-image class is the only record of the intrinsic size; keep it as
        # data-* so the thumbnail box can reserve the right shape (width/height
        # attributes would describe the full image, not the thumbnail)
        for cls in (el.get("class") or "").split():
            if cls in self._img_dims:
                w, h = self._img_dims[cls]
                el.set("data-w", str(w))
                el.set("data-h", str(h))
                break

    def _fix_link(self, el, idx: int) -> None:
        href = el.get("href")
        if not href:                                 # a bare <a id="…"> target
            el.attrib.pop("href", None)
            return
        if href.startswith(("http://", "https://", "mailto:")):
            el.set("target", "_blank")
            el.set("rel", "noopener")
            return
        if href.startswith("#"):                     # same document, leave it
            return
        path, _, anchor = href.partition("#")
        target = self._href_to_idx.get(self._resolve(path))
        if target is None:
            target = self._href_to_idx.get(posixpath.basename(path))
        if target is None:                           # points outside the spine
            el.attrib.pop("href", None)
            return
        el.set("href", f"#/c/{target}" + (f"/{anchor}" if anchor else ""))

    def _group_figures(self, body) -> None:
        """<p><img></p> followed by a caption -> <figure><img><figcaption>.

        Grouping is what lets the reader lay a figure out as thumbnail-plus-caption
        and expand it on demand. The caption is recognised by its class mentioning
        "caption" — the one naming convention publishers do share — and its class
        is kept so the book's own CSS still styles it.
        """
        for img in body.xpath(".//img"):
            p = img.getparent()
            if p is None or p.tag not in ("p", "div"):
                continue
            if len(p) != 1 or (p.text or "").strip() or (img.tail or "").strip():
                continue
            fig = etree.Element("figure")
            fig.tail = p.tail
            p.addprevious(fig)
            p.getparent().remove(p)
            img.tail = None
            fig.append(img)
            nxt = fig.getnext()
            if nxt is not None and nxt.tag in ("p", "div") and \
                    "caption" in (nxt.get("class") or "").lower():
                nxt.tag = "figcaption"
                fig.append(nxt)

    # ---------- book-level views ----------

    def outline(self) -> dict:
        """Everything the reader needs up front: TOC and the print-page index."""
        chapters = [{"idx": i, "title": self.chapter(i).title,
                     "pages": len(self.chapter(i).pages)}
                    for i in range(len(self.spine))]
        pages = self.pages_index()
        return {
            **self.meta,
            "cover": f"/api/res/{self.cover}" if self.cover else "",
            "chapters": chapters,
            "toc": self.toc,
            "pages": pages,
            "total_pages": len(pages),
        }

    def pages_index(self) -> list:
        """Printed pages across the whole book, in reading order."""
        if self._pages is None:
            self._pages = [
                {"page": pg["page"], "chapter": i, "id": pg["id"]}
                for i in range(len(self.spine))
                for pg in self.chapter(i).pages
            ]
        return self._pages

    # ---------- plain text, for passing passages to a model ----------

    def chapter_text(self, idx: int) -> str:
        doc = lhtml.fromstring(f"<div>{self.chapter(idx).html}</div>")
        return _collapse(doc.text_content())

    def page_text(self, lo: int, hi: int) -> str:
        """Plain text of printed pages [lo, hi] of :meth:`pages_index`.

        Extracting here rather than in the browser means a question about twenty
        pages uploads two integers instead of 35 KB, and the reader never has to
        fetch neighbouring chapters just to assemble a range that crosses them.
        """
        pages = self.pages_index()
        if not pages:
            return ""
        lo, hi = max(0, lo), min(len(pages) - 1, hi)
        if lo > hi:
            return ""
        by_id = {p["id"]: n for n, p in enumerate(pages)}
        # include the chapter holding mark hi+1: page `hi` runs up to it, so its
        # tail can sit in the next chapter
        span = pages[lo:min(hi + 2, len(pages))]
        parts = [t for c in sorted({p["chapter"] for p in span})
                 if (t := self._chapter_page_text(c, lo, hi, by_id))]
        return "\n\n".join(parts)

    def _chapter_page_text(self, idx: int, lo: int, hi: int, by_id: dict) -> str:
        """One chapter's share of a page range, walked in document order."""
        root = lhtml.fromstring(f"<div>{self.chapter(idx).html}</div>")
        marks = root.xpath(".//*[contains(@class,'pgmark')]")
        # text before a chapter's first mark still belongs to the preceding page
        page = by_id.get(marks[0].get("id"), 0) - 1 if len(marks) else -1
        out: list[str] = []
        last_block = None

        def emit(text, node):
            nonlocal last_block
            if not text or not text.strip() or not (lo <= page <= hi):
                return
            block = node
            while block is not None and block.tag not in TEXT_BLOCKS:
                block = block.getparent()
            if block is not last_block:
                out.append("\n\n")
                last_block = block
            out.append(text)

        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            if "pgmark" in (el.get("class") or ""):
                n = by_id.get(el.get("id"))
                if n is not None:
                    page = n
            elif el.tag == "br" and lo <= page <= hi:
                out.append("\n")           # else a two-line title runs together
            emit(el.text, el)
            parent = el.getparent()
            emit(el.tail, parent if parent is not None else el)
        return _collapse("".join(out))
