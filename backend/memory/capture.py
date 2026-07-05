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
        source_hash = vault.source_hash(rec.text)
        db.insert_entry(
            conn,
            id=rec.id, date=rec.date, type=rec.type,
            text=rec.text, word_count=rec.word_count, created_at=rec.created_at,
        )
        db.create_pending_extraction(conn, rec.id, source_hash=source_hash)
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
    source_hash = vault.source_hash(text)

    conn = db.get_or_create_db()
    try:
        current_status = db.current_extraction_status(conn, entry_id, source_hash)
        if current_status is not None:
            log.info("skipped extraction for unchanged entry %s", entry_id)
            return current_status
    finally:
        conn.close()

    result = await extract.extract_entry(text, call_model=call_model)

    conn = db.get_or_create_db()
    try:
        if result.status == "done":
            db.finalize_extraction(
                conn, entry_id,
                mood=result.mood, emotions=result.emotions, entities=result.entities,
                themes=result.themes, events=result.events, stated_goals=result.stated_goals,
                behaviors=result.behaviors, decisions=result.decisions,
                open_loops=result.open_loops, self_judgments=result.self_judgments,
                summary=result.summary, extracted_at=result.extracted_at,
                source_hash=source_hash,
            )
            db.replace_mood_series(
                conn, entry_id=entry_id, date=date,
                mood=result.mood, emotions=result.emotions,
            )
        else:
            db.mark_null_stored(conn, entry_id, source_hash=source_hash)
    finally:
        conn.close()

    if result.status == "done":
        try:
            from . import vector
            vector.embed_summary(
                entry_id=entry_id, date=date, summary=result.summary,
                mood=result.mood, themes=result.themes, is_seeded=False,
            )
            # L2 episodes (R4): open loops + notable events get their own vectors so
            # they're recallable on their own terms, not just via the summary. Same
            # best-effort contract — a durable entry never hinges on an embed.
            vector.embed_episodes(
                entry_id=entry_id, date=date, mood=result.mood, themes=result.themes,
                open_loops=result.open_loops, events=result.events, is_seeded=False,
            )
        except Exception as e:  # noqa: BLE001 — embedding is best-effort
            log.error("embedding failed for entry %s (entry still saved): %s", entry_id, e)

    return result.status


async def recompute_entry(
    entry_id: str,
    *,
    call_model: ModelCaller | None = None,
) -> str:
    """Synchronously rederive L1/L2 for exactly one L0 entry UID.

    R5 edits must become visible before the API returns, but they must not run a
    full rebuild. This function reads the current Markdown body, refreshes the
    single SQLite/FTS row, hash-gates extraction, replaces the single mood point,
    and updates or clears only this entry's journal/episode vectors.

    Raises:
        KeyError: if ``entry_id`` is not present in the Markdown vault.
    """
    rec = vault.find_entry(entry_id)
    if rec is None:
        raise KeyError(entry_id)

    source_hash = vault.source_hash(rec.text)
    conn = db.get_or_create_db()
    try:
        previous = db.get_entry(conn, entry_id)
        db.upsert_entry_from_l0(
            conn,
            id=rec.id,
            date=rec.date,
            type=rec.type,
            text=rec.text,
            word_count=rec.word_count,
            created_at=rec.created_at,
            is_seeded=False,
        )
        db.refresh_entry_fts(
            conn,
            entry_id,
            previous_text=previous["text"] if previous is not None else None,
        )
        current_status = db.current_extraction_status(conn, entry_id, source_hash)
        if current_status is not None:
            log.info("skipped recompute for unchanged entry %s", entry_id)
            return current_status
        db.mark_extraction_pending(conn, entry_id, source_hash=source_hash)
    finally:
        conn.close()

    result = await extract.extract_entry(rec.text, call_model=call_model)

    conn = db.get_or_create_db()
    try:
        if result.status == "done":
            db.finalize_extraction(
                conn,
                entry_id,
                mood=result.mood,
                emotions=result.emotions,
                entities=result.entities,
                themes=result.themes,
                events=result.events,
                stated_goals=result.stated_goals,
                behaviors=result.behaviors,
                decisions=result.decisions,
                open_loops=result.open_loops,
                self_judgments=result.self_judgments,
                summary=result.summary,
                extracted_at=result.extracted_at,
                source_hash=source_hash,
            )
            db.replace_mood_series(
                conn,
                entry_id=entry_id,
                date=rec.date,
                mood=result.mood,
                emotions=result.emotions,
                is_seeded=False,
            )
        else:
            db.mark_null_stored(conn, entry_id, source_hash=source_hash)
            db.delete_mood_series(conn, entry_id)
    finally:
        conn.close()

    try:
        from . import vector

        if result.status == "done":
            vector.embed_summary(
                entry_id=entry_id,
                date=rec.date,
                summary=result.summary,
                mood=result.mood,
                themes=result.themes,
                is_seeded=False,
            )
            vector.embed_episodes(
                entry_id=entry_id,
                date=rec.date,
                mood=result.mood,
                themes=result.themes,
                open_loops=result.open_loops,
                events=result.events,
                is_seeded=False,
            )
        else:
            vector.delete_entry_vectors(entry_id)
    except Exception as e:  # noqa: BLE001 - L0/L1 are already durable
        log.error("vector refresh failed for entry %s (L1 still recomputed): %s", entry_id, e)

    # The entry's body changed (we passed the hash gate above), so its extraction
    # was rewritten — whether that succeeded (`done`) or degraded to `null_stored`,
    # any L3 claim resting on this entry may no longer hold. Flag them either way.
    _flag_claims_for_revalidation(entry_id)

    log.info("recomputed entry %s with status %s", entry_id, result.status)
    return result.status


def _flag_claims_for_revalidation(entry_id: str) -> None:
    """Flag L3 claims citing ``entry_id`` for re-audit after that entry was edited.

    When an entry is edited its extraction changes, so any profile claim resting on
    it may no longer be supported. We don't re-judge here — the claim is revalidated
    on the next consolidation (R8). We only set ``needs_revalidation`` on the
    affected claims (IMPLEMENTATION_PLAN_V2 Phase 7.5 self-heal hook / ADR-001 item
    4) so that audit knows where to look. Best-effort: L0/L1 are already durable, and
    a missing or unreadable profile must never break the recompute path.
    """
    try:
        from . import profile as profile_mod

        prof = profile_mod.get_profile()
        if prof is None:
            return
        flagged = 0
        for claims in (prof.goals, prof.patterns, prof.open_loops,
                       prof.relationships, prof.watch_list):
            for claim in claims:
                evidence = claim.get("evidence")
                if isinstance(evidence, list) and entry_id in evidence:
                    claim["needs_revalidation"] = True
                    flagged += 1
        if flagged:
            profile_mod.save_profile(prof)
            log.info("flagged %d L3 claim(s) for revalidation after editing %s", flagged, entry_id)
    except Exception as e:  # noqa: BLE001 — self-heal flagging is best-effort
        log.error("revalidation flagging failed for entry %s (recompute still done): %s", entry_id, e)
