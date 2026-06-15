#!/usr/bin/env bash
# Eva — first-run model download (macOS / Apple Silicon).
#
# Downloads the quantized Gemma 4 E2B GGUF into models/. This is the ONLY
# permitted outbound network call in the whole app (CLAUDE.md rule 4); everything
# else runs fully offline. Idempotent: if the file is already present it does
# nothing.
#
# We standardise on the unsloth QAT build, quant UD-Q4_K_XL (~2.6 GB) — the same
# file the backend and Phase-2 extraction were validated against. Do NOT swap in
# a different quant; the filename is referenced by name in backend/llm/server.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_DIR="$ROOT/models"
MODEL_FILE="gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
HF_REPO="unsloth/gemma-4-E2B-it-qat-GGUF"

mkdir -p "$MODEL_DIR"

if [ -s "$MODEL_DIR/$MODEL_FILE" ]; then
  echo "[download] $MODEL_FILE already present in models/ — nothing to do."
  exit 0
fi

echo "[download] Fetching $MODEL_FILE from $HF_REPO (~2.6 GB)…"
echo "[download] This is the only network access Eva ever makes."

# Prefer the huggingface CLI (ships with huggingface_hub, already a dep). It
# resolves to huggingface.co + its CDN; allow-list those if you front this with
# the net guard (EVA_ALLOW_HOST=huggingface.co).
if command -v hf >/dev/null 2>&1; then
  hf download "$HF_REPO" "$MODEL_FILE" --local-dir "$MODEL_DIR"
elif command -v huggingface-cli >/dev/null 2>&1; then
  huggingface-cli download "$HF_REPO" "$MODEL_FILE" --local-dir "$MODEL_DIR"
else
  echo "[download] huggingface CLI not found; falling back to curl." >&2
  curl -L --fail -o "$MODEL_DIR/$MODEL_FILE" \
    "https://huggingface.co/$HF_REPO/resolve/main/$MODEL_FILE?download=true"
fi

if [ -s "$MODEL_DIR/$MODEL_FILE" ]; then
  echo "[download] Done: $MODEL_DIR/$MODEL_FILE"
else
  echo "[download] FAILED: $MODEL_FILE was not written." >&2
  exit 1
fi
