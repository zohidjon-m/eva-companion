#!/usr/bin/env bash
# Eva — Phase 14 verification + run helper.
#
# 1. Seeds ~3 weeks of demo data + the seeded knowledge graph (is_seeded=1).
# 2. Validates GET /insights/graph against EVA_MEMORY_ARCHITECTURE §7.4.
# 3. Runs the Phase-14 backend tests.
# 4. Launches the full app: backend on :8000 (with the model so chat works too)
#    and the frontend on http://localhost:1420.
#
# Then open http://localhost:1420 → Insights → "Connections" / "Looking back",
# and turn on the "Demo data" toggle. Ctrl-C tears everything down.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY=backend/.venv/bin/python

echo "── 1/4  Seeding demo data + knowledge graph ────────────────────────────"
"$PY" scripts/seed_demo.py --no-embed

echo
echo "── 2/4  Validating /insights/graph against §7.4 ────────────────────────"
"$PY" scripts/validate_graph.py

echo
echo "── 3/4  Running Phase-14 backend tests ─────────────────────────────────"
( cd backend && .venv/bin/python -m pytest -q \
    tests/test_insights_graph.py tests/test_insights_growth.py \
    tests/test_db_schema.py tests/test_seed_demo.py tests/test_insights_mood.py )

echo
echo "── 4/4  Launching the app (backend :8000 + frontend :1420) ─────────────"
# The model server needs an interpreter with llama-cpp-python; the backend's own
# .venv doesn't have it (here it lives in anaconda). Probe candidates and pick the
# first that can actually import llama_cpp — voice (kokoro/faster-whisper) runs
# in-process from .venv and needs no special interpreter.
LLAMA_PY=""
for p in /opt/anaconda3/bin/python "$HOME/anaconda3/bin/python" \
         "$HOME/opt/anaconda3/bin/python" "$HOME/miniconda3/bin/python" \
         /opt/homebrew/bin/python3 "$(command -v python3 || true)"; do
  if [ -x "$p" ] && "$p" -c "import llama_cpp" >/dev/null 2>&1; then
    LLAMA_PY="$p"; break
  fi
done
if [ -z "$LLAMA_PY" ]; then
  echo "[run] WARNING: no python with llama-cpp-python found — chat will be offline,"
  echo "[run]          but Insights (graph/growth/mood) and voice still work."
fi

# Free the port if a previous run left a backend behind.
pkill -f "uvicorn app:app" 2>/dev/null || true; sleep 1

echo "[run] backend on http://127.0.0.1:8000 (model via ${LLAMA_PY:-none}) …"
( cd backend && EVA_START_LLAMA=1 EVA_LLAMA_PYTHON="$LLAMA_PY" \
    exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --reload ) &
BACKEND_PID=$!

cleanup() {
  echo
  echo "[run] shutting down…"
  kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd ui
[ -d node_modules ] || npm install
echo "[run] frontend — open http://localhost:1420"
npm run dev &
FRONTEND_PID=$!

wait "$FRONTEND_PID"
