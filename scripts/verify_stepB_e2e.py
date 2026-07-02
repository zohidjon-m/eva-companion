#!/usr/bin/env python3
"""Phase 2 Step B end-to-end check against a REAL llama-server on :11500.

Mirrors the plan's Phase-2 acceptance test: capture 3 entries, run real
extraction over the OpenAI HTTP endpoint, and confirm the full chain —
Markdown (L0) + entries/extractions (L1) + journals vectors (L2). Requires
llama-server running on 127.0.0.1:11500.

Run from backend/:  .venv/bin/python ../scripts/verify_stepB_e2e.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ["EVA_VAULT_DIR"] = tempfile.mkdtemp(prefix="eva_e2e_")

from memory import capture, db, vault, vector  # noqa: E402

ENTRIES = [
    ("chat", "Rough day at work — I snapped at my teammate Dana during standup and felt awful about it."),
    ("chat", "Went for a long run by the river after work and it completely reset my head. Grateful for that."),
    ("journal", "I keep saying I want to read more but I doom-scroll every night instead. Starting tomorrow: 20 pages before bed, no phone."),
]


async def main() -> int:
    recs = []
    for typ, text in ENTRIES:
        rec = capture.capture_entry(text, typ)
        status = await capture.run_extraction_and_embed(rec.id, rec.text, rec.date)
        print(f"  [{typ}] {rec.id[:8]} → {status}")
        recs.append(rec)

    conn = db.connect()
    n_entries = db.count_entries(conn)
    done = conn.execute("SELECT COUNT(*) FROM extractions WHERE extraction_status='done'").fetchone()[0]
    nulls = conn.execute("SELECT COUNT(*) FROM extractions WHERE extraction_status='null_stored'").fetchone()[0]

    print("\n--- results ---")
    print(f"entries rows         : {n_entries}")
    print(f"extractions 'done'   : {done}")
    print(f"extractions null     : {nulls}")
    print(f"journals vectors     : {vector.count()}")

    # Show one real extraction in full so the JSON quality is visible.
    row = db.get_extraction(conn, recs[0].id)
    print("\n--- sample extraction (entry 1) ---")
    for k in ("mood", "emotions", "entities", "themes", "events", "behaviors",
              "decisions", "open_loops", "self_judgments", "summary"):
        print(f"  {k}: {row[k]}")

    # Markdown has all three turns.
    md_days = {r.date for r in recs}
    md_turns = sum(len(vault.read_day(d)) for d in md_days)
    print(f"\nmarkdown turns on disk: {md_turns}")

    # Recall works on real vectors.
    r = vector.recall("feeling stressed about coworkers", n_results=1)
    print(f"recall top hit themes : {r['metadatas'][0][0].get('themes')!r}")
    conn.close()

    ok = (n_entries == 3 and done == 3 and nulls == 0 and vector.count() == 3 and md_turns == 3)
    print("\n" + ("PASS ✅" if ok else "INCOMPLETE ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
