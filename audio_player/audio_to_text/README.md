# audio_to_text

Turn speech audio into natural, short-sentence lyrics with accurate word-level timestamps
(`[HH:MM:SS.cc] text`, one sentence per line) — ready to drop into the `player/`.

## Install

```bash
pip install -r requirements.txt
# plus ffmpeg (system):   macOS: brew install ffmpeg
```

`mlx-whisper` is Apple-Silicon only. `funasr`/`torch`/`torchaudio` are only needed for the
optional 2nd engine (SenseVoice) and the VAD QA step — install matching torch/torchaudio versions.

## Quick start

From a video file (extract audio + transcribe in one go):

```bash
python3 mp4_to_lyrics.py "Episode.mp4" --out out_dir
# -> out_dir/Episode.mp3  +  out_dir/Episode.txt   (Whisper)
# add --sensevoice to also run Alibaba SenseVoice as a 2nd engine + gap-fill
```

From an audio file directly (the main, best-quality pipeline with sentence splitting):

```bash
python3 transcribe_sentences.py input.mp3 output.txt
```

Recommended full pass for a folder of mp3s:

```bash
for f in out_dir/*.mp3; do python3 transcribe_sentences.py "$f" "${f%.mp3}.txt"; done
python3 vad_qa.py --apply out_dir/*.mp3      # drop intro/outro junk, flag misaligned lines
python3 fix_abbrev.py out_dir/*.txt          # merge "Mr." / "Dr." orphan splits
python3 fix_loops.py  out_dir/*.txt          # collapse repeated-word hallucinations
```

Then copy the `*.txt` into a collection folder under the player's `LYRICS_ROOT`.

## Scripts

| script | purpose |
|---|---|
| `transcribe_sentences.py <audio> <out.txt>` | main: Whisper word-timestamps + anti-hallucination, split into one short sentence per line (sentence punctuation / clause pause, abbreviation-aware), long run-ons split by an LLM and re-aligned to timestamps, tiny fragments merged by gap |
| `mp4_to_lyrics.py <file\|dir> --out DIR [--sensevoice]` | batch: ffmpeg audio extract + transcribe; optional SenseVoice 2nd engine + gap-fill |
| `vad_qa.py [--apply] <mp3...>` | FSMN-VAD check: drop lines before first / after last speech (intro/outro junk), flag in-silence lines; reads/writes the sibling `.txt` |
| `fix_abbrev.py <txt...>` | merge lines wrongly split after an abbreviation period (`Mr.`, `Dr.`, `U.S.`, initials) |
| `fix_loops.py <txt...>` | collapse consecutive repeated words / bigrams (Whisper loop hallucinations); drop symbol-only lines |

## LLM (optional, for splitting long run-on lines)

`transcribe_sentences.py` calls an OpenAI-compatible chat API to split long lines that have no
punctuation/pause. Configure via env; if unreachable it simply leaves long lines intact.

```bash
export LLM_BASE_URL="<your OpenAI-compatible /chat/completions endpoint>"
export LLM_API_KEY="..."          # optional
export LLM_MODEL="claude-haiku-4-5"
```

## Notes

- Whisper: always run with `condition_on_previous_text=False` (already set) to avoid repetition
  loops on music / sparse speech.
- For English audio, Whisper is the primary engine; SenseVoice is a cheaper 2nd opinion / gap-filler.
- Output timestamps are word-level (Whisper), typically within ~0.1s of true onset (VAD-verified).
