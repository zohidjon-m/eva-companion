#!/usr/bin/env bash
# Eva — dev launcher (Phase 0).
#
# Starts both halves of the app for local development:
#   1. The Python FastAPI backend (uvicorn, 127.0.0.1:8000)
#   2. The frontend — the Tauri shell if Rust is installed (`tauri dev`, which
#      itself starts the Vite dev server), otherwise the bare Vite dev server
#      in a browser tab at http://localhost:1420.
#
# Per EVA_SYSTEM_DESIGN §4, the shell owns the backend as a sidecar. In dev we
# approximate that with this script ("a dev.sh that starts both is fine for
# now"). Ctrl-C tears both down.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --- backend ---------------------------------------------------------------
if [ ! -d backend/.venv ]; then
  echo "[dev] creating backend venv (first run)…"
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --upgrade pip >/dev/null
  backend/.venv/bin/pip install -r backend/requirements.txt
fi

echo "[dev] starting backend on http://127.0.0.1:8000 …"
# EVA_START_LLAMA=1 → the backend launches & supervises the model server
# (python -m llama_cpp.server on :11500) as a sidecar (EVA_SYSTEM_DESIGN §4).
# A missing GGUF degrades gracefully to first-run setup; it never crashes.
( cd backend && EVA_START_LLAMA=1 exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --reload ) &
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
