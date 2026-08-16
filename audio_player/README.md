# Audio Player

Read a transcript on your phone while the audio plays **locally from the
device** — zero streaming, so it costs no bandwidth and little battery — and
interpret any word or phrase with an LLM. Lyrics are editable in the page.

    LYRICS_ROOT=~/lyrics_data PORT=7062 ./run.sh      # → http://localhost:7062

Two parts: the player itself, and `audio_to_text/`, the ASR pipeline that turns
video/audio into the timestamped transcripts the player reads.

## Player

Point `LYRICS_ROOT` at a directory; every subdirectory holding `.txt` lyrics
becomes a selectable collection. Adding material means dropping a folder of
`.txt` in — there is no database and no import step.

```
$LYRICS_ROOT/
  some_show/     *.txt        # one .txt per episode
  another/       *.txt  *.mp4
```

Line format is `[HH:MM:SS] text` or `[HH:MM:SS.cc] text`, one sentence per line.
An optional `_config.json` per collection sets `label` (display name), `type`
(`audio` or `video`), and `break_after` (a regex on the track name that groups
the list, e.g. by season).

- Pick local mp3s once → cached in the browser (IndexedDB) → auto-restored next
  time, fully offline. Video collections stream `.mp4` from the server instead.
- Sticky progress bar with full-height lyrics, current line auto-centred;
  auto-follow backs off while you scroll or hold a selection.
- Dark/light and font size are remembered (`localStorage`).
- In-page lyrics editor: validates the timestamp format and that times never run
  backwards, and writes a timestamped `.bak` before saving.

### ✨ 解读 (select → LLM)

Select any lyric text and a bubble offers 解读; the answer streams into a side
drawer you can ask follow-up questions in. The prompt is tuned for a Chinese
speaker learning English: common words get one line, while idioms, slang and
cultural references get the background, and suspected ASR mistakes are called
out.

Only the selected line plus 5 lines either side are sent as context — enough to
resolve pronouns without shipping the whole transcript on every turn. Playback
pauses while the drawer (or the editor) is open and resumes when you leave it.

Models come from `config.py` — copy `config_example.py` and edit it. It is
gitignored, and it is the **single** source of models and keys: keys never
belong in `run.sh`, which is tracked. Each entry is any OpenAI-compatible
`/chat/completions` streaming endpoint, and its optional `extra` is merged into
the request payload (that is where a thinking model's
`{"thinking": {"type": "disabled"}}` or a `temperature` goes). The browser only
ever sees each model's display name; keys stay server-side.

Needs `flask`.

## `audio_to_text/` — audio → timestamped lyrics (ASR)

Transcribe speech to natural short-sentence lyrics with word-level timestamps.

Pipeline (`transcribe_sentences.py <audio> <out.txt>`):
- Whisper (`mlx-whisper`, `whisper-large-v3-turbo`) with `word_timestamps` and
  anti-hallucination (`condition_on_previous_text=False`).
- Split into one short sentence per line: break at sentence punctuation **or** a
  clause pause, abbreviation-aware (won't break `Mr.` / `Dr.` / `U.S.` /
  initials).
- Long run-ons are split semantically by an LLM (any OpenAI-compatible chat
  API), then re-aligned to the original word timestamps.
- Tiny stray fragments are merged into the temporally-closest neighbour (never
  across a real pause).
- Output format: `[HH:MM:SS.cc] text`.

Helpers:
- `mp4_to_lyrics.py <file|dir> --out DIR [--sensevoice]` — extract audio +
  transcribe in batch (Whisper primary, optional Alibaba SenseVoice as a second
  engine / gap-fill).
- `vad_qa.py [--apply] <mp3...>` — FSMN-VAD cross-check: drop intro/outro junk,
  flag lines that don't align with detected speech.
- `fix_abbrev.py` / `fix_loops.py` — post-fixes (merge abbreviation orphans;
  collapse consecutive repeated words/bigrams).

This pipeline takes its LLM endpoint from the environment, not `config.py`:

```bash
export LLM_BASE_URL="<your OpenAI-compatible /chat/completions endpoint>"
export LLM_API_KEY="..."        # optional
export LLM_MODEL="claude-haiku-4-5"
```

Needs `pip install mlx-whisper` (Apple Silicon), plus `funasr` for
`--sensevoice`, and `ffmpeg` for audio extraction.
