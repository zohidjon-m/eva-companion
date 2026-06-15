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
"""
from __future__ import annotations

import sys

MODEL = "BAAI/bge-small-en-v1.5"


def main() -> int:
    print(f"Downloading embedding model {MODEL} (one-time)…")
    from fastembed import TextEmbedding

    emb = TextEmbedding(model_name=MODEL)
    # Force the weights to materialise by embedding a probe string.
    vec = next(iter(emb.embed(["hello world"])))
    print(f"OK — model ready, embedding dim = {len(vec)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
