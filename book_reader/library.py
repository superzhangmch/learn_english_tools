"""A directory of epubs, opened on demand.

Listing a shelf and reading a book want very different things. Listing needs a
title and a cover for everything present and needs it now; reading needs one
book parsed properly. So the two are separated: metadata comes from a cached
skim of the OPF, and the full :class:`Epub` is built only for the book actually
being read, then kept for a few books at a time.

The id of a book is derived from its filename, so reading positions and saved
lookups survive a restart, a re-scan, and the book being moved.
"""
import hashlib
import json
import os
import posixpath
import zipfile
from collections import OrderedDict

from lxml import etree

from epub import Epub, CONTAINER_NS, DC_NS, OPF_NS

OPEN_BOOKS = 4          # full parses kept in memory


def book_id(path: str) -> str:
    return hashlib.sha1(os.path.basename(path).encode("utf-8")).hexdigest()[:12]


def _skim(path: str) -> dict:
    """Title, author and cover from the OPF alone — no chapters parsed."""
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        opf_path = ""
        try:
            root = etree.fromstring(zf.read("META-INF/container.xml"))
            el = root.find(f".//{{{CONTAINER_NS}}}rootfile")
            opf_path = el.get("full-path") if el is not None else ""
        except (KeyError, etree.XMLSyntaxError):
            pass
        if opf_path not in names:
            opf_path = next((n for n in names if n.endswith(".opf")), "")
        if not opf_path:
            raise ValueError("no OPF")

        opf = etree.fromstring(zf.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)

        def text(tag):
            el = opf.find(f".//{{{DC_NS}}}{tag}")
            return (el.text or "").strip() if el is not None and el.text else ""

        def resolve(href):
            href = (href or "").split("#")[0]
            return posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href

        manifest = {i.get("id"): i for i in
                    opf.findall(f".//{{{OPF_NS}}}manifest/{{{OPF_NS}}}item")}
        cover = ""
        for item in manifest.values():
            if "cover-image" in (item.get("properties") or ""):
                cover = resolve(item.get("href"))
                break
        if not cover:
            for m in opf.findall(f".//{{{OPF_NS}}}meta"):
                if m.get("name") == "cover" and m.get("content") in manifest:
                    cover = resolve(manifest[m.get("content")].get("href"))
                    break
        if not cover:
            cover = next((n for n in ("cover.jpeg", "cover.jpg", "cover.png")
                          if resolve(n) in names), "")
            cover = resolve(cover) if cover else ""

        creators = [(e.text or "").strip()
                    for e in opf.findall(f".//{{{DC_NS}}}creator")
                    if e.text and e.text.strip()]
        spine = opf.findall(f".//{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref")
        return {
            "title": text("title") or os.path.splitext(os.path.basename(path))[0],
            "author": ", ".join(creators),
            "language": text("language"),
            "date": text("date")[:10],
            "cover_path": cover if cover in names else "",
            "chapters": len(spine),
        }


class Library:
    """The shelf: what is on it, and one or two books off it at a time."""

    def __init__(self, root: str, cache_path: str = ""):
        self.root = os.path.expanduser(root)
        self.cache_path = cache_path or os.path.join(self.root, ".library.json")
        self._meta: dict[str, dict] = {}
        self._open: "OrderedDict[str, Epub]" = OrderedDict()
        self._disk = self._load_cache()
        self.scan()

    # ---------- shelf ----------

    def _load_cache(self) -> dict:
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self) -> None:
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._disk, f, ensure_ascii=False)
        except OSError:
            pass                                    # caching is best-effort

    def scan(self) -> None:
        """Re-read the directory. Skimming is cached against size and mtime."""
        meta, dirty = {}, False
        for entry in sorted(os.scandir(self.root), key=lambda e: e.name.lower()):
            if not entry.is_file() or not entry.name.lower().endswith(".epub"):
                continue
            stat = entry.stat()
            key = f"{entry.name}|{stat.st_size}|{int(stat.st_mtime)}"
            info = self._disk.get(key)
            if info is None:
                try:
                    info = _skim(entry.path)
                except Exception:                   # noqa: BLE001 — skip bad files
                    continue
                self._disk[key] = info
                dirty = True
            bid = book_id(entry.path)
            meta[bid] = {
                **info, "id": bid, "path": entry.path,
                "file": entry.name, "bytes": stat.st_size,
                "cover": f"/api/books/{bid}/res/{info['cover_path']}"
                         if info["cover_path"] else "",
            }
        self._meta = meta
        if dirty:
            # keep only entries for files still present
            live = {f"{m['file']}|{m['bytes']}|{int(os.stat(m['path']).st_mtime)}"
                    for m in meta.values()}
            self._disk = {k: v for k, v in self._disk.items() if k in live}
            self._save_cache()

    def shelf(self) -> list[dict]:
        return [{k: v for k, v in m.items() if k not in ("path", "cover_path")}
                for m in self._meta.values()]

    def has(self, bid: str) -> bool:
        return bid in self._meta

    def meta(self, bid: str) -> dict:
        return self._meta[bid]

    # ---------- books ----------

    def book(self, bid: str) -> Epub:
        """The parsed book, kept open for a few books at a time."""
        if bid not in self._meta:
            raise KeyError(bid)
        if bid in self._open:
            self._open.move_to_end(bid)
            return self._open[bid]
        book = Epub(self._meta[bid]["path"], res_prefix=f"/api/books/{bid}/res")
        self._open[bid] = book
        while len(self._open) > OPEN_BOOKS:
            _, evicted = self._open.popitem(last=False)
            try:
                evicted.zf.close()
            except Exception:                       # noqa: BLE001
                pass
        return book
