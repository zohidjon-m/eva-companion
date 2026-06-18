#!/usr/bin/env bash
# Eva — demo-day runner (Phase 15).
#
# The single command for running the live demo:
#   1. (optional, --reset) reset the vault to the known demo state.
#   2. Run the failure drills and print the PASS/FAIL report.
#   3. Launch the app: backend on :8000 (with the model so chat/voice work) and
#      the frontend on http://localhost:1420.
#
# Then follow DEMO_SCRIPT.md beat by beat. Ctrl-C tears everything down.
#
# Usage:
#   ./run_demo.sh            # drills + launch (vault left as-is)
#   ./run_demo.sh --reset    # demo_reset.py --yes first, then drills + launch
#   ./run_demo.sh --reset --skip-drills
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY=backend/.venv/bin/python

DO_RESET=0
DO_DRILLS=1
for arg in "$@"; do
  case "$arg" in
    --reset) DO_RESET=1 ;;
    --skip-drills) DO_DRILLS=0 ;;
    *) echo "unknown arg: $arg (use --reset / --skip-drills)" >&2; exit 2 ;;
  esac
done

if [ "$DO_RESET" -eq 1 ]; then
  echo "── Resetting to the demo state ─────────────────────────────────────────"
  "$PY" scripts/demo_reset.py --yes
  echo
fi

if [ "$DO_DRILLS" -eq 1 ]; then
  echo "── Failure drills ──────────────────────────────────────────────────────"
  # Don't let a drill failure abort the launch — print it and let the operator
  # decide. (set -e would otherwise kill the script on a non-zero drill exit.)
  "$PY" scripts/demo_drills.py || echo "[demo] WARNING: a drill did not pass — review above before presenting."
  echo
fi

echo "── Launching the app (backend :8000 + frontend :1420) ──────────────────"
# The backend launches the model server itself. It prefers the native llama.cpp
# `llama-server` binary (brew install llama.cpp); if that's absent it falls back
# to any Python with llama-cpp-python. No interpreter probe needed here anymore.
if ! command -v llama-server >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/llama-server ]; then
  echo "[demo] NOTE: 'llama-server' not found — install it for fast chat:"
  echo "[demo]       brew install llama.cpp   (chat falls back to llama-cpp-python if present)"
fi

# Free the port if a previous run left a backend behind.
pkill -f "uvicorn app:app" 2>/dev/null || true; sleep 1

echo "[demo] backend on http://127.0.0.1:8000 (model server: llama-server) …"
( cd backend && EVA_START_LLAMA=1 \
    exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 ) &
BACKEND_PID=$!

cleanup() {
  echo
  echo "[demo] shutting down…"
  kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd ui
[ -d node_modules ] || npm install
echo "[demo] frontend — open http://localhost:1420  (follow DEMO_SCRIPT.md)"
if command -v cargo >/dev/null 2>&1; then
  npm run tauri dev &
else
  echo "[demo] cargo not found — running the Vite dev server (browser) instead of the native shell."
  npm run dev &
fi
FRONTEND_PID=$!
wait "$FRONTEND_PID"
