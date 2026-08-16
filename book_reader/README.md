# Book Reader

Read an epub in the browser and interpret any word, phrase or passage with an
LLM. The reader is deliberately plain — the point of it is the interpretation
layer, which is what a plain epub viewer cannot give you.

    .venv/bin/python server.py        # → http://localhost:8400/read

## What it does

Select text and a bubble offers three readings, each streamed into a side drawer
you can then ask follow-up questions in:

| | |
|---|---|
| **✨ 解读** | meaning in this context. A single word gets a fixed four-part answer — IPA, sense here, etymology, and what the surrounding sentence says. |
| **语法** | sentence structure: translation, clause breakdown, notable idiom. |
| **背景** | the knowledge the passage assumes but does not state — people, institutions, allusions, and why the author put this sentence here. |

Plus **提问** in the header: two selects choose how many printed pages either
side of the one you are on go into context (max 10 each, default 1 back and 0
forward), and then you ask your own question about them. It crosses chapter
boundaries, and reports how many characters it is about to send before you
commit to a question.

There is deliberately **no chapter-summary button**. A digest stands in for the
reading; every mode here explains something you are looking at so that you can
read it. `提问` keeps the same distinction: you choose the extent and you ask the
question, and the prompt forbids summarising the pages unasked, requires the
answer to sit in the text supplied, and requires saying so plainly when the
answer is not there rather than guessing. That last one holds up in practice —
given a two-page window that stops mid-sentence, the model reports that the
passage cuts off and the answer lies further on, instead of filling the gap from
what it already knows about the subject.

Every lookup is sent with the book title, author, chapter title, printed page and
the sentences on either side of the selection. This matters more than it sounds:
an ordinary English noun can be a term of art in the book you happen to be
reading, and only the surrounding book tells the model which reading is wanted.

Every lookup is also saved. A book is read over days and the same proper nouns
and terms keep coming back, so `/vocab` keeps them with their sentence and where
in the book you met them. Follow-ups and `提问` sessions are not saved — they
belong to the entry they came from, and a run of pages is not a vocabulary item.

## Design notes

**The book styles itself.** Publisher class names carry real structural
typography — a hanging indent for a bibliography entry, a caption, a chronology
line — but every publisher spells them differently, so a hand-written map of
class names only ever works for one book. Instead the epub's own stylesheet is
reused: filtered to a whitelist of structural declarations and scoped under
`#article` (`epub.py:stylesheet`, served at `/api/book.css`). Colour, typeface
and layout geometry are dropped — those belong to the reader, and publisher
values break the theme, the font-size control and the image sizing.

**Printed pages when the book has them.** Where an epub records the paper book's
page boundaries, paging follows them and the footer reads `p. 47 · 52 / 168`, so
a position is stable and citable. Four incompatible conventions exist for
recording this and `pagination.py` knows all four — but across a 40-epub sample,
**32 had none at all**, so its absence is the normal case: those books page by
screenful instead. Run it standalone to see what a book offers:

    .venv/bin/python pagination.py book.epub

**Passages are named, not uploaded.** A question about a page range sends two
integers; the server already has the epub and reads the text itself
(`epub.py:page_text`). Extracting in the browser instead meant uploading tens of
KB on the first turn and again on every follow-up, and fetching neighbouring
chapters just to assemble a range crossing them. Three turns over a 21-page
window went from 111 KB uploaded to 5 KB. `/api/passage` returns the size only,
so the reader can show what a range costs without transferring it.

**Figures cost nothing until wanted.** Images load as a server-downscaled 96 px
thumbnail beside their caption; clicking fetches the full image. A chapter with
four plates goes from 192 KB of figures to 13 KB — 93% — which matters reading
over a phone or a tunnel. Thumbnails are real resizes cached under `cache/`, not
CSS shrinks of the full file.

**Chapters are fetched once.** They stay in memory for the session, so paging
back over a chapter boundary costs nothing. Nothing is prefetched — a chapter is
fetched only when you actually turn onto it.

**Text is gzipped, streams are not.** Chapter HTML compresses 60–64%, and a
link-dense chapter such as an index 91%. Starlette's `GZipMiddleware` decides on
response size alone and would therefore also buffer `/api/interpret`;
interpretation has to arrive token by token to feel live, so `GzipExceptStreams`
in `server.py` excludes `text/event-stream` by content-type, and skips JPEGs as
already compressed.

**The footer shows what the session has cost.** Tap the byte counter for a
breakdown by kind — text, thumbnails, full images, interpretation. It is measured
in the browser from Resource Timing, so it is the bytes this device actually
pulled after gzip, and a cache hit correctly counts as zero. Two traps there: the
HTML document is a `navigation` entry and absent from `getEntriesByType('resource')`,
and a `PerformanceObserver` with `buffered: true` replays entries a prior
`getEntriesByType` already returned, so the naive version both under- and
double-counts at once.

A cold load is about 24 KB, plus 6 KB for a text chapter and 4 KB for its
figures. It was 180 KB until `/favicon.ico` stopped serving the untouched cover:
anything that reaches for `book.open()` without going through the `?w=` thumbnail
path is a 157 KB mistake waiting to happen, and a route the browser requests by
itself is the easiest place to miss one.

Nothing is extracted from the epub to disk; text, images and CSS are all read
out of the zip on demand.

## Prompts

Kept short on purpose, and the single-word branch is kept strictly disjoint from
the general one: a one-word selection is detected server-side and the two are
then concatenated, so anything said in both is said twice. They once overlapped
almost entirely *and* disagreed — one allowing IPA to be skipped for easy words,
the other demanding it always — which handed the model a choice that should not
have been its to make. Splitting them cut the system prompt 43% with no loss:
a single-word answer still carries IPA, sense here, etymology and the sentence's
meaning, verified down to `the`.

What counts as one word is defined by what a word is *not* — it contains no
space — rather than by enumerating permitted characters. The enumerating version
(`[A-Za-z][A-Za-z'’.\-]*`) silently excluded every accented word, so `café`,
`émigrés` and `naïve` fell through to the phrase rules and lost their IPA and
etymology; across a 25-epub sample there were 80 distinct such words, and they
are exactly the ones a reader stops on. Digits pass for the same reason
(`COVID-19`, `5G`); a Latin letter is still required, so a Chinese selection
keeps the reverse treatment instead.

Changing a prompt changes every model's output, so any edit needs re-running
across the whole configured menu, not just the default. Two things learned the
hard way: `max_tokens` is an *output* budget that a reasoning model will spend on
hidden thinking, leaving nothing for the answer when only `delta.content` is
forwarded; and `temperature` is starting to be rejected outright by newer models,
so it is per-model here and omitted entirely when set to `None` — overriding
cannot achieve that, since overriding replaces a value rather than removing a key.

## Layout

    server.py       FastAPI: book/chapter/resource routes, interpret SSE, sqlite
    epub.py         epub → HTML: metadata, spine, TOC, stylesheet filtering, text
    pagination.py   printed-page detection (optional; also a standalone probe)
    config.py       book path, LLM endpoints, keys — gitignored
    pages/          reader.html, vocab.html
    static/         interpret-widget.js — the selection bubble and drawer

`static/interpret-widget.js` came from `../news_reader` and is kept close to it —
a third mode button, the book-context fields, and a generic `payload.extra`
pass-through so this reader can name a passage without the widget knowing what a
page range is. Keeping it recognisable means a fix in either reader can be
carried across.

## Keys

`←` `→` page · `⇧←` `⇧→` chapter · `t` contents · `-` `+` font size · `Esc` close drawer

Font size, reading face (serif / sans) and theme live in the ⚙ menu, which the
widget owns and persists.

The header stays put rather than hiding on scroll: which chapter you are in is
the thing you lose track of, so it is always named there. Printed page boundaries
render as a rule carrying the page number, splitting the paragraph exactly where
the paper page ended.

One trap worth recording, since it recurs with any new book: a publisher
stylesheet routinely carries a body-level `font-size:1em`, which after scoping
becomes `#article.<bodyclass>` — one class more specific than `#article`, so it
wins and pins the text to 16px whatever size the reader picked. The reader's size
therefore sits on `main` and the article inherits it, so publisher `em` values
multiply it instead of replacing it.

## Configuration

Copy `config_example.py` to `config.py` (gitignored) and set `BOOK_PATH` and
`MODELS`. API keys stay server-side; the browser only ever sees a model's display
name via `/api/models`. `BOOK_PATH`, `HOST`, `PORT` and `DB_PATH` can also come
from the environment.

Call every model end-to-end before listing it. A proxy will happily advertise
models whose deployments 404 or 500 on use, and a menu entry that fails only once
you pick it mid-sentence is worse than no entry at all.

## Not done yet

- **Offline dictionary.** `/api/dict` is a stub returning 501. Every word lookup
  currently costs an LLM round-trip of a second or two, including for `the` and
  `house`. A local dictionary (ECDICT is ~770k entries with 中文 glosses, IPA and
  frequency bands) would answer those instantly and leave the LLM to phrases,
  sentences and the sense a word carries in this passage. Its frequency data
  would also let the difficulty judgement in the prompt work from real data
  instead of the model's guess.
- **One book at a time.** `BOOK_PATH` is a single epub. Nothing in the parser is
  book-specific, so a library index over a directory is additive.
