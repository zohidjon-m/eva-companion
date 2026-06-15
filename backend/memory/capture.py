"""Capture pipeline — the one path every saved entry flows through.

This is the model-agnostic seam the plan calls for: chat turns, journal entries,
and (later) voice transcripts all call :func:`capture_entry` to persist, then
:func:`run_extraction_and_embed` to enrich in the background. Phase 4's `/chat`
WebSocket and Phase 5's journal save will both reuse these two functions rather
than re-implementing capture.

Ordering matters and is deliberate:
1. Write L0 Markdown (the source of truth) — if this fails, nothing else runs.
2. Index into L1 SQLite + open a ``pending`` extraction row.
3. (background) Run extraction; on success finalise L1, copy mood, embed into L2.

Steps 1–2 are synchronous and fast so the user's save is instant; step 3 is
backgrounded so a slow or down model never blocks capture. A failed extraction
degrades to ``null_stored`` — never to a lost entry.
"""

from __future__ import annotations

import logging

from . import db, extract, vault
from .extract import ModelCaller

log = logging.getLogger("eva.memory.capture")


def capture_entry(text: str, entry_type: str) -> vault.EntryRecord:
    """Persist one entry to L0 (Markdown) and L1 (SQLite index + pending row).

    Returns the :class:`~memory.vault.EntryRecord` so the caller can schedule
    background extraction with the entry's id/text/date. Does not touch the model.
    """
    rec = vault.save_entry(text, entry_type)  # L0 first — source of truth.

    conn = db.get_or_create_db()
    try:
        db.insert_entry(
            conn,
            id=rec.id, date=rec.date, type=rec.type,
            text=rec.text, word_count=rec.word_count, created_at=rec.created_at,
        )
        db.create_pending_extraction(conn, rec.id)
    finally:
        conn.close()

    log.info("captured %s entry %s", entry_type, rec.id)
    return rec


async def run_extraction_and_embed(
    entry_id: str,
    text: str,
    date: str,
    *,
    call_model: ModelCaller | None = None,
) -> str:
    """Background job: extract an entry, then finalise L1 and embed into L2.

    Opens its own SQLite connection (background tasks may run off the main thread,
    and sqlite3 connections are not shareable across threads). Returns the final
    extraction status (``done`` or ``null_stored``) for logging/tests.

    Embedding is best-effort: the entry and its extraction are already durable, so
    an embedding failure is logged but does not roll anything back.
    """
    result = await extract.extract_entry(text, call_model=call_model)

    conn = db.connect()
    try:
        if result.status == "done":
            db.finalize_extraction(
                conn, entry_id,
                mood=result.mood, emotions=result.emotions, entities=result.entities,
                themes=result.themes, events=result.events, stated_goals=result.stated_goals,
                behaviors=result.behaviors, decisions=result.decisions,
                open_loops=result.open_loops, self_judgments=result.self_judgments,
                summary=result.summary, extracted_at=result.extracted_at,
            )
            db.upsert_mood_series(
                conn, entry_id=entry_id, date=date,
                mood=result.mood, emotions=result.emotions,
            )
        else:
            db.mark_null_stored(conn, entry_id)
    finally:
        conn.close()

    if result.status == "done":
        try:
            from . import vector
            vector.embed_summary(
                entry_id=entry_id, date=date, summary=result.summary,
                mood=result.mood, themes=result.themes, is_seeded=False,
            )
        except Exception as e:  # noqa: BLE001 — embedding is best-effort
            log.error("embedding failed for entry %s (entry still saved): %s", entry_id, e)

    return result.status
