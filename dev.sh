#!/usr/bin/env bash
# Eva — dev launcher.
#
# Starts the WHOLE stack for local development with one command:
#   1. The native llama.cpp `llama-server` model server on 127.0.0.1:11500
#      (launched & supervised by the backend via EVA_START_LLAMA=1 — §4).
#   2. The Python FastAPI backend (uvicorn) on 127.0.0.1:8000.
#   3. The frontend — the Tauri native shell if Rust is installed (`tauri dev`,
#      which itself starts Vite), otherwise the bare Vite dev server in a
#      browser tab at http://localhost:1420.
#
# Cross-platform: works on macOS/Linux and on Windows via Git Bash. It reuses an
# existing backend/.venv, else falls back to a system Python that already has the
# deps, else creates the venv on first run. Ctrl-C tears everything down.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# UTF-8 output — the download/status scripts print non-ASCII (e.g. "→"), which
# crashes on Windows' default cp1252 console without this.
export PYTHONIOENCODING="utf-8"
export PYTHONUTF8="1"

# --- pick the backend Python interpreter -----------------------------------
# Preference order:
#   1. An existing backend/.venv  (Windows: Scripts/, Unix: bin/)
#   2. A system python that ALREADY imports the deps (no venv rebuild)
#   3. Create backend/.venv and install core requirements (first-run)
VENV_WIN="$ROOT/backend/.venv/Scripts/python.exe"
VENV_NIX="$ROOT/backend/.venv/bin/python"

system_python() {
  command -v python3 2>/dev/null || command -v python 2>/dev/null || true
}

has_deps() {  # $1 = python interpreter
  "$1" - <<'PY' >/dev/null 2>&1
import importlib.util as u
import sys
for m in ("fastapi", "uvicorn", "chromadb", "fastembed"):
    if u.find_spec(m) is None:
        sys.exit(1)
PY
}

if [ -x "$VENV_WIN" ]; then
  PYBIN="$VENV_WIN"
elif [ -x "$VENV_NIX" ]; then
  PYBIN="$VENV_NIX"
else
  SYS_PY="$(system_python)"
  if [ -n "$SYS_PY" ] && has_deps "$SYS_PY"; then
    echo "[dev] using system Python ($SYS_PY) — deps already present, skipping venv."
    PYBIN="$SYS_PY"
  else
    echo "[dev] creating backend venv (first run)…"
    [ -n "$SYS_PY" ] || { echo "[dev] ERROR: no python/python3 on PATH." >&2; exit 1; }
    "$SYS_PY" -m venv backend/.venv
    if [ -x "$VENV_WIN" ]; then PYBIN="$VENV_WIN"; else PYBIN="$VENV_NIX"; fi
    "$PYBIN" -m pip install --upgrade pip >/dev/null
    "$PYBIN" -m pip install -r backend/requirements.txt
    echo "[dev] (voice is optional: pip install -r backend/requirements-voice.txt)"
  fi
fi

# --- readiness feedback: model + llama-server binary -----------------------
# Non-fatal — a missing GGUF or binary degrades gracefully to first-run setup;
# the backend never crashes. We just tell the developer what to expect.
"$PYBIN" - <<'PY' || true
import sys
sys.path.insert(0, "backend")
try:
    from llm import server as s
    print(f"[dev] model present : {s.model_present()}  ({s.configured_model_path()})")
    print(f"[dev] llama-server  : {s.resolve_llama_server() or 'NOT FOUND — run local AI setup / brew install llama.cpp'}")
except Exception as e:  # noqa: BLE001 - readiness print must never block startup
    print(f"[dev] (model/binary check skipped: {e})")
PY

# --- backend (+ model server sidecar) --------------------------------------
echo "[dev] starting backend on http://127.0.0.1:8000  (model server on :11500) …"
# EVA_START_LLAMA=1 → the backend launches & supervises the llama.cpp
# `llama-server` binary as a sidecar. server.py finds it at
# bin/llama.cpp/<os>/llama-server(.exe), on PATH, or via $EVA_LLAMA_SERVER_BIN.
( cd backend && EVA_START_LLAMA=1 exec "$PYBIN" -m uvicorn app:app \
    --host 127.0.0.1 --port 8000 --reload ) &
BACKEND_PID=$!

cleanup() {
  echo
  echo "[dev] shutting down…"
  kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- frontend --------------------------------------------------------------
cd ui
if [ ! -d node_modules ]; then
  echo "[dev] installing frontend deps (first run)…"
  npm install
fi

if command -v cargo >/dev/null 2>&1; then
  echo "[dev] starting Tauri shell (cargo found) …"
  npm run tauri dev &
  FRONTEND_PID=$!
else
  echo "[dev] cargo/rustc not found — starting Vite only (open http://localhost:1420)."
  echo "[dev] install Rust (https://rustup.rs) then re-run to get the native window."
  npm run dev &
  FRONTEND_PID=$!
fi

wait "$FRONTEND_PID"
