# Book Reader

Read epubs in the browser and interpret any word, phrase or passage with an LLM.
The reader itself is deliberately plain — the point of it is the interpretation
layer, which is what a plain epub viewer cannot give you.

    .venv/bin/python server.py        # → http://localhost:8400/

`/` is the shelf; click a book to read it.

## What it does

Select text and a bubble offers, depending on the book:

| | |
|---|---|
| **✨ 解读** | meaning in this context. A single word gets a fixed four-part answer — IPA, sense here, etymology, and what the surrounding sentence says. |
| **查** | this book's own glossary of proper nouns. Local, instant, no tokens. Only offered for a book that has one. |
| **语法** | sentence structure: translation, clause breakdown, notable idiom. |
| **背景** | the knowledge the passage assumes but does not state. |

Plus **提问** in the header: two selects choose how many printed pages either
side of the one you are on go into context (max 10 each, default 1 back and 0
forward), and then you ask your own question about them.

There is deliberately **no chapter-summary button**. A digest stands in for the
reading; every mode here explains something you are looking at so that you can
read it. `提问` keeps the same distinction: you choose the extent and you ask the
question, and the prompt forbids summarising the pages unasked, requires the
answer to sit in the text supplied, and requires saying so plainly when the
answer is not there rather than guessing.

Every lookup is sent with the book title, author, chapter title, printed page and
the sentences on either side of the selection. This matters more than it sounds:
an ordinary English noun can be a term of art in the book you happen to be
reading, and only the surrounding book tells the model which reading is wanted.

Lookups are saved to `/vocab`, per book, with their sentence and where in the
book you met them. Follow-ups, `提问` sessions and local glossary hits are not
saved — a run of pages is not a vocabulary item, and a glossary hit is already
written down.

## The shelf

`BOOKS_DIR` is a directory of epubs; symlinks are followed, so a book can stay
where it lives. Listing is a cached skim of each OPF — title, author, chapter
count — so a large shelf opens instantly; the full parse happens only for the
book you open, and a few stay in memory (`library.py`).

A book's id is derived from its filename, so reading positions, saved lookups and
glossaries survive a restart, a re-scan, or the file being replaced.

Covers are deliberately **not** fetched for the shelf: they were 93% of its
bytes, and the title is what you are scanning for. Each card is set like a spine
instead. A cold shelf is about 3 KB.

## Design notes

**The book styles itself.** Publisher class names carry real structural
typography — a hanging indent for a bibliography entry, a caption, a chronology
line — but every publisher spells them differently, so a hand-written map of
class names only ever works for one book. Instead the epub's own stylesheet is
reused: filtered to a whitelist of structural declarations and scoped under
`#article` (`epub.py:stylesheet`). Colour, typeface and layout geometry are
dropped — those belong to the reader, and publisher values break the theme, the
font-size control and the image sizing.

**Content documents are decoded, not sniffed.** An epub's XHTML is required to be
UTF-8, and plenty of it carries no declaration saying so; libxml2 then guesses,
and guesses Latin-1, turning every em dash into `â€“`. One book had 5,107
characters mangled across 40 of its 49 documents. `epub.py:decode_html` decodes
by the rules the format guarantees — and strips the XML declaration afterwards,
because lxml refuses a `str` that still carries one.

**Printed pages when the book has them.** Where an epub records the paper book's
page boundaries, paging follows them and the footer reads `p. 47 · 52 / 168`.
Four incompatible conventions exist and `pagination.py` knows all four — but
across a 40-epub sample, **32 had none at all**, so paging by screenful is the
normal case, not the fallback. Run it standalone to see what a book offers:

    .venv/bin/python pagination.py book.epub

**Passages are named, not uploaded.** A question about a page range sends two
integers; the server already has the epub and reads the text itself
(`epub.py:page_text`). Extracting in the browser instead meant uploading tens of
KB on the first turn and again on every follow-up. Three turns over a 21-page
window went from 111 KB uploaded to 5 KB.

**Figures cost nothing until wanted.** Images load as a server-downscaled 96 px
thumbnail beside their caption; clicking fetches the full image. An image that
recurs across many documents, or is tiny, is furniture rather than a plate — a
colophon logo appearing 24 times is not something to offer a zoom on — and is
left inline. The cover is identified from the OPF, not from how it happens to be
marked up.

**Text is gzipped, streams are not.** Starlette's `GZipMiddleware` decides on
response size alone and would therefore also buffer `/api/interpret`;
interpretation has to arrive token by token to feel live, so `GzipExceptStreams`
excludes `text/event-stream` by content-type, and skips JPEGs as already
compressed.

**Assets are versioned.** Every page loads its script as `?v=<mtime of static/>`,
and that token is part of the page's ETag. Without it a phone will happily run
yesterday's JavaScript against today's HTML — and that failure is silent: the
page works, one feature is just missing. It cost two rounds of debugging to find
once, which is once too many.

**The footer shows what the session has cost.** Tap the byte counter for a
breakdown by kind. Measured in the browser from Resource Timing, so it is the
bytes this device actually pulled after gzip. Two traps there: the HTML document
is a `navigation` entry and absent from `getEntriesByType('resource')`, and a
`PerformanceObserver` with `buffered: true` replays entries a prior
`getEntriesByType` already returned — the naive version both under- and
double-counts at once.

Nothing is extracted from the epub to disk; text, images and CSS are read out of
the zip on demand.

## Prompts

Kept short, and the single-word branch kept strictly disjoint from the general
one: a one-word selection is detected server-side and the two are concatenated,
so anything said in both is said twice. They once overlapped almost entirely
*and* disagreed — one allowing IPA to be skipped for easy words, the other
demanding it always — which handed the model a choice that should not have been
its to make. Splitting them cut the system prompt 43% with no loss.

What counts as one word is defined by what a word is *not* — it contains no
space — rather than by enumerating permitted characters. The enumerating version
(`[A-Za-z][A-Za-z'’.\-]*`) silently excluded every accented word, so `café` and
`émigrés` lost their IPA and etymology; across a 25-epub sample there were 80
distinct such words, and they are exactly the ones a reader stops on.

Changing a prompt changes every model's output, so any edit needs re-running
across the whole configured menu. Two things learned the hard way: `max_tokens`
is an *output* budget a reasoning model will spend on hidden thinking, leaving
nothing for the answer when only `delta.content` is forwarded; and `temperature`
is now rejected outright by some models, so it is per-model and omitted entirely
when set to `None` — overriding cannot achieve that, since overriding replaces a
value rather than removing a key.

When asking for structured output, ask for delimited lines, not JSON. Asked for a
JSON array of glossary entries the model reliably emitted an unescaped quote
inside a description and 5 of 11 batches failed to parse; the same request as
`a | b | c | d | e` per line succeeded 8 of 8. A malformed line loses one row,
a malformed array loses all of them.

## Tools

    tools/pdf2epub.py        text-layer PDF → epub: chapters from the bookmarks,
                             paragraphs rejoined, running heads removed, and an
                             anchor per page carrying the printed folio
    tools/build_glossary.py  a book's own proper nouns → IPA, Chinese rendering,
                             one line each; stored per book for 查
    tools/add_appendix.py    append a chapter to an epub, so reference matter
                             turns up in the table of contents like anything else

`pdf2epub` recovers the printed page numbers by voting: a folio sequence differs
from the page index by a fixed offset, so the offset shared by the most pages is
the book's, and every number that disagrees — a table cell, a year, a note
marker — was never a folio. Taking the first plausible number per page instead
produced labels ranging over 1–36109 with 121 places going backwards.

`build_glossary` finds names by aggregating capitalised words over the body text
and keeping the frequent ones. Position does most of the filtering (a word never
seen away from the start of a sentence is not a name); the rest is a stop list,
plus `--drop` for the book-specific case, since no shared list can know that
"Roman" is noise in a history of Rome and signal anywhere else. Check with
`--dry` before spending anything.

## Layout

    server.py       FastAPI: shelf, chapters, resources, interpret SSE, sqlite
    library.py      a directory of epubs, skimmed for listing, opened on demand
    epub.py         epub → HTML: metadata, spine, TOC, stylesheet, text
    pagination.py   printed-page detection (optional; also a standalone probe)
    config.py       books directory, LLM endpoints, keys — gitignored
    pages/          library.html, reader.html, vocab.html
    static/         interpret-widget.js, icons
    tools/          the three converters above

`static/interpret-widget.js` came from `../news_reader` and is kept close to it.
What it gained is generic rather than book-specific: `INTERPRET_MODES` for which
buttons the bubble offers, `INTERPRET_LOCAL` for a mode answered on the spot
instead of streamed, and `payload.extra` so a page can name a passage without the
widget knowing what a page range is. The bubble rebuilds its buttons every time
it opens, because what a page can offer often is not known until its own data has
loaded — assuming otherwise is what hid 查 for an afternoon.

## Keys

`←` `→` page · `⇧←` `⇧→` chapter · `t` contents · `-` `+` font size · `Esc` close

Font size, reading face (serif / sans) and theme live in the ⚙ menu.

The header stays put rather than hiding on scroll: which chapter you are in is
the thing you lose track of. Home and contents sit at opposite ends of it — both
are navigation, and side by side they were easy to mis-tap.

One trap worth recording, since it recurs with any new book: a publisher
stylesheet routinely carries a body-level `font-size:1em`, which after scoping
becomes `#article.<bodyclass>` — one class more specific than `#article`, so it
wins and pins the text to 16px whatever size the reader picked. The reader's size
therefore sits on `main` and the article inherits it, so publisher `em` values
multiply it instead of replacing it.

## Configuration

Copy `config_example.py` to `config.py` (gitignored) and set `BOOKS_DIR` and
`MODELS`. API keys stay server-side; the browser only ever sees a model's display
name via `/api/models`. `BOOKS_DIR`, `HOST`, `PORT` and `DB_PATH` can also come
from the environment.

Call every model end-to-end before listing it. A proxy will happily advertise
models whose deployments 404 or 500 on use, and a menu entry that fails only once
you pick it mid-sentence is worse than no entry at all.

`reader.db` holds reading positions, saved lookups and glossaries. It is personal
data and stays out of the repo, as do the books themselves.

## Not done yet

- **A general dictionary.** `查` covers the proper nouns of a book that has a
  glossary. Ordinary vocabulary still costs an LLM round-trip, including for
  `the` and `house`. A local dictionary (ECDICT is ~770k entries with Chinese
  glosses, IPA and frequency bands) would answer those instantly, and its
  frequency data would let the difficulty judgement in the prompt work from real
  numbers instead of the model's guess. `INTERPRET_LOCAL` is the seam it slots
  into.
- **Multi-word terms.** Names are aggregated word by word, so "Magna Graecia"
  and "Concilium Plebis" arrive as halves and get dropped.
