#!/usr/bin/env python3
"""One-time, out-of-band download of the bge-small-en-v1.5 embedding model.

Run this ONCE during setup. It intentionally does NOT import Eva's net-guard, so
the model can be fetched from HuggingFace. After this completes, the model is
cached locally and the backend runs fully offline (memory/vector.py forces
HF_HUB_OFFLINE at runtime), so the privacy guard is never tripped at runtime.

This mirrors CLAUDE.md rule 4: "Only the first-run model/voice download is
allowed." In the shipped app this download is driven by the Phase-10 first-run
setup screen; this script is the developer/CI equivalent.

    backend/.venv/bin/python scripts/download_embed_model.py

The model is written to ``<vault>/models/fastembed`` — the SAME persistent cache
the runtime reads (memory/vector.py). fastembed's own default is the volatile
system temp dir, which macOS purges; pinning it to the vault keeps the model put
so corpus upload / recall don't break after a reboot.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Resolve the vault path WITHOUT importing memory.vector — that module sets
# HF_HUB_OFFLINE=1 at import, which would force this very download offline and
# fail. memory/__init__ (vault_dir) sets nothing, so it is safe to import.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from memory import vault_dir  # noqa: E402

MODEL = "BAAI/bge-small-en-v1.5"


def main() -> int:
    cache = vault_dir() / "models" / "fastembed"
    cache.mkdir(parents=True, exist_ok=True)
    print(f"Downloading embedding model {MODEL} → {cache} (one-time)…")
    from fastembed import TextEmbedding

    emb = TextEmbedding(model_name=MODEL, cache_dir=str(cache))
    # Force the weights to materialise by embedding a probe string.
    vec = next(iter(emb.embed(["hello world"])))
    print(f"OK — model ready, embedding dim = {len(vec)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
