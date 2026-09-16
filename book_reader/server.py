"""Book Reader — read an epub in the browser, interpret selections with an LLM.

Run:  .venv/bin/python server.py

The reader itself is deliberately plain; the point of it is the interpretation
layer. Everything a lookup needs to be unambiguous — book title, author, chapter
title, printed page, and the sentences either side of the selection — is sent
with every request, because "the Republic" or "censor" only means one thing once
you know which book and chapter you are in.

Settings (book path, LLM endpoints/keys, HOST/PORT) live in config.py, which is
gitignored; copy config_example.py to start. Environment variables of the same
name win over config.py.
"""
import gzip
import hashlib
import json
import os
import re
import sqlite3
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from library import Library

try:
    import config as _cfg
except ImportError:
    import config_example as _cfg

HERE = os.path.dirname(os.path.abspath(__file__))

BOOKS_DIR = os.environ.get("BOOKS_DIR") or getattr(_cfg, "BOOKS_DIR", "") \
    or os.path.dirname(getattr(_cfg, "BOOK_PATH", "") or "") or "~/books"
HOST = os.environ.get("HOST") or getattr(_cfg, "HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT") or getattr(_cfg, "PORT", 8400))

MODELS = getattr(_cfg, "MODELS", None) or [{
    "name": _cfg.LLM_MODEL, "base_url": _cfg.LLM_BASE_URL,
    "api_key": _cfg.LLM_API_KEY, "model": _cfg.LLM_MODEL,
}]
MODELS_BY_NAME = {m["name"]: m for m in MODELS}

DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "reader.db"))

app = FastAPI(title="Book Reader")

# Types worth compressing. Everything else — chiefly the JPEGs, which are already
# compressed — is passed through untouched, so we don't burn CPU for nothing.
COMPRESSIBLE = ("text/", "application/json", "application/javascript", "image/svg+xml")


class GzipExceptStreams:
    """gzip for text and JSON, never for Server-Sent Events.

    Chapter HTML is the bulk of what a reader downloads and compresses about
    four-to-one, which is the largest remaining saving once figures are
    thumbnailed. Starlette's own GZipMiddleware decides on size alone, and would
    therefore also buffer `/api/interpret` — that stream has to arrive token by
    token or the interpretation stops feeling live, so it is excluded by
    content-type here.
    """

    def __init__(self, app, minimum_size: int = 600, level: int = 6):
        self.app, self.minimum_size, self.level = app, minimum_size, level

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        accepted = any(
            k.lower() == b"accept-encoding" and b"gzip" in v.lower()
            for k, v in scope.get("headers", [])
        )
        if not accepted:
            return await self.app(scope, receive, send)

        start, chunks, passthrough = None, [], False

        async def send_wrapper(message):
            nonlocal start, passthrough
            if message["type"] == "http.response.start":
                headers = message["headers"]
                ctype = next((v.decode("latin-1").lower() for k, v in headers
                              if k.lower() == b"content-type"), "")
                encoded = any(k.lower() == b"content-encoding" for k, v in headers)
                if encoded or ctype.startswith("text/event-stream") or \
                        not ctype.startswith(COMPRESSIBLE):
                    passthrough = True
                    return await send(message)
                start = message                     # hold until we know the size
                return
            if passthrough or message["type"] != "http.response.body":
                return await send(message)

            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(chunks)
            if len(body) < self.minimum_size:       # too small to be worth it
                await send(start)
                return await send({"type": "http.response.body", "body": body})
            packed = gzip.compress(body, self.level)
            headers = [(k, v) for k, v in start["headers"]
                       if k.lower() not in (b"content-length", b"content-encoding", b"vary")]
            headers += [(b"content-encoding", b"gzip"),
                        (b"content-length", str(len(packed)).encode("latin-1")),
                        (b"vary", b"Accept-Encoding")]
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": packed})

        await self.app(scope, receive, send_wrapper)


app.add_middleware(GzipExceptStreams)

library = Library(BOOKS_DIR)


def get_book(bid: str):
    """The book, or None — callers turn that into a 404."""
    try:
        return library.book(bid)
    except KeyError:
        return None


# ---------- store ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS progress (
            book    TEXT PRIMARY KEY,
            chapter INTEGER NOT NULL,
            anchor  TEXT,
            page    TEXT,
            updated REAL NOT NULL
        );
        -- every lookup is kept: a book is read over days, and the same proper
        -- nouns and Latin terms keep coming back
        CREATE TABLE IF NOT EXISTS vocab (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            book     TEXT NOT NULL,
            text     TEXT NOT NULL,
            mode     TEXT NOT NULL,
            chapter  INTEGER,
            chapter_title TEXT,
            page     TEXT,
            sentence TEXT,
            answer   TEXT,
            created  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS vocab_book_created ON vocab(book, created DESC);
        -- each book's own proper nouns; see tools/build_glossary.py. A general
        -- dictionary is no help with Amphimachus, and has the wrong Paris.
        CREATE TABLE IF NOT EXISTS glossary (
            book TEXT NOT NULL, term TEXT NOT NULL, key TEXT NOT NULL,
            ipa TEXT, zh TEXT, kind TEXT, note TEXT, n INTEGER,
            PRIMARY KEY (book, term)
        );
        CREATE INDEX IF NOT EXISTS glossary_key ON glossary(book, key);
        """)


init_db()


# ---------- shelf ----------

@app.get("/api/books")
async def api_books():
    """Everything on the shelf. Cheap: skimmed from each OPF and cached."""
    return {"books": library.shelf()}


@app.post("/api/books/rescan")
async def api_rescan():
    library.scan()
    return {"books": library.shelf()}


# ---------- one book ----------

@app.get("/api/books/{bid}")
async def api_book(bid: str):
    book = get_book(bid)
    if book is None:
        return JSONResponse({"error": "no such book"}, status_code=404)
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM glossary WHERE book=?", (bid,)).fetchone()[0]
    return {"id": bid, "glossary": n, **book.outline()}


@app.get("/api/books/{bid}/book.css")
async def api_book_css(bid: str):
    """The book's own stylesheet, filtered and scoped to the article.

    This is what keeps the reader general instead of tuned to one publisher: the
    classes on the markup carry the structural typography, and the only thing
    that reliably knows what they mean is the book itself.
    """
    book = get_book(bid)
    if book is None:
        return Response("", media_type="text/css", status_code=404)
    return Response(book.stylesheet(), media_type="text/css",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/books/{bid}/passage")
async def api_passage(bid: str, page_from: int = -1, page_to: int = -1, chapter: int = -1):
    """How big a passage is, without sending it.

    The reader shows the size before you commit to a question; the text itself
    never crosses the network, since the server reads it from the epub.
    """
    book = get_book(bid)
    if book is None:
        return JSONResponse({"error": "no such book"}, status_code=404)
    if page_from >= 0:
        pages = book.pages_index()
        if not pages:
            return {"chars": 0, "label": "本书没有印刷页码", "pages": 0}
        lo, hi = max(0, page_from), min(len(pages) - 1, page_to)
        text = book.page_text(lo, hi)
        return {"chars": len(text), "pages": hi - lo + 1,
                "label": f"p. {pages[lo]['page']} – {pages[hi]['page']}（共 {hi - lo + 1} 页）"}
    if chapter >= 0:
        try:
            text = book.chapter_text(chapter)
        except IndexError:
            return JSONResponse({"error": "no such chapter"}, status_code=404)
        return {"chars": len(text), "pages": 0,
                "label": (book.chapter(chapter).title or f"第 {chapter + 1} 节") + "（整章）"}
    return JSONResponse({"error": "need page_from/page_to or chapter"}, status_code=400)


@app.get("/api/books/{bid}/chapter/{idx}")
async def api_chapter(bid: str, idx: int):
    book = get_book(bid)
    if book is None:
        return JSONResponse({"error": "no such book"}, status_code=404)
    try:
        ch = book.chapter(idx)
    except IndexError:
        return JSONResponse({"error": "no such chapter"}, status_code=404)
    return {
        "idx": ch.idx,
        "title": ch.title,
        "html": ch.html,
        "pages": ch.pages,
        "body_class": ch.body_class,
        "prev": ch.idx - 1 if ch.idx > 0 else None,
        "next": ch.idx + 1 if ch.idx + 1 < len(book.spine) else None,
    }


MEDIA_TYPES = {
    ".jpeg": "image/jpeg", ".jpg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".css": "text/css",
}
THUMB_DIR = os.path.join(HERE, "cache", "thumbs")
os.makedirs(THUMB_DIR, exist_ok=True)


def _thumb(book, path: str, width: int) -> str | None:
    """Downscale an image from the zip to `width` px, cached on disk.

    Real resizing, not a CSS shrink: a figure in this book is 15–70 KB, a 96 px
    thumbnail of it is under 3 KB, and the full image is only fetched if the
    reader actually opens it.
    """
    key = hashlib.sha1(f"{book.path}|{path}|{width}".encode("utf-8")).hexdigest()[:20]
    out = os.path.join(THUMB_DIR, f"{key}.jpg")
    if os.path.exists(out):
        return out
    try:
        from PIL import Image

        with book.open(path) as fh, Image.open(fh) as im:
            im = im.convert("RGB")
            im.thumbnail((width, width * 8), Image.LANCZOS)
            im.save(out, "JPEG", quality=78, optimize=True)
        return out
    except Exception:  # noqa: BLE001 — any decode failure: serve the original
        return None


@app.get("/api/books/{bid}/res/{path:path}")
async def api_res(bid: str, path: str, w: int = 0):
    """Serve a resource straight out of the epub zip; `?w=` gives a thumbnail."""
    book = get_book(bid)
    if book is None or not book.has(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    media = MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    cache = {"Cache-Control": "public, max-age=86400"}

    if w and media.startswith("image/") and media != "image/svg+xml":
        thumb = _thumb(book, path, max(16, min(w, 2000)))
        if thumb:
            return FileResponse(thumb, media_type="image/jpeg", headers=cache)
    return StreamingResponse(book.open(path), media_type=media, headers=cache)


# ---------- reading position ----------

class Progress(BaseModel):
    book: str
    chapter: int
    anchor: str = ""
    page: str = ""


@app.get("/api/progress")
async def get_progress(book: str = ""):
    """One book's position, or every book's — the shelf shows where you are."""
    with db() as conn:
        if book:
            row = conn.execute("SELECT * FROM progress WHERE book=?", (book,)).fetchone()
            return dict(row) if row else {}
        rows = conn.execute("SELECT * FROM progress").fetchall()
    return {"progress": {r["book"]: dict(r) for r in rows}}


@app.put("/api/progress")
async def put_progress(p: Progress):
    with db() as conn:
        conn.execute(
            "INSERT INTO progress(book, chapter, anchor, page, updated) VALUES(?,?,?,?,?) "
            "ON CONFLICT(book) DO UPDATE SET chapter=excluded.chapter, "
            "anchor=excluded.anchor, page=excluded.page, updated=excluded.updated",
            (p.book, p.chapter, p.anchor, p.page, time.time()),
        )
    return {"ok": True}


# ---------- vocabulary book ----------

@app.get("/api/vocab")
async def list_vocab(limit: int = 500, words_only: bool = False, book: str = ""):
    sql = "SELECT * FROM vocab WHERE 1=1"
    args: list = []
    if book:
        sql += " AND book=?"
        args.append(book)
    if words_only:
        sql += " AND length(text) <= 40"
    sql += " ORDER BY created DESC LIMIT ?"
    args.append(max(1, min(limit, 2000)))
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.delete("/api/vocab/{item_id}")
async def delete_vocab(item_id: int):
    with db() as conn:
        conn.execute("DELETE FROM vocab WHERE id=?", (item_id,))
    return {"ok": True}


# ---------- the book's own glossary ----------

def _variants(word: str):
    """Spellings to try, in order of confidence."""
    w = word.strip().strip("\u2018\u2019'\".,;:!?()[]")
    seen, out = set(), []
    for cand in (w, re.sub(r"[\u2019']s$", "", w),
                 w[:-1] if w.endswith("s") else "",
                 w[:-2] if w.endswith("es") else "",
                 w[:-3] + "y" if w.endswith("ies") else "",
                 w + "s"):
        c = cand.strip().lower()
        if c and len(c) > 1 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


@app.get("/api/books/{bid}/dict")
async def api_dict(bid: str, word: str):
    """Look a proper noun up in this book's glossary.

    Instant and offline — the point of the button beside 解读 is that a name you
    half-remember costs nothing to check, where an LLM round-trip would make you
    weigh whether it is worth interrupting the page for.
    """
    with db() as conn:
        for key in _variants(word):
            row = conn.execute(
                "SELECT term, ipa, zh, kind, note, n FROM glossary WHERE book=? AND key=?",
                (bid, key)).fetchone()
            if row:
                return {"found": True, **dict(row)}
        # a selected phrase may contain a name: "son of Peleus"
        for token in re.findall(r"[A-Za-z\u00c0-\u024f'\u2019-]{3,}", word)[:6]:
            for key in _variants(token):
                row = conn.execute(
                    "SELECT term, ipa, zh, kind, note, n FROM glossary WHERE book=? AND key=?",
                    (bid, key)).fetchone()
                if row:
                    return {"found": True, "via": token, **dict(row)}
        total = conn.execute("SELECT COUNT(*) FROM glossary WHERE book=?", (bid,)).fetchone()[0]
    return {"found": False, "word": word, "glossary": total}


# ---------- interpretation ----------

class Msg(BaseModel):
    role: str
    content: str


class InterpretReq(BaseModel):
    text: str = ""
    # a passage is named, not uploaded: indices into the book's printed-page
    # index, or a chapter for books that record no pages
    book_id: str = ""            # which book the page indices refer to
    page_from: int = -1
    page_to: int = -1
    passage_chapter: int = -1
    context: str = ""
    mode: str = "interpret"      # interpret | grammar | background | chapter
    model: str = ""              # display name from /api/models; "" = default
    book: str = ""               # book title
    author: str = ""
    chapter: int = -1
    chapter_title: str = ""
    page: str = ""
    history: list[Msg] = []


BOOK_ROLE = (
    "你是嵌在英文原版书阅读器里的『阅读助手』。用户正在读一本英文书，选中了一个词或一段话，想快速看懂。"
    "请求里会给你书名、作者、章标题和印刷页码——**先用它们定位语境再作答**：一个平常的英文词，"
    "在具体某本书里往往是该领域的术语，意思和日常义相差很远；先看这是哪本书的哪一章，再决定取哪个义项。"
)

# INTERPRET_PROMPT and SINGLE_WORD_RULE are strictly disjoint: the word case is
# detected by regex below, so the two are always concatenated, and anything said
# in both is said twice. They used to overlap almost entirely — and contradict,
# one saying IPA could be skipped for easy words while the other demanded it
# always — which cost tokens and gave the model a choice it should not have had.
INTERPRET_PROMPT = (
    BOOK_ROLE + "用中文讲，**极简扼要，像书页旁批**：只说最关键的，通常 2～4 行一眼扫完。"
    "不堆砌、不凑格式、不罗列例句近义词。\n\n"
    "**难度自适应**：常见基础词一行意思就够；篇幅留给真正生僻、专业、易误解或有典故背景的词。"
    "标准是『读原版书的成年学习者会不会卡住』。\n\n"
    "- **短语/习语/专名**：一两句说清整体含义和中文对应。习语点明字面vs实际；"
    "专名（人名/地名/官职/制度/术语）说清它在**这本书的语境里**指什么。\n"
    "- **长句片段**：一两句用大白话说清这里在讲什么。仅当有生词、复杂语法或背景造成障碍时，"
    "才补一句点破难点；没难点就说『直译即可』。\n\n"
    "**若选中的不是整句，必须另起一行写『**本句**：…』**，用中文意译说清它所在那句话的意思（说人话，别逐字直译）。\n\n"
    "surrounding context 只用来消歧和定位本句，不要复述整段。可少量加粗，别做成长列表。"
    "追问时再展开；首次一律从简。选中的若是中文，反过来给地道英文表达。"
)

# Fires only when the selection is one word; it is then the sole authority on
# what a word answer contains.
SINGLE_WORD_RULE = (
    "【选中的是单个单词——四项都要给，即使是 house、the 这类最常见的词】\n"
    "① **音标**：IPA，英式美式均可。\n"
    "② **此处意思**：结合本句语境的确切含义 + 地道中文翻译。\n"
    "③ **词源**：词根/词缀、来自哪门语言、本义如何演变到今义。\n"
    "④ **本句**：这个词所在那句话的中文大意。\n"
    "常见词把①②③④各写一行即可，不必展开。"
)

GRAMMAR_PROMPT = (
    "你是英语语法老师。用户在读英文原版书，选中了一句话或一个片段，想弄懂它的**语法结构和整体意思**。"
    "用中文讲，精炼有条理、别啰嗦，重点在讲清结构：\n"
    "① **整句意思**：先给一句流畅的中文译文。\n"
    "② **结构拆解**：主干（主语/谓语/宾语）是什么；有哪些从句、非谓语、插入语、倒装、省略，分别修饰或充当什么；"
    "时态、语态、指代、连接词的作用；把造成理解困难的语法点逐一点破。\n"
    "③ **关键词组**：挑出值得注意的固定搭配 / 短语动词 / 习惯用法（有才写）。\n"
    "分析对象以 surrounding context（完整句子）为准；若选中的只是片段，就针对它所在的那**整句**分析。"
    "用少量 Markdown 加粗/短列表组织，但别写成长篇。"
)

# The mode a reference book needs and a news reader does not: the sentence is
# grammatically easy but assumes background the reader hasn't got.
BACKGROUND_PROMPT = (
    BOOK_ROLE + "这次用户要的不是字面意思，而是**背景**：这段话预设了哪些他可能不知道的知识。用中文讲，控制在 4～8 行。\n"
    "挑真正构成理解障碍的讲，通常是这几类（有才写，不要硬凑齐）：\n"
    "- **人物/地点/事件**：是谁、在哪、什么时候、和这本书的主线什么关系。\n"
    "- **制度/官职/术语**：这个词背后的制度或概念是怎么运作的。\n"
    "- **典故/引文**：出自哪里，原文语境是什么。\n"
    "- **为什么这句重要**：作者摆这句话在这里，是想推进什么论点。\n"
    "**只讲这段话真正需要的背景**，不要写成百科条目，也不要复述原文。"
    "涉及年代就给出来（如 BC 133、前 44 年），读者需要时间坐标。"
)

# "ask" carries a run of pages the reader chose the extent of, and their own
# question about it. Deliberately not a summary mode: it answers what was asked
# rather than standing in for the reading.
ASK_PROMPT = (
    BOOK_ROLE + "用户给了他正在读的**若干页原文**，以及他自己的问题。用中文回答。\n"
    "- **只回答他问的**，不要顺带概述这几页，不要复述原文。\n"
    "- 答案必须**落在给定原文里**。要用到原文之外的背景才能讲清时，说明哪部分是补充的。\n"
    "- 原文回答不了这个问题（比如答案在后面的章节），就直接说，别猜。\n"
    "- 需要指位置时用印刷页码。篇幅按问题的需要来：能一句说清就一句，别拉长。\n"
    "可用少量 Markdown 加粗和短列表。"
)


# Any Latin letter, accents included: café, naïve, Ångström, état, œuvre.
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")


def _is_single_word(text: str) -> bool:
    """Whether a selection is one word, and so gets the strict four-part answer.

    Defined by what a word is not — it has no space in it — rather than by
    enumerating the characters one may contain. The enumerating version
    (`[A-Za-z][A-Za-z'’.\\-]*`) silently excluded every accented word, so `café`
    and `émigrés` fell through to the phrase rules and lost their IPA and
    etymology. Those are exactly the words a reader stops on. Digits are allowed
    through for the same reason (`COVID-19`, `5G`); a Latin letter is still
    required, so a Chinese selection keeps the reverse treatment instead.
    """
    return (bool(text) and len(text) <= 40
            and not re.search(r"\s", text)
            and _LATIN.search(text) is not None)


def _build_messages(req: InterpretReq):
    text = req.text.strip()
    if req.mode == "grammar":
        system, tail = GRAMMAR_PROMPT, "\n分析这一句的语法结构和整体意思。"
    elif req.mode == "background":
        system, tail = BACKGROUND_PROMPT, "\n讲清读懂这段话所需要的背景。"
    elif req.mode == "ask":
        system, tail = ASK_PROMPT, ""
    else:
        system = INTERPRET_PROMPT
        if _is_single_word(text):
            system += "\n\n" + SINGLE_WORD_RULE
        tail = "\n解读选中的内容。"

    where = []
    if req.book:
        where.append(f"Book: {req.book}" + (f" — {req.author}" if req.author else ""))
    if req.chapter_title:
        where.append(f"Chapter: {req.chapter_title}")
    if req.page:
        where.append(f"Printed page: {req.page}")

    user = f'Selected text:\n"""\n{text}\n"""\n'
    if where:
        user += "\n" + "\n".join(where) + "\n"
    if req.context:
        ctx = req.context.strip()
        if len(ctx) > 2000:
            ctx = ctx[:2000] + "…"
        user += f'\nSurrounding context:\n"""\n{ctx}\n"""\n'
    user += tail

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for m in req.history:
        if m.role in ("user", "assistant") and m.content.strip():
            messages.append({"role": m.role, "content": m.content})
    return messages


def _save_lookup(req: InterpretReq, answer: str) -> None:
    """Record a lookup so it can be revisited later.

    Only first turns are saved: a follow-up belongs to the entry it came from,
    not beside it.
    """
    if req.history or not answer.strip():
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO vocab(book, text, mode, chapter, chapter_title, page, "
            "sentence, answer, created) VALUES(?,?,?,?,?,?,?,?,?)",
            (req.book_id, req.text.strip(), req.mode, req.chapter, req.chapter_title,
             req.page, req.context.strip()[:2000], answer, time.time()),
        )


@app.get("/api/models")
async def list_models():
    return {"models": [m["name"] for m in MODELS], "default": MODELS[0]["name"]}


def _passage(req: InterpretReq) -> str:
    """The passage a request refers to, read from the book here.

    A question about twenty pages therefore uploads two integers rather than
    35 KB — and re-uploads nothing at all on each follow-up.
    """
    if req.page_from < 0 and req.passage_chapter < 0:
        return req.text
    book = get_book(req.book_id)
    if book is None:
        return req.text
    if req.page_from >= 0:
        return book.page_text(req.page_from, req.page_to)
    return book.chapter_text(req.passage_chapter)   # books with no printed pages


@app.post("/api/interpret")
async def interpret(req: InterpretReq):
    req.text = _passage(req)
    if not req.text.strip():
        return JSONResponse({"error": "empty selection"}, status_code=400)

    sel = MODELS_BY_NAME.get(req.model) or MODELS[0]
    base = sel["base_url"].rstrip("/")
    payload = {
        "model": sel["model"],
        "messages": _build_messages(req),
        "stream": True,
        # reasoning models spend max_tokens on hidden thinking, and only
        # delta.content is forwarded below — leave enough room that the answer
        # still gets written after the thinking, and that a long grammar
        # breakdown or chapter guide is not cut off by finish=length
        "max_tokens": 4000,
    }
    # Newer models are starting to reject `temperature` outright — Bedrock's
    # claude-opus-4-7 answers "`temperature` is deprecated for this model" — so it
    # is per-model and omitted entirely when set to None, which no amount of
    # overriding in `extra` could achieve.
    temperature = sel.get("temperature", 0.3)
    if temperature is not None:
        payload["temperature"] = temperature
    payload.update(sel.get("extra") or {})
    headers = {"Authorization": f"Bearer {sel['api_key']}", "Content-Type": "application/json"}

    async def gen():
        acc = ""
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                async with client.stream("POST", f"{base}/chat/completions",
                                         json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", "replace")[:400]
                        yield _sse({"error": f"LLM {resp.status_code}: {body}"})
                        return
                    finish = None
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            choice = json.loads(data)["choices"][0]
                            # record finish_reason first: the final chunk often
                            # carries no delta and would be skipped below
                            finish = choice.get("finish_reason") or finish
                            delta = choice["delta"].get("content")
                            if delta:
                                acc += delta
                                yield _sse({"delta": delta})
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                    if not acc:
                        yield _sse({"error": (
                            f"模型没有输出正文 (finish_reason={finish})。"
                            "多半是思考过程把 max_tokens 用完了，换个模型再试。"
                        )})
        except Exception as e:  # noqa: BLE001
            yield _sse({"error": str(e)})
        if acc:
            try:
                _save_lookup(req, acc)
            except sqlite3.Error:
                pass                              # saving is best-effort
        yield _sse({"done": True})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ---------- pages ----------

PAGES = os.path.join(HERE, "pages")


def _asset_version() -> str:
    """A token that changes whenever anything under static/ does.

    Without it a browser keeps a cached script indefinitely — StaticFiles sends
    no Cache-Control, so freshness is heuristic, and a phone will happily run
    yesterday's JavaScript against today's HTML. That failure is silent: the page
    works, one feature is just missing.
    """
    latest = 0
    for root, _, files in os.walk(os.path.join(HERE, "static")):
        for f in files:
            latest = max(latest, int(os.stat(os.path.join(root, f)).st_mtime))
    return str(latest)


def _page(name: str, request: Request) -> Response:
    """Serve a page with revalidation rather than no-store.

    The pages carry their CSS and script inline, so the reader HTML is ~11 KB
    gzipped and was being refetched on every single open. An ETag off the file's
    mtime and size turns a repeat open into a 304 of a couple of hundred bytes,
    while still picking up edits immediately.
    """
    path = os.path.join(PAGES, name)
    stat = os.stat(path)
    version = _asset_version()
    # the page's identity includes its assets': a new script must invalidate the
    # HTML that loads it, or the two drift apart
    etag = f'W/"{int(stat.st_mtime)}-{stat.st_size}-{version}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    with open(path, encoding="utf-8") as f:
        body = f.read().replace("{{V}}", version)
    return HTMLResponse(body, headers={"ETag": etag, "Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
async def page_library(request: Request):
    return _page("library.html", request)


@app.get("/read", response_class=HTMLResponse)
async def page_reader(request: Request):
    return _page("reader.html", request)


@app.get("/vocab", response_class=HTMLResponse)
async def page_vocab(request: Request):
    return _page("vocab.html", request)


@app.get("/favicon.ico")
async def favicon():
    """The app's own mark, not a book's cover.

    Browsers fetch this unprompted on every page load. It used to serve a
    thumbnail of whichever book happened to be first on the shelf — arbitrary,
    and 2 KB each time; a 3 KB file cached for a year is both more honest and
    cheaper.
    """
    return FileResponse(os.path.join(HERE, "static", "favicon.ico"),
                        media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=31536000"})


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


if __name__ == "__main__":
    import uvicorn

    print(f"Book Reader → http://{HOST}:{PORT}/")
    print(f"  {len(library.shelf())} books in {library.root}")
    for meta in library.shelf():
        print(f"    {meta['title'][:52]:<54}{meta['chapters']:>3} ch")
    uvicorn.run(app, host=HOST, port=PORT)
