"""Printed-page detection — an optional enrichment, kept out of the parser.

Where an epub records the paper book's page boundaries, the reader can page and
cite by real page numbers instead of ones invented from the window size. But
this is a bonus, not a given: plenty of epubs record nothing, and those that do
disagree about how. Four conventions turn up in the wild:

1. ``<a id="page_47"/>`` — an empty anchor whose id names the page. What epub2
   converters (calibre, and the OUP files it was fed) emit.
2. ``<span epub:type="pagebreak" title="47"/>`` — the epub3 vocabulary, with the
   label in ``title``, ``aria-label``, or the element's own text.
3. ``role="doc-pagebreak"`` — the ARIA spelling of the same thing.
4. A ``<nav epub:type="page-list">`` in the navigation document, pointing at ids
   that need not look like page markers at all.

This module knows about all four and nothing about any particular book. It is
also runnable, to check what a new epub offers before settling in to read it::

    python pagination.py path/to/book.epub
"""
import posixpath
import re

from lxml import html as lhtml

# "page_47", "page-iv", "pg12" — the label is whatever follows the prefix
PAGE_ID_RE = re.compile(r"^(?:page|pg)[_\-]?([0-9]+|[ivxlcdm]+)$", re.I)

OPS_NS = "http://www.idpf.org/2007/ops"
PAGEBREAK_HINTS = ("pagebreak", "doc-pagebreak")


def _epub_type(el) -> str:
    """`epub:type`, however the parser happened to represent the prefix."""
    return (el.get("epub:type") or el.get(f"{{{OPS_NS}}}type") or "").lower()


def _label_from_id(el_id: str | None) -> str | None:
    m = PAGE_ID_RE.match(el_id or "")
    return m.group(1) if m else None


def label_of(el, nav_labels: dict | None = None) -> str | None:
    """The printed page this element marks, or None if it marks nothing.

    `nav_labels` maps element id -> label for the current document, as declared
    by a page-list in the navigation document (convention 4).
    """
    el_id = el.get("id")
    if nav_labels and el_id and el_id in nav_labels:
        return nav_labels[el_id]

    if any(h in _epub_type(el) for h in PAGEBREAK_HINTS) or \
            any(h in (el.get("role") or "").lower() for h in PAGEBREAK_HINTS):
        text = (el.text or "").strip()
        return (el.get("title") or el.get("aria-label") or el.get("data-page")
                or _label_from_id(el_id) or text or None)

    # an empty anchor or span whose id names a page; must be empty, or we would
    # swallow real content that happens to sit on an id like "page_title"
    if el.tag in ("a", "span", "div") and not len(el) and not (el.text or "").strip():
        return _label_from_id(el_id)
    return None


def nav_page_list(zf, names, nav_href: str) -> dict:
    """Read a `page-list` nav, returning {document href: {anchor id: label}}.

    Empty for the great majority of epubs, which have no page-list at all.
    """
    out: dict[str, dict] = {}
    if not nav_href or nav_href not in names:
        return out
    try:
        doc = lhtml.fromstring(zf.read(nav_href))
    except Exception:  # noqa: BLE001 — a malformed nav is not worth failing over
        return out
    base = posixpath.dirname(nav_href)
    for nav in doc.xpath("//*[local-name()='nav']"):
        if "page-list" not in _epub_type(nav):
            continue
        for a in nav.xpath(".//*[local-name()='a'][@href]"):
            label = " ".join(a.itertext()).strip()
            path, _, anchor = a.get("href").partition("#")
            if not label or not anchor:
                continue
            target = posixpath.normpath(posixpath.join(base, path)) if base else path
            out.setdefault(target, {})[anchor] = label
    return out


if __name__ == "__main__":
    import sys
    import zipfile

    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} book.epub")

    from epub import Epub

    epub = Epub(sys.argv[1])
    outline = epub.outline()
    pages = outline["pages"]
    print(f"{outline['title']} — {outline['author']}")
    print(f"{len(outline['chapters'])} spine documents, {len(pages)} printed pages")
    if not pages:
        print("\nNo printed page markers found; the reader will page by screen.")
        sys.exit(0)
    print(f"\nfirst: {pages[0]['page']!r} (doc {pages[0]['chapter']})"
          f"   last: {pages[-1]['page']!r} (doc {pages[-1]['chapter']})")
    per_doc: dict[int, int] = {}
    for p in pages:
        per_doc[p["chapter"]] = per_doc.get(p["chapter"], 0) + 1
    print("\npages per document:")
    for c in outline["chapters"]:
        n = per_doc.get(c["idx"], 0)
        print(f"  {c['idx']:>3}  {n:>4}  {c['title'][:56]}")
