#!/usr/bin/env python3
"""Quick corpus retrieval probe — Phase 6 check.

Embeds a query and prints the nearest chunks from the ``corpus`` ChromaDB
collection, with their source file and page/section. This is the plan's
verification that "a quick script queries the corpus collection for a phrase from
the book and gets the right chunk back" — and a handy debugging tool while
building Phase 7's grounded retrieval.

Runs fully offline (memory.vector forces HF_HUB_OFFLINE). Point it at a vault
with EVA_VAULT_DIR if you're not using the default local_vault/.

    backend/.venv/bin/python scripts/query_corpus.py "a phrase from your book"
    backend/.venv/bin/python scripts/query_corpus.py -k 3 "another phrase"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the backend package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from memory import vector  # noqa: E402  (after sys.path tweak)


def main() -> int:
    parser = argparse.ArgumentParser(description="Query Eva's corpus collection.")
    parser.add_argument("query", help="The phrase or question to search for.")
    parser.add_argument("-k", type=int, default=5, help="How many chunks to return.")
    args = parser.parse_args()

    total = vector.corpus_count()
    if total == 0:
        print("The corpus collection is empty — upload a document first.")
        return 1
    print(f"Searching {total} chunks for: {args.query!r}\n")

    res = vector.query_corpus(args.query, n_results=args.k)
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]

    for i, (text, meta, dist) in enumerate(zip(docs, metas, dists), start=1):
        where = meta.get("source_file", "?")
        if meta.get("page") is not None:
            where += f", p.{meta['page']}"
        elif meta.get("section"):
            where += f" — {meta['section']}"
        snippet = " ".join(text.split())[:200]
        print(f"[{i}] distance={dist:.3f}  ({where})")
        print(f"    {snippet}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
