#!/bin/bash
# Build, install, and refresh the EtymologyDict macOS dictionary.
#
# Usage:
#   ./build.sh                                  # use defaults
#   DATA_DIR=/path/to/data ./build.sh           # override data dir
#
# All paths are configurable via env vars. Defaults point to this user's setup;
# change them or set env vars to point at your own locations.

set -euo pipefail

# --- Configurable paths ---
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Where the source data files (etymology_dict.json, word_source.js) live.
# Defaults to the bundled `data/` directory in this repo.
DATA_DIR="${DATA_DIR:-$ROOT_DIR/data}"

# Where to write the AppleDict XML/CSS/plist/Makefile source.
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/objects/apple_src}"

# Path to the mac-dictionary-kit ddk/ directory (provides build_dict.sh and
# the compiled binary tools that turn AppleDict XML into a .dictionary bundle).
DDK_PATH="${DDK_PATH:-$ROOT_DIR/mac-dictionary-kit/ddk}"

# Python interpreter (must be 3.11+ with lemminflect + nltk installed).
PYTHON="${PYTHON:-python3.11}"

echo "DATA_DIR   = $DATA_DIR"
echo "OUTPUT_DIR = $OUTPUT_DIR"
echo "DDK_PATH   = $DDK_PATH"
echo "PYTHON     = $PYTHON"
echo

# --- Step 1: generate AppleDict XML source ---
echo "[1/3] Generating AppleDict XML source..."
"$PYTHON" "$ROOT_DIR/build_full.py" \
    --data-dir "$DATA_DIR" \
    --output-dir "$OUTPUT_DIR" \
    --ddk-path "$DDK_PATH"

# --- Step 2: compile to .dictionary bundle ---
echo
echo "[2/3] Compiling .dictionary bundle..."
cd "$OUTPUT_DIR"
make clean
make

# --- Step 3: install + refresh Dictionary.app ---
echo
echo "[3/3] Installing to ~/Library/Dictionaries/..."
make install

echo
echo "Killing Dictionary.app + LookupViewService to refresh caches..."
killall -9 LookupViewService Dictionary 2>/dev/null || true

echo
echo "Done. Open Dictionary.app -> Preferences -> enable 'EtymologyDict'."
