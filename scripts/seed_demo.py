#!/usr/bin/env python3
"""Seed ~3 weeks of believable, backdated demo data for the mood chart (Phase 12).

What it writes, every row marked ``is_seeded = 1``:
  * ``entries``      — one backdated journal entry per seeded day (L0 index row).
  * ``extractions``  — a matching ``done`` extraction (mood, emotions, themes,
                       summary) so the chart has a hover summary and the data
                       looks like it came through the real pipeline.
  * ``mood_series``  — the denormalised point the chart actually reads.
  * ``journals``     — (best-effort) the summary embedded into ChromaDB with
                       ``is_seeded=True``, so the Phase-11 recall filter
                       (``is_seeded=False``) provably excludes it. If the embedding
                       model isn't present, this step is skipped with a warning —
                       the chart works regardless; only the recall-exclusion demo
                       needs the vectors.

Two deliberate design choices:

  * **Seed is DB-only, not L0 Markdown.** The plan (Phase 12) lists the seed as
    "entries + extractions + mood_series rows". Synthetic demo data is *not* the
    user's real life, so it does not earn a place in the L0 source-of-truth vault;
    keeping it out of Markdown also makes a re-seed perfectly clean (see next).
  * **Safe to re-run on a vault with real entries.** It only ever deletes and
    rewrites ``is_seeded = 1`` rows; ``is_seeded = 0`` real data is never touched.
    ``ON DELETE CASCADE`` from ``entries`` clears the seeded extractions and
    mood_series automatically.

The chart-shaping touches you can see in the data below: a believable mood arc
(rough patch → recovery), two skipped days (natural gaps), and one entry whose
mood is NULL — proving the chart draws a *gap* there, never a zero.

Usage (from the repo root or anywhere):
    backend/.venv/bin/python scripts/seed_demo.py
    backend/.venv/bin/python scripts/seed_demo.py --no-embed   # skip ChromaDB
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Run from anywhere: make the backend package importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from memory import db  # noqa: E402
from memory import graph as graph_builder  # noqa: E402

log = logging.getLogger("eva.seed")


# ─────────────────────────────────────────────────────────────────────────────
# The demo month. Each tuple is (days_ago, mood, themes, emotions, summary).
# `mood` is None for the one entry that demonstrates a NULL gap in the line.
# Days that are simply absent (e.g. 18, 12) are the natural gaps between dots.
# ─────────────────────────────────────────────────────────────────────────────
def _emo(*pairs: tuple[str, float]) -> list[dict]:
    return [{"name": name, "intensity": intensity} for name, intensity in pairs]


SEED_DAYS: list[tuple[int, int | None, list[str], list[dict], str]] = [
    (21, -2, ["work", "stress"], _emo(("anxiety", 0.6), ("frustration", 0.5)),
     "Started the week already behind. The new project scope keeps shifting and I felt anxious all day, like I'm running to stand still."),
    (20, -3, ["work", "conflict"], _emo(("anger", 0.6), ("shame", 0.4)),
     "Snapped at Daniel in the planning meeting over something small. I could see it land wrong and I haven't apologised yet. Sat with that all evening."),
    (19, -1, ["health", "running"], _emo(("calm", 0.5), ("fatigue", 0.4)),
     "Forced myself out for a slow run by the river after work. Didn't fix anything but my head was quieter for an hour, which was enough."),
    # (18) skipped — a gap.
    (17, -4, ["work", "self-doubt"], _emo(("anxiety", 0.8), ("sadness", 0.6)),
     "Lowest point in a while. Missed a deadline I'd promised and spent the night convinced I'm not good at this. Couldn't sleep."),
    (16, -2, ["faith", "discipline"], _emo(("calm", 0.4), ("hope", 0.3)),
     "Prayed fajr for the first time in weeks. Small thing, but it felt like reaching for solid ground. Want to keep this going."),
    (15, None, ["rest"], _emo(("tired", 0.5)),
     "A quiet, blurry day. Didn't write much and honestly couldn't name how I felt — somewhere in the grey middle."),
    (14, 0, ["friendship", "repair"], _emo(("relief", 0.5), ("calm", 0.4)),
     "Finally talked to Daniel properly. Apologised, he was generous about it. The knot I'd carried for a week loosened. We're okay."),
    (13, 1, ["running", "health"], _emo(("pride", 0.5), ("calm", 0.5)),
     "Third run this week — a real one, no walking breaks. Starting to feel like the version of me who keeps promises to himself."),
    # (12) skipped — a gap.
    (11, 2, ["work", "progress"], _emo(("satisfaction", 0.6), ("calm", 0.5)),
     "Shipped the piece that had been hanging over me. The relief was disproportionate. Reminded myself that the dread is always worse than the doing."),
    (10, 1, ["faith", "discipline"], _emo(("calm", 0.5), ("gratitude", 0.4)),
     "Fajr again, four days running now. Read a few pages after instead of reaching for my phone. Tiny, deliberate choices stacking up."),
    (9, -1, ["family", "tension"], _emo(("frustration", 0.5), ("guilt", 0.4)),
     "Tense call with my mother about visiting. Old script, same lines. I kept my temper this time, which I'm counting as a win."),
    (8, 2, ["running", "health"], _emo(("joy", 0.6), ("pride", 0.5)),
     "Longest run yet, out past the bridge and back. Felt strong. Food tasted better, slept like a stone. The body keeps the score both ways."),
    (7, 1, ["work", "calm"], _emo(("calm", 0.6), ("focus", 0.5)),
     "Steady, unremarkable day at work and I'm learning to value that. Not every day needs to be a battle. Closed the laptop on time."),
    (6, 3, ["friendship", "joy"], _emo(("joy", 0.7), ("gratitude", 0.6)),
     "Dinner with Daniel and a couple of friends. Laughed properly for the first time in ages. Grateful the friendship survived my worse week."),
    (5, 2, ["faith", "discipline"], _emo(("calm", 0.6), ("hope", 0.5)),
     "A full week of fajr. It's becoming less of a fight and more of a rhythm. I like who I am at 5am better than who I am at 11pm."),
    (4, 0, ["work", "stress"], _emo(("anxiety", 0.5), ("fatigue", 0.4)),
     "New deadline announced and I felt the old anxiety reach for me. But I noticed it this time instead of drowning in it. Went for a run instead of spiralling."),
    (3, 2, ["health", "running"], _emo(("calm", 0.5), ("pride", 0.5)),
     "Run, prayer, early night — the loop that seems to hold me together. Nothing dramatic to report, which is itself the report."),
    (2, 3, ["growth", "reflection"], _emo(("gratitude", 0.7), ("calm", 0.6)),
     "Looked back at where my head was three weeks ago and barely recognised it. Same problems, mostly — but I'm meeting them differently."),
    (1, 2, ["faith", "calm"], _emo(("calm", 0.6), ("contentment", 0.5)),
     "Good day. Quiet, full, enough. Trying to remember this feeling for the next rough patch, because there's always a next one."),
]


def _clear_seed(conn) -> int:
    """Delete every seeded row and return how many seed entries were removed.

    Only ``is_seeded = 1`` rows are touched — real data (``is_seeded = 0``) is
    untouched. Deleting the ``entries`` rows cascades to their extractions and
    mood_series via ``ON DELETE CASCADE``, so this one statement clears all three
    tables' seed rows.
    """
    removed = conn.execute("SELECT COUNT(*) FROM entries WHERE is_seeded = 1").fetchone()[0]
    conn.execute("DELETE FROM entries WHERE is_seeded = 1")
    conn.commit()
    return removed


def _insert_seed_entry(
    conn,
    *,
    entry_date: str,
    created_at: str,
    text: str,
    mood: int | None,
    emotions: list[dict],
    themes: list[str],
    summary: str,
) -> str:
    """Write one seeded entry + its done extraction + its mood point. Returns id.

    Mirrors what the real capture+extraction pipeline writes for a finished entry,
    but synchronously and with ``is_seeded=1`` on the rows that carry that flag
    (``entries`` and ``mood_series``; ``extractions`` has no such column by design).
    """
    entry_id = str(uuid.uuid4())
    db.insert_entry(
        conn,
        id=entry_id, date=entry_date, type="journal",
        text=text, word_count=len(text.split()),
        created_at=created_at, is_seeded=True,
    )
    db.create_pending_extraction(conn, entry_id)
    db.finalize_extraction(
        conn, entry_id,
        mood=mood, emotions=emotions, entities=[], themes=themes,
        events=[], stated_goals=[], behaviors=[], decisions=[],
        open_loops=[], self_judgments=[], summary=summary, extracted_at=created_at,
    )
    db.upsert_mood_series(
        conn, entry_id=entry_id, date=entry_date,
        mood=mood, emotions=emotions, is_seeded=True,
    )
    return entry_id


def _embed_seeded(seeded: list[dict]) -> int:
    """Embed seeded summaries into the journals collection with is_seeded=True.

    Best-effort: if the embedding model isn't present (offline, never downloaded),
    we log and return 0 rather than fail the seed — the mood chart needs no
    vectors. When it does run, it first clears any prior seeded vectors so a
    re-seed doesn't accumulate duplicates, then embeds each summary tagged
    ``is_seeded=True`` so journal recall (which filters ``is_seeded=False``)
    provably never surfaces seed data.
    """
    try:
        from memory import vector

        col = vector._get_collection()  # noqa: SLF001 — internal, but the seam we need
        col.delete(where={"is_seeded": True})
        for s in seeded:
            vector.embed_summary(
                entry_id=s["entry_id"], date=s["date"], summary=s["summary"],
                mood=s["mood"], themes=s["themes"], is_seeded=True,
            )
        return len(seeded)
    except Exception as exc:  # noqa: BLE001 — embedding is a bonus, never required
        log.warning("skipped embedding seeded summaries (chart still works): %s", exc)
        return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Seed ~3 weeks of demo mood data.")
    parser.add_argument(
        "--no-embed", action="store_true",
        help="Skip embedding seeded summaries into ChromaDB (DB rows only).",
    )
    args = parser.parse_args()

    conn = db.get_or_create_db()
    try:
        removed = _clear_seed(conn)
        if removed:
            log.info("cleared %d existing seed entr(y/ies)", removed)

        today = date.today()
        seeded: list[dict] = []
        for days_ago, mood, themes, emotions, summary in SEED_DAYS:
            d = today - timedelta(days=days_ago)
            entry_date = d.isoformat()
            # A late-evening timestamp reads like a real journaling habit and keeps
            # same-day ordering stable. Backdated, so it never collides with today.
            created_at = datetime.combine(d, time(21, 30)).isoformat(timespec="seconds")
            text = f"(seed) {summary}"
            entry_id = _insert_seed_entry(
                conn, entry_date=entry_date, created_at=created_at, text=text,
                mood=mood, emotions=emotions, themes=themes, summary=summary,
            )
            seeded.append({
                "entry_id": entry_id, "date": entry_date, "summary": summary,
                "mood": mood, "themes": themes,
            })

        moods = [s["mood"] for s in seeded if s["mood"] is not None]
        log.info(
            "seeded %d entr(y/ies) across %s … %s (mood %d..%d, %d NULL-mood gap)",
            len(seeded), seeded[0]["date"], seeded[-1]["date"],
            min(moods), max(moods), len(seeded) - len(moods),
        )

        # Phase 14: derive the seeded knowledge graph from the same extractions
        # (themes/emotions + a curated lexicon over the entry text). All rows are
        # is_seeded=1 so the future L4 builder can prune them. This is honest
        # co-occurrence, not invented links — every node/edge cites real entries.
        n_nodes, n_edges = graph_builder.store_seed_graph(conn)
        log.info("seeded knowledge graph: %d node(s), %d edge(s)", n_nodes, n_edges)
    finally:
        conn.close()

    if not args.no_embed:
        embedded = _embed_seeded(seeded)
        if embedded:
            log.info("embedded %d seeded summar(y/ies) into journals (is_seeded=True)", embedded)

    print(
        "\nDone. The Insights mood chart will show the seed month when fetched with "
        "?include_seeded=true (the UI's demo toggle). Real entries (is_seeded=0) are "
        "untouched, and seed data never surfaces in recall."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
