#!/bin/bash
# Launch the Local Player web app.
#   LYRICS_ROOT : dir whose subdirs (each containing .txt lyrics) become collections
#   HOST / PORT : bind address / port
#
# The "✨ select-to-explain" LLM models/keys live in config.py (gitignored) — see
# config_example.py. config.py is the single source of truth and takes precedence.
#
# ⚠ DO NOT set LLM_API_KEY (or other LLM_* vars) here — run.sh is git-tracked, so a
#   key in it can leak to the repo. Keys belong only in config.py. The LLM_BASE_URL /
#   LLM_MODEL / LLM_API_KEY / LLM_THINKING env fallback still exists in app.py for a
#   quick single-model setup WITHOUT config.py, but prefer config.py.
cd "$(dirname "$0")"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-7062}"
export LYRICS_ROOT="${LYRICS_ROOT:-$HOME/lyrics_data}"
exec python3 app.py
