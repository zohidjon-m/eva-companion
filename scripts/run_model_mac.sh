#!/usr/bin/env bash
# Eva — run the model server in the foreground (manual / debugging).
#
# In normal use the backend launches and supervises the model server itself
# (set EVA_START_LLAMA=1 when starting the backend). This script is the manual
# equivalent: it runs backend/llm/server.py, which resolves an interpreter that
# has llama-cpp-python and execs the exact `python -m llama_cpp.server` command
# (the same flags as CLAUDE.md). Useful for watching the llama.cpp load log —
# look for "offloaded N/N layers to GPU" to confirm Metal offload.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Use the backend venv's python to import the server module; server.py itself
# finds an interpreter with llama_cpp (falling back to $EVA_LLAMA_PYTHON / a
# system python3) and re-execs into it.
PY="$ROOT/backend/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

exec "$PY" backend/llm/server.py
