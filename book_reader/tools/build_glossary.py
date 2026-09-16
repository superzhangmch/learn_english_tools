#!/usr/bin/env python3
"""Build a book's own glossary of proper nouns, and store it for lookup.

A general dictionary is no use for the names that actually stop you in a novel
or an epic: Amphimachus and Lyrnessus are not in any dictionary, and "Paris" is
in the wrong one. What helps is a glossary of *this book* — so each book gets
its own, keyed by book id, and the reader looks up against that.

Names are found by aggregating capitalised words over the body text and keeping
the frequent ones, then an LLM supplies pronunciation, the conventional Chinese
rendering, and one line on who or what it is.

    python tools/build_glossary.py <book.epub> [--min 5] [--model M] [--dry]
"""
import argparse
import collections
import json
import os
import re
import sqlite3
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from epub import Epub          # noqa: E402
from library import book_id    # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "reader.db"))

def _llm_defaults() -> tuple[str, str, str]:
    """Endpoint, key and model — from config.py, which is gitignored.

    Deliberately not defaulted to anything concrete here: this file ships
    publicly, and a hardcoded proxy address describes the author's machine to
    anyone reading the repo.
    """
    try:
        import config as cfg
    except ImportError:
        return "", "", ""
    first = (getattr(cfg, "MODELS", None) or [{}])[0]
    base = (first.get("base_url") or getattr(cfg, "LLM_BASE_URL", "")).rstrip("/")
    return (f"{base}/chat/completions" if base else "",
            first.get("api_key") or getattr(cfg, "LLM_API_KEY", ""),
            first.get("model") or getattr(cfg, "LLM_MODEL", ""))


_BASE, _KEY, _MODEL = _llm_defaults()
ENDPOINT = os.environ.get("GLOSSARY_ENDPOINT") or _BASE
API_KEY = os.environ.get("GLOSSARY_KEY") or _KEY

# Capitalised words that are not names. Sentence-initial position already
# filters most of these out; what is left is adjectives and epithets, which a
# poem in translation produces in bulk.
STOP = set("""The A An And But For Nor Or So Yet If When While Then Now Here There This That These
Those He She It They We You I His Her Its Their Our Your My Him Them Us Me Who What Which Where Why
How In On At By To Of With From Into Over Under As Not No Yes All Some Any Each Every Both Either
Do Does Did Have Has Had Will Would Shall Should Can Could May Might Must Let Come Go Say Said See
One Two Three Four Five Six Seven Eight Nine Ten First Next Last Other Another Such Same Own Like
O Ah Oh Sir Lord Lady God Gods King Queen Father Mother Son Daughter Brother Sister Men Man Women
Chapter Book Page Note Notes Introduction Meanwhile However Therefore Besides Instead Perhaps Even
Still Just Once Again Away Back Down Up Out Off Well Never Always Soon Nothing Something Since
Until Before After Though Although Because Whether Unless Whenever Wherever Whatever Whoever Thus
Hence Moreover Nevertheless Indeed Surely Truly Alas Behold Look Take Give Make Know Think Tell Ask
Speak Hear Feel Leave Stand Fall Bring Send Keep Hold Turn Get Listen Remember Seeing Enough Very
None Later Angrily Most Many Quick Friends Are Great Good Dear Poor Old Young Long Far High Deep
Wide Full Fair Swift Brave Noble Divine Immortal Warlike Glorious Venerable Dread Godlike Mount
River Battle
War Wars Empire City Temple Hill Sea Land Island Lake Valley Gate Wall Road Field Camp House
North South East West Second Third Fourth New Old Elder Younger Minor Major Great Little
League Orders Conflict Figure Table Tables Age Ages Period Century Year Years Day Days Time
Social Public Private Civil Foreign Modern Ancient Early Late Middle Northern Southern Eastern
Western Central Upper Lower Inner Outer United States Kingdom Republic Nation People World""".split())

SYSTEM = (
    "你在为一本英文原版书编专名对照表，读者是中国读者：读英文时看到一个名字，"
    "要能立刻对上中文、知道是谁或是什么地方。\n\n"
    "**输出格式：每个词条一行，五段用竖线 | 分隔。不要表头、不要编号、不要解释、不要代码围栏。**\n"
    "英文原名 | 英语读法国际音标 | 中文译名 | 类别 | 一句话说明\n\n"
    "· 英文原名：原样照抄给定拼写。\n"
    "· 音标：这个名字在**英语中**的读法，带重音符，如 /əˈkɪliːz/。不是原语言读法。\n"
    "· 中文译名：优先采用该领域**通行的中文译法**；古希腊罗马专名用罗念生／王焕生体系。"
    "无通行译法的按原语音译。\n"
    "· 类别：只填 人 / 神 / 地 / 族 / 物 之一。\n"
    "· 说明：不超过 30 个汉字，讲清在**本书里**的身份关系，**句中不要出现竖线**。\n\n"
    "给了 example 的，据此判断本书里指的是哪一个（同名者很多）。"
)


def body_chapters(book: Epub) -> list[int]:
    """Prefer numbered chapters; fall back to everything of a decent length."""
    toc = {t["chapter"]: t["label"] for t in book.outline()["toc"]}
    numbered = [i for i in range(len(book.spine))
                if re.match(r"^\s*(\d+|[IVXLC]+)[.\s]", toc.get(i, ""))]
    if len(numbered) >= 3:
        return numbered
    return [i for i in range(len(book.spine)) if len(book.chapter_text(i)) > 2000]


def find_names(book: Epub, minimum: int) -> dict:
    text = " ".join(book.chapter_text(i) for i in body_chapters(book))
    counts, non_initial = collections.Counter(), collections.Counter()
    for m in re.finditer(r"(^|[.!?;:\"”’]\s+|\n)?\s*\b([A-Z][A-Za-zà-ÿ'’-]{2,})\b", text):
        w = re.sub(r"[’']s$", "", m.group(2))
        if len(w) < 3:
            continue
        if w.isupper():
            w = w.title()
        if w in STOP or w.title() in STOP or "’" in w or "'" in w:
            continue
        counts[w] += 1
        if not m.group(1):
            non_initial[w] += 1
    names = {w: n for w, n in counts.items() if non_initial[w] >= 1 and n >= minimum}
    # merge a plural or adjectival form into the base when both are present
    for w in list(names):
        for suffix in ("s", "es", "ns"):
            base = w[: -len(suffix)]
            if suffix and w.endswith(suffix) and base in names and len(base) > 3:
                names[base] += names.pop(w)
                break
    out = {}
    for w, n in sorted(names.items(), key=lambda kv: -kv[1]):
        m = re.search(r"[^.!?]{0,110}\b" + re.escape(w) + r"\b[^.!?]{0,110}[.!?]", text)
        out[w] = {"n": n, "eg": " ".join(m.group(0).split())[:200] if m else ""}
    return out


def describe(items, context, model) -> list[dict]:
    """One batch, as delimited lines.

    Not JSON: asked for a JSON array the model reliably emitted an unescaped
    quote inside a description and half the batches failed to parse. A line of
    five fields cannot be malformed in a way that loses the other rows.
    """
    user = ((f'参考（本书自带的人物表节选，译名与身份以它为准）：\n"""\n{context[:2600]}\n"""\n\n'
             if context else "") + "词条：\n" +
            "\n".join(f"- {w}（{d['n']} 次）example: {d['eg'][:140]}" for w, d in items))
    for attempt in range(3):
        try:
            r = httpx.post(ENDPOINT, timeout=300,
                           headers={"Authorization": f"Bearer {API_KEY}"},
                           json={"model": model, "max_tokens": 8000, "temperature": 0.2,
                                 "messages": [{"role": "system", "content": SYSTEM},
                                              {"role": "user", "content": user}]})
            text = r.json()["choices"][0]["message"]["content"]
            rows = []
            for line in text.splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) == 5 and parts[0] and not parts[0].startswith(("#", "-", "·", "`", "英文")):
                    rows.append(dict(zip(("en", "ipa", "zh", "kind", "note"), parts)))
            if rows:
                return rows
        except Exception as e:  # noqa: BLE001
            print(f"    retry {attempt + 1}: {type(e).__name__} {str(e)[:60]}", file=sys.stderr)
            time.sleep(2)
    return []


def character_list(book: Epub) -> str:
    """A 'Main Characters' section, if the book has one, as authority."""
    for entry in book.outline()["toc"]:
        if re.search(r"character|dramatis|persons|cast", entry["label"], re.I):
            return book.chapter_text(entry["chapter"])
    return ""


def store(bid: str, title: str, rows: list[dict]) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS glossary (
        book TEXT NOT NULL, term TEXT NOT NULL, key TEXT NOT NULL,
        ipa TEXT, zh TEXT, kind TEXT, note TEXT, n INTEGER,
        PRIMARY KEY (book, term)
    );
    CREATE INDEX IF NOT EXISTS glossary_key ON glossary(book, key);
    """)
    with conn:
        conn.execute("DELETE FROM glossary WHERE book=?", (bid,))
        conn.executemany(
            "INSERT OR REPLACE INTO glossary(book, term, key, ipa, zh, kind, note, n) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [(bid, r["en"], r["en"].lower(), r["ipa"], r["zh"], r["kind"], r["note"],
              r.get("n", 0)) for r in rows])
    conn.close()
    print(f"{title}\n  {len(rows)} entries stored for book {bid}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("epub")
    ap.add_argument("--min", type=int, default=5, help="minimum occurrences")
    ap.add_argument("--model", default=_MODEL)
    ap.add_argument("--batch", type=int, default=18)
    ap.add_argument("--dry", action="store_true", help="list the names and stop")
    ap.add_argument("--drop", default="",
                    help="comma-separated words to exclude for this book")
    ap.add_argument("--json", default="", help="also write the table here")
    a = ap.parse_args()

    if not a.dry and not ENDPOINT:
        sys.exit("No LLM endpoint. Set MODELS in config.py, or GLOSSARY_ENDPOINT "
                 "and GLOSSARY_KEY in the environment.")
    book = Epub(a.epub)
    bid = book_id(a.epub)
    names = find_names(book, a.min)
    # Book-specific exclusions. The shared STOP list cannot know that "Roman" is
    # noise in a history of Rome and signal anywhere else.
    for w in (x.strip() for x in a.drop.split(",") if x.strip()):
        names.pop(w, None)
    print(f"{book.meta['title']}\n  {len(names)} names occurring >= {a.min} times")
    if a.dry:
        print("  " + ", ".join(list(names)[:60]) + (" …" if len(names) > 60 else ""))
        return

    context = character_list(book)
    items = list(names.items())
    rows: list[dict] = []
    for i in range(0, len(items), a.batch):
        chunk = items[i:i + a.batch]
        t0 = time.time()
        got = describe(chunk, context, a.model)
        print(f"  batch {i // a.batch + 1}: asked {len(chunk)}, got {len(got)}"
              f"  {time.time() - t0:.0f}s", file=sys.stderr)
        for r in got:
            r["n"] = names.get(r["en"], {}).get("n", 0)
        rows.extend(got)

    seen, unique = set(), []
    for r in rows:
        if r["en"] not in seen:
            seen.add(r["en"])
            unique.append(r)
    store(bid, book.meta["title"], unique)
    if a.json:
        json.dump(unique, open(a.json, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
