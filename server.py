"""News Reader — fetch a news URL, show it in reading mode, interpret selections with an LLM.

Run:  .venv/bin/python server.py

Settings (LLM endpoint/model/key, HOST, PORT, weather coords) live in config.py
(gitignored) — copy config_example.py to config.py and fill in. Environment
variables of the same names override config.py.
"""
import hashlib
import json
import os
import re
import time

import httpx
import trafilatura
import board  # dashboard (news / weather / radar) logic, reused as a module
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# settings come from config.py (gitignored); env vars override; example is the fallback
try:
    import config as _cfg
except ImportError:
    import config_example as _cfg

LLM_BASE_URL = (os.environ.get("LLM_BASE_URL") or _cfg.LLM_BASE_URL).rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL") or _cfg.LLM_MODEL
LLM_API_KEY = os.environ.get("LLM_API_KEY") or _cfg.LLM_API_KEY

# selectable interpret models (keys stay server-side; UI only sees the names)
MODELS = getattr(_cfg, "MODELS", None) or [
    {"name": LLM_MODEL, "base_url": LLM_BASE_URL, "api_key": LLM_API_KEY, "model": LLM_MODEL}
]
MODELS_BY_NAME = {m["name"]: m for m in MODELS}
HOST = os.environ.get("HOST") or getattr(_cfg, "HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT") or getattr(_cfg, "PORT", 8000))

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}

app = FastAPI(title="News Reader")
# CORS kept for flexibility (dashboard + reader are now same-origin anyway)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

HERE = os.path.dirname(os.path.abspath(__file__))

# persistent cache of fetched+extracted articles (keyed by URL)
CACHE_DIR = os.environ.get("CACHE_DIR", os.path.join(HERE, "cache"))
CACHE_TTL = int(os.environ.get("CACHE_TTL", "0"))  # seconds; 0 = never expire
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha256(url.encode("utf-8")).hexdigest()[:20] + ".json")


def normalize_content(html: str) -> str:
    """Map trafilatura's simplified tags to real HTML the browser can render."""
    # drop the <html>/<body> wrapper
    html = re.sub(r"</?(?:html|body)>", "", html)
    # <graphic src alt/> -> <img ...>
    html = html.replace("<graphic ", "<img ").replace("</graphic>", "")
    # lists
    html = (html.replace("<list", "<ul").replace("</list>", "</ul>")
                .replace("<item>", "<li>").replace("</item>", "</li>"))
    # line breaks
    html = re.sub(r"<lb\s*/?>", "<br>", html)
    # drop the FIRST heading (it duplicates the title shown in the meta block)
    html = re.sub(r"<h1\b[^>]*>.*?</h1>", "", html, count=1, flags=re.DOTALL)
    return html.strip()


# ---------- extraction ----------

@app.get("/api/extract")
async def extract(url: str, refresh: bool = False):
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "URL must start with http:// or https://"}, status_code=400)

    path = _cache_path(url)
    if not refresh and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            if CACHE_TTL <= 0 or (time.time() - cached.get("fetched_at", 0) < CACHE_TTL):
                cached["cached"] = True
                cached["id"] = os.path.basename(path)[:-5]
                return cached
        except (json.JSONDecodeError, OSError):
            pass  # corrupt/unreadable cache -> re-fetch

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=25.0, headers=BROWSER_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"Failed to fetch page: {e}"}, status_code=502)

    content_html = trafilatura.extract(
        html,
        output_format="html",
        include_formatting=True,
        include_images=True,
        include_links=False,
        favor_recall=True,
        url=url,
    )
    if not content_html:
        return JSONResponse({"error": "Could not extract readable content from this page."}, status_code=422)

    meta = trafilatura.extract_metadata(html, default_url=url)
    result = {
        "url": url,
        "title": (meta.title if meta else None) or "Untitled",
        "author": meta.author if meta else None,
        "date": meta.date if meta else None,
        "sitename": meta.sitename if meta else None,
        "content_html": normalize_content(content_html),
        "fetched_at": time.time(),
        "cached": False,
        "id": os.path.basename(path)[:-5],
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except OSError:
        pass  # caching is best-effort
    return result


# ---------- reading list (backed by the cache) ----------

@app.get("/api/list")
async def list_articles():
    items = []
    for name in os.listdir(CACHE_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(CACHE_DIR, name), encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        items.append({
            "id": name[:-5],  # filename without .json
            "url": d.get("url"),
            "title": d.get("title") or "Untitled",
            "sitename": d.get("sitename"),
            "date": d.get("date"),            # news publish time
            "fetched_at": d.get("fetched_at"),  # added time
        })
    items.sort(key=lambda x: x.get("fetched_at") or 0, reverse=True)
    return {"items": items}


@app.post("/api/delete")
async def delete_article(payload: dict):
    aid = str(payload.get("id", ""))
    if not re.fullmatch(r"[0-9a-f]{20}", aid):
        return JSONResponse({"error": "bad id"}, status_code=400)
    path = os.path.join(CACHE_DIR, aid + ".json")
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True}


# ---------- interpretation ----------

class Msg(BaseModel):
    role: str
    content: str


class InterpretReq(BaseModel):
    text: str
    context: str = ""
    title: str = ""
    mode: str = "interpret"   # "interpret" (default) or "grammar"
    model: str = ""           # selected model NAME (from /api/models); "" = default
    history: list[Msg] = []  # follow-up turns after the initial interpretation


SYSTEM_PROMPT = (
    "你是嵌在英文新闻阅读器里的『语言学习助手』。用户正在读 BBC / CNN 英文新闻，选中了一个词或一段话，"
    "想快速看懂。用中文讲，**极简扼要，像报纸旁批**——用户在读新闻，不要长篇大论影响阅读。\n\n"
    "总原则：只说最关键的，通常 2～4 行、一眼扫完即可。不堆砌、不为凑格式硬写、不罗列一堆例句/近义词/搭配。\n\n"
    "**难度自适应，是重点**：先判断这个词/短语有多难。**中小学生都认识的常见词、基础词**"
    "（如 house、important、government、review、decide 这类），别啰嗦——一行中文意思就够，不用展开。"
    "（注意：单个词的**词源例外，永远要给**，见下面『单个词』一档。）"
    "把篇幅只留给**真正生僻、专业、易误解或有文化/典故背景**的词。判断标准是『读新闻的成年学习者会不会卡住』，"
    "不卡就一笔带过。\n\n"
    "按选中内容分三档（在上面『难度自适应』的前提下）：\n"
    "- **单个词**：**必须给词源**——不论这个词多常见、多简单，都要用一句话讲清它的来源"
    "（词根/词缀、来自哪门语言、原义如何演变成今义），这是用户明确要求的，任何情况下都不能省略。"
    "另外给本词在此处的意思；难词再加读音(IPA)，常见词可省略读音。即便是 house、important 这类基础词，"
    "也要『一行意思 + 一句词源』。\n"
    "- **短语/习语/专名**：一两句说清整体含义和中文对应；习语点明字面vs实际，专名说明指什么。\n"
    "- **长句片段**：一两句用大白话说清这里在讲什么；**仅当**有生词、复杂语法或风俗/文化/典故/背景造成理解障碍时，"
    "才补一句点破那个难点。没难点就说『直译即可』，不要硬凑。\n\n"
    "**本句意思，必做**：只要选中的不是一整句（即选中的是单个词、短语、专名），"
    "在解释完这个词/短语之后，**必须再用一行说清它所在的那句话是什么意思**——"
    "看 surrounding context 找到包含选中词的那一句，用中文意译说清这句在讲什么（不必逐字直译，说人话）。"
    "单起一行，写成『**本句**：…』。这是用户明确要求的，不能省略。"
    "若选中的本身就是整句或长片段，则跳过这一条（上面『长句片段』一档已经在做这件事）。\n\n"
    "surrounding context 用来消歧、并据以给出上面的『本句』，但不要复述整篇文章。"
    "可用少量 Markdown 加粗，但别做成长列表。"
    "用户若追问，再展开细讲；首次解读一律从简。选中的若是中文，则反过来给地道英文表达。"
)

# rule-triggered when the selection is a SINGLE word — force the full format
SINGLE_WORD_RULE = (
    "【本次选中的是单个单词——硬性格式要求，四项一个都不能少，即使是最常见的词】\n"
    "① **音标**：给 IPA（英式或美式均可）。\n"
    "② **此处意思**：结合本句语境的确切含义 + 地道中文翻译。\n"
    "③ **词源**：词根/词缀、来自哪门语言、本义如何演变到今义。\n"
    "④ **本句**：这个词所在那句话的中文大意。\n"
    "即使是 house、important、the 这类词，①②③④ 也全部要给，绝不省略音标或词源。"
)

GRAMMAR_PROMPT = (
    "你是英语语法老师。用户在读英文新闻，选中了一句话或一个片段，想弄懂它的**语法结构和整体意思**。"
    "用中文讲，精炼有条理、别啰嗦，重点在讲清结构：\n"
    "① **整句意思**：先给一句流畅的中文译文。\n"
    "② **结构拆解**：主干（主语/谓语/宾语）是什么；有哪些从句、非谓语、插入语、倒装、省略，分别修饰或充当什么；"
    "时态、语态、指代、连接词的作用；把造成理解困难的语法点逐一点破。\n"
    "③ **关键词组**：挑出值得注意的固定搭配 / 短语动词 / 习惯用法（有才写）。\n"
    "分析对象以 surrounding context（完整句子）为准；若选中的只是片段，就针对它所在的那**整句**分析。"
    "用少量 Markdown 加粗/短列表组织，但别写成长篇。"
)


def _build_messages(req: InterpretReq):
    text = req.text.strip()
    if req.mode == "grammar":
        system = GRAMMAR_PROMPT
        tail = "\nAnalyze the grammar and overall meaning of the sentence (the surrounding context is the sentence)."
    else:
        system = SYSTEM_PROMPT
        if re.fullmatch(r"[A-Za-z][A-Za-z'’.\-]*", text):   # single English word → strict format
            system = SYSTEM_PROMPT + "\n\n" + SINGLE_WORD_RULE
        tail = "\nInterpret the selected text."
    user = f"Selected text:\n\"\"\"\n{text}\n\"\"\"\n"
    if req.title:
        user += f"\nArticle title: {req.title}\n"
    if req.context:
        ctx = req.context.strip()
        if len(ctx) > 1500:
            ctx = ctx[:1500] + "…"
        user += f"\nSurrounding context:\n\"\"\"\n{ctx}\n\"\"\"\n"
    user += tail
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # follow-up turns (assistant reply, then user question, ...)
    for m in req.history:
        if m.role in ("user", "assistant") and m.content.strip():
            messages.append({"role": m.role, "content": m.content})
    return messages


@app.get("/api/models")
async def list_models():
    return {"models": [m["name"] for m in MODELS], "default": MODELS[0]["name"]}


@app.post("/api/interpret")
async def interpret(req: InterpretReq):
    if not req.text.strip():
        return JSONResponse({"error": "empty selection"}, status_code=400)

    sel = MODELS_BY_NAME.get(req.model) or MODELS[0]   # resolve name -> endpoint/key
    base = sel["base_url"].rstrip("/")
    payload = {
        "model": sel["model"],
        "messages": _build_messages(req),
        "stream": True,
        "temperature": 0.3,
        "max_tokens": 800,
    }
    payload.update(sel.get("extra") or {})   # per-model extras (e.g. GLM thinking:disabled)
    headers = {"Authorization": f"Bearer {sel['api_key']}", "Content-Type": "application/json"}

    async def gen():
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST", f"{base}/chat/completions", json=payload, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", "replace")[:400]
                        yield _sse({"error": f"LLM {resp.status_code}: {body}"})
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"].get("content")
                            if delta:
                                yield _sse({"delta": delta})
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except Exception as e:  # noqa: BLE001
            yield _sse({"error": str(e)})
        yield _sse({"done": True})

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ---------- pages ----------
PAGES = os.path.join(HERE, "pages")


def _page(name: str) -> HTMLResponse:
    with open(os.path.join(PAGES, name), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


@app.get("/")
async def root(request: Request):
    # redirect by UA to a DISTINCT url so caches never cross-serve mobile/desktop
    ua = request.headers.get("user-agent", "")
    target = "/mobile.html" if "Mobile" in ua else "/index.html"
    return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})


@app.get("/index.html", response_class=HTMLResponse)
async def page_dashboard():
    return _page("dashboard.html")


@app.get("/mobile.html", response_class=HTMLResponse)
@app.get("/m", response_class=HTMLResponse)
async def page_mobile():
    return _page("mobile.html")


@app.get("/d", response_class=HTMLResponse)
async def page_desktop():
    return _page("dashboard.html")


@app.get("/reader", response_class=HTMLResponse)
@app.get("/reader/", response_class=HTMLResponse)
async def page_reader():
    return _page("reader.html")


# ---------- dashboard (board) API — thin wrappers over the reused module ----------

@app.get("/news")
async def board_news():
    data = board.get("news") or {"items": []}
    with board._lock:
        items = [it for it in data["items"] if (it["link"] or it["title"]) not in board._dismissed]
    return {"items": items, "updated": board._updated("news")}


@app.get("/weather")
async def board_weather():
    data = board.get("weather") or {"error": "unavailable"}
    return {**data, "updated": board._updated("weather")}


@app.get("/status")
async def board_status():
    return {**{n: board._updated(n) for n in board._caches}, "radar_time": board._radar_time}


@app.get("/radar-meta")
async def board_radar_meta():
    try:
        anim = board.get_radar_anim()
        return {"count": len(anim["urls"]), "urls": anim["urls"], "times": anim["times"]}
    except Exception as e:  # noqa: BLE001
        return {"count": 0, "error": str(e)}


@app.get("/radar")
async def board_radar():
    img = board.get("radar")
    if img:
        return Response(content=img, media_type="image/png")
    return JSONResponse({"error": "radar unavailable"}, status_code=503)


@app.post("/dismiss")
async def board_dismiss(request: Request):
    # read the raw body so it works regardless of Content-Type (the news pages
    # POST JSON without an application/json header → FastAPI's dict body 422'd,
    # so dismissals were silently dropped and items reappeared on reload)
    try:
        payload = json.loads((await request.body()) or b"{}")
    except Exception:
        payload = {}
    key = payload.get("key")
    if key:
        with board._lock:
            board._dismissed.add(key)
            board.DISMISSED_FILE.write_text(json.dumps(sorted(board._dismissed)))
    return {"ok": True}


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.on_event("startup")
def _prefetch():
    import threading
    for name in board.FETCHERS:  # warm the caches so first requests are instant
        threading.Thread(target=board.get, args=(name,), daemon=True).start()


if __name__ == "__main__":
    import uvicorn

    print(f"News Reader (merged: reader + dashboard) → http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
