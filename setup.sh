#!/usr/bin/env bash
# Eva — one-shot setup (macOS / Apple Silicon).
#
# Installs EVERY dependency and downloads EVERY model weight Eva needs, so that
# after this finishes the app runs fully offline (CLAUDE.md rule 4: the only
# permitted network access is this first-run download). Idempotent — re-running
# skips anything already present.
#
# What it does, in order:
#   1. Verify / install system tools  : Homebrew, Python >=3.11, Node+npm,
#                                        llama.cpp (the llama-server binary),
#                                        espeak-ng (voice-out G2P fallback).
#   2. Backend Python venv            : backend/.venv + pip install requirements.
#   3. Frontend deps                  : npm install in ui/.
#   4. Model weights (the downloads)  : Gemma 4 LLM, bge-small embeddings,
#                                        faster-whisper STT, Kokoro TTS.
#   5. Rust toolchain (optional)      : only needed for the native Tauri window.
#
# Usage:
#   ./setup.sh                # install everything, voice models included
#   ./setup.sh --no-voice     # skip the Whisper + Kokoro voice downloads
#   ./setup.sh --with-rust    # also install the Rust toolchain (native window)
#   ./setup.sh --help
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WITH_VOICE=1
WITH_RUST=0

for arg in "$@"; do
  case "$arg" in
    --no-voice)  WITH_VOICE=0 ;;
    --with-rust) WITH_RUST=1 ;;
    -h|--help)
      sed -n '2,/--help$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "[setup] unknown option: $arg (try --help)" >&2
      exit 2 ;;
  esac
done

say()  { echo "[setup] $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# --- 1. system tools -------------------------------------------------------
say "Checking system tools…"

if [ "$(uname -s)" != "Darwin" ]; then
  say "WARNING: this script targets macOS / Apple Silicon. Continuing, but"
  say "         Homebrew steps may not apply on your platform."
fi

if ! have brew; then
  say "Homebrew not found. Install it from https://brew.sh then re-run." >&2
  say "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"" >&2
  exit 1
fi

# Eva needs Python >= 3.11 (the macOS system python3 is often 3.9, too old).
# Find the first interpreter that qualifies — your 3.12 is fine; 3.13 etc. too.
PYTHON_BIN=""
for cand in python3.13 python3.12 python3.11 python3; do
  if have "$cand" && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    PYTHON_BIN="$cand"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  say "No Python >= 3.11 found. Installing Python 3.12 via Homebrew…"
  brew install python@3.12
  PYTHON_BIN="python3.12"
fi
say "Python: $("$PYTHON_BIN" --version) ($(command -v "$PYTHON_BIN"))"

if ! have node || ! have npm; then
  say "Installing Node via Homebrew…"
  brew install node
else
  say "Node: $(node --version), npm: $(npm --version)"
fi

# llama.cpp provides the `llama-server` binary the backend supervises (:11500).
if ! have llama-server && [ ! -x /opt/homebrew/bin/llama-server ]; then
  say "Installing llama.cpp (provides llama-server)…"
  brew install llama.cpp
else
  say "llama-server: present"
fi

# espeak-ng — Kokoro TTS grapheme-to-phoneme fallback for unusual words.
if [ "$WITH_VOICE" -eq 1 ]; then
  if ! have espeak-ng; then
    say "Installing espeak-ng (voice-out pronunciation fallback)…"
    brew install espeak-ng
  else
    say "espeak-ng: present"
  fi
fi

# --- 2. backend venv -------------------------------------------------------
if [ ! -d backend/.venv ]; then
  say "Creating backend venv with $PYTHON_BIN…"
  "$PYTHON_BIN" -m venv backend/.venv
fi
say "Installing backend Python dependencies…"
backend/.venv/bin/pip install --upgrade pip >/dev/null
backend/.venv/bin/pip install -r backend/requirements.txt

VENV_PY="$ROOT/backend/.venv/bin/python"

# --- 3. frontend deps ------------------------------------------------------
say "Installing frontend dependencies (npm)…"
( cd ui && npm install )

# --- 4. model weights (the downloads) --------------------------------------
# These deliberately bypass the runtime net-guard; they are the one-time,
# online part of setup. Each is idempotent and skips if already cached.

say "Downloading the LLM (Gemma 4 E2B GGUF, ~2.6 GB)…"
bash scripts/download_model_mac.sh

say "Downloading the embedding model (bge-small) — needed for Library + recall…"
"$VENV_PY" scripts/download_embed_model.py

if [ "$WITH_VOICE" -eq 1 ]; then
  say "Downloading voice-in weights (faster-whisper base.en + small.en)…"
  "$VENV_PY" scripts/download_whisper_model.py all

  say "Downloading voice-out weights (Kokoro + af_heart + spaCy en_core_web_sm)…"
  "$VENV_PY" scripts/download_kokoro_model.py
else
  say "Skipping voice model downloads (--no-voice)."
fi

# --- 5. Rust toolchain (optional) ------------------------------------------
if [ "$WITH_RUST" -eq 1 ] && ! have cargo; then
  say "Installing the Rust toolchain (for the native Tauri window)…"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
elif ! have cargo; then
  say "Rust not installed — the native window is unavailable; ./dev.sh will fall"
  say "back to Vite in a browser. Re-run with --with-rust, or see https://rustup.rs."
fi

# --- done ------------------------------------------------------------------
echo
say "Setup complete. Start Eva with:  ./dev.sh"
[ "$WITH_VOICE" -eq 0 ] && say "Voice was skipped; re-run without --no-voice to enable it."
