#!/usr/bin/env bash
# Packaging spike build — freeze hello.py into a single binary and prove it runs.
#
# Proves the Tauri-sidecar mechanism end to end on macOS: PyInstaller onefile
# build -> launch -> HTTP response. Run from anywhere; paths are resolved here.
#
# Usage:
#   packaging/spike/build_spike.sh          # uses python3 on PATH
#   (or activate the backend venv first to reuse its pyinstaller)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PY="${PYTHON:-python3}"
echo "[spike] python: $($PY --version)"

# Ensure pyinstaller + fastapi/uvicorn are importable in this interpreter.
$PY -m PyInstaller --version >/dev/null 2>&1 || {
  echo "[spike] installing pyinstaller fastapi uvicorn into current interpreter…"
  $PY -m pip install --quiet pyinstaller fastapi "uvicorn[standard]"
}

echo "[spike] building onefile binary…"
$PY -m PyInstaller \
  --onefile \
  --name eva-backend-spike \
  --collect-submodules uvicorn \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  --clean --noconfirm \
  hello.py

BIN="$HERE/dist/eva-backend-spike"
echo "[spike] built: $BIN"

echo "[spike] running frozen binary --selfcheck…"
"$BIN" --selfcheck

echo "[spike] PASS — frozen FastAPI sidecar launches and answers HTTP."
