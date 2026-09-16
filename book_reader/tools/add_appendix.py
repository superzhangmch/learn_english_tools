#!/usr/bin/env python3
"""Append a chapter to an existing epub, as though the book had shipped with it.

Used to add reference matter a translation lacks — a glossary of names, a
chronology — so it turns up in the table of contents and at the end of the spine
like anything else, rather than living in a separate file you have to remember.

The book is rewritten rather than patched in place: a zip cannot have entries
inserted, and the mimetype entry has to stay first and uncompressed.

    python tools/add_appendix.py book.epub body.html --title "Glossary" [-o out.epub]
"""
import argparse
import os
import posixpath
import re
import shutil
import sys
import tempfile
import zipfile

from lxml import etree

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XHTML_NS = "http://www.w3.org/1999/xhtml"

PAGE = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><meta charset="utf-8"/><title>{title}</title>{css}</head>
<body>{body}</body>
</html>
"""


def find_opf(zf: zipfile.ZipFile) -> str:
    root = etree.fromstring(zf.read("META-INF/container.xml"))
    el = root.find(f".//{{{CONTAINER_NS}}}rootfile")
    if el is not None and el.get("full-path"):
        return el.get("full-path")
    return next(n for n in zf.namelist() if n.endswith(".opf"))


def add(src: str, body_html: str, title: str, out: str, ident: str = "appendix",
        extra_css: str = "") -> None:
    with zipfile.ZipFile(src) as zf:
        names = zf.namelist()
        opf_path = find_opf(zf)
        opf_dir = posixpath.dirname(opf_path)
        opf = etree.fromstring(zf.read(opf_path))

        manifest = opf.find(f"{{{OPF_NS}}}manifest")
        spine = opf.find(f"{{{OPF_NS}}}spine")
        if manifest is None or spine is None:
            sys.exit("OPF has no manifest/spine")

        # place the new document beside the ones already there
        docs = [i.get("href") for i in manifest
                if i.get("media-type") == "application/xhtml+xml"]
        sub = posixpath.dirname(docs[0]) if docs else ""
        href = posixpath.join(sub, f"{ident}.xhtml") if sub else f"{ident}.xhtml"
        full = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
        if full in names:
            sys.exit(f"{full} already exists — the appendix was added once already")

        # reuse whatever stylesheet the book's own chapters link, so the appendix
        # inherits its typography instead of looking pasted in
        css = ""
        if docs:
            first = posixpath.normpath(posixpath.join(opf_dir, docs[0])) if opf_dir else docs[0]
            for m in re.finditer(rb'<link[^>]+rel=["\']stylesheet["\'][^>]*>',
                                 zf.read(first), re.I):
                css += m.group(0).decode("utf-8", "replace")

        ident_n, n = ident, 2
        while any(i.get("id") == ident_n for i in manifest):
            ident_n, n = f"{ident}{n}", n + 1

        item = etree.SubElement(manifest, f"{{{OPF_NS}}}item")
        item.set("id", ident_n)
        item.set("href", href)
        item.set("media-type", "application/xhtml+xml")
        ref = etree.SubElement(spine, f"{{{OPF_NS}}}itemref")
        ref.set("idref", ident_n)

        # table of contents: NCX for epub2, the nav document for epub3
        ncx_out = nav_out = None
        ncx_item = next((i for i in manifest
                         if i.get("media-type") == "application/x-dtbncx+xml"), None)
        if ncx_item is not None:
            ncx_path = posixpath.normpath(posixpath.join(opf_dir, ncx_item.get("href"))) \
                if opf_dir else ncx_item.get("href")
            ncx = etree.fromstring(zf.read(ncx_path))
            navmap = ncx.find(f"{{{NCX_NS}}}navMap")
            if navmap is not None:
                order = [int(p.get("playOrder") or 0) for p in navmap] or [0]
                point = etree.SubElement(navmap, f"{{{NCX_NS}}}navPoint")
                point.set("id", f"np-{ident_n}")
                point.set("playOrder", str(max(order) + 1))
                label = etree.SubElement(point, f"{{{NCX_NS}}}navLabel")
                etree.SubElement(label, f"{{{NCX_NS}}}text").text = title
                content = etree.SubElement(point, f"{{{NCX_NS}}}content")
                # NCX hrefs are relative to the NCX, which sits beside the OPF
                content.set("src", href)
                ncx_out = (ncx_path, etree.tostring(ncx, xml_declaration=True,
                                                    encoding="utf-8"))

        nav_item = next((i for i in manifest if "nav" in (i.get("properties") or "")), None)
        if nav_item is not None:
            nav_path = posixpath.normpath(posixpath.join(opf_dir, nav_item.get("href"))) \
                if opf_dir else nav_item.get("href")
            from lxml import html as lhtml

            doc = lhtml.fromstring(zf.read(nav_path))
            ols = doc.xpath("//*[local-name()='nav']//*[local-name()='ol']")
            if ols:
                li = etree.SubElement(ols[0], "li")
                a = etree.SubElement(li, "a")
                a.set("href", posixpath.relpath(full, posixpath.dirname(nav_path)))
                a.text = title
                nav_out = (nav_path, etree.tostring(doc, method="html", encoding="utf-8"))

        page = PAGE.format(title=title, css=css, body=body_html)
        replaced = {opf_path: etree.tostring(opf, xml_declaration=True, encoding="utf-8")}
        # Styling goes into the book's own stylesheet rather than a <style> block
        # in the page: readers that sanitise markup strip inline <style>, and the
        # appendix would then render unstyled in exactly the reader it was made for.
        if extra_css:
            sheet = next((i.get("href") for i in manifest
                          if i.get("media-type") == "text/css"), "")
            if sheet:
                sp = posixpath.normpath(posixpath.join(opf_dir, sheet)) if opf_dir else sheet
                replaced[sp] = zf.read(sp) + b"\n\n" + extra_css.encode("utf-8")
        for pair in (ncx_out, nav_out):
            if pair:
                replaced[pair[0]] = pair[1]

        tmp = out + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
            zo.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                        compress_type=zipfile.ZIP_STORED)
            for info in zf.infolist():
                if info.filename == "mimetype":
                    continue
                data = replaced.get(info.filename)
                zo.writestr(info.filename, data if data is not None
                            else zf.read(info.filename))
            zo.writestr(full, page.encode("utf-8"))
    os.replace(tmp, out)
    print(f"{out}\n  + {full}  ({len(page):,} bytes)  «{title}»"
          f"  ncx={'yes' if ncx_out else 'no'} nav={'yes' if nav_out else 'no'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("epub")
    ap.add_argument("body", help="HTML fragment for the <body> of the new chapter")
    ap.add_argument("--title", required=True)
    ap.add_argument("--id", default="appendix")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--css", default="", help="rules to append to the book's stylesheet")
    a = ap.parse_args()
    with open(a.body, encoding="utf-8") as fh:
        body = fh.read()
    dest = a.out or a.epub
    if dest == a.epub:                       # editing in place: keep a copy first
        shutil.copy2(a.epub, a.epub + ".bak")
    css = open(a.css, encoding="utf-8").read() if a.css else ""
    add(a.epub, body, a.title, dest, a.id, css)
