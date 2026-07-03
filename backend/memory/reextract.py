"""R3 rebuild path: derive SQLite L1 from the Markdown vault.

The Markdown day files remain the source of truth. This module reads L0, keeps
R2's stable UID and source-hash contracts intact, and repairs the derived
SQLite tables without touching L0 or ChromaDB.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import db, extract, vault
from .extract import ModelCaller

log = logging.getLogger("eva.memory.reextract")


class ReextractError(RuntimeError):
    """Raised when L0 cannot be safely rebuilt into L1.

    R3 must not invent missing identities or guess through duplicate UIDs. Those
    are vault integrity problems that need the R2 backfill or manual inspection.
    """


@dataclass(frozen=True)
class ReextractReport:
    """Counters for one R3 re-extraction/rebuild run."""

    scanned: int
    inserted: int
    updated: int
    unchanged: int
    pruned: int
    extraction_created: int
    skipped: int
    retried: int
    done: int
    null_stored: int
    fts_rebuilt: bool


@dataclass(frozen=True)
class _L0Turn:
    id: str
    date: str
    time: str
    type: str
    text: str

    @property
    def created_at(self) -> str:
        return f"{self.date}T{self.time}"

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def _load_l0_turns() -> list[_L0Turn]:
    dates = sorted(vault.list_day_dates())
    turns: list[_L0Turn] = []
    seen: dict[str, str] = {}
    errors: list[str] = []

    for date in dates:
        for turn in vault.read_day(date):
            loc = f"{date} {turn.time}"
            if turn.id is None:
                errors.append(
                    f"{loc}: missing UID; run scripts/backfill_entry_uids.py first"
                )
                continue
            if turn.id in seen:
                errors.append(f"{loc}: duplicate UID {turn.id!r} also seen at {seen[turn.id]}")
                continue
            if turn.type not in vault.ENTRY_TYPES:
                errors.append(f"{loc}: unsupported entry type {turn.type!r}")
                continue
            seen[turn.id] = loc
            turns.append(
                _L0Turn(
                    id=turn.id,
                    date=date,
                    time=turn.time,
                    type=turn.type,
                    text=turn.text,
                )
            )

    if errors:
        raise ReextractError("; ".join(errors))
    return turns


def _json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


async def reextract_all(call_model: ModelCaller | None = None) -> ReextractReport:
    """Rebuild L1 SQLite from L0 Markdown and retry stale extractions.

    Reads every Markdown turn oldest-first, preserves the UID already stored in
    the header, uses R2's canonical source hash, and refreshes entries,
    extractions, mood_series, and FTS. Chroma/L2 is intentionally untouched; R4
    owns vector rebuildability.
    """
    turns = _load_l0_turns()
    entry_ids = {turn.id for turn in turns}

    inserted = 0
    updated = 0
    unchanged = 0
    extraction_created = 0
    skipped = 0
    retried = 0
    done = 0
    null_stored = 0

    conn = db.get_or_create_db()
    try:
        for turn in turns:
            state = db.upsert_entry_from_l0(
                conn,
                id=turn.id,
                date=turn.date,
                type=turn.type,
                text=turn.text,
                word_count=turn.word_count,
                created_at=turn.created_at,
                is_seeded=False,
            )
            if state == "inserted":
                inserted += 1
            elif state == "updated":
                updated += 1
            else:
                unchanged += 1

        pruned = db.prune_entries_not_in(conn, entry_ids)

        for turn in turns:
            source_hash = vault.source_hash(turn.text)
            if db.ensure_pending_extraction(conn, turn.id, source_hash=source_hash):
                extraction_created += 1

            row = db.get_extraction(conn, turn.id)
            if (
                row is not None
                and row["extraction_status"] == "done"
                and row["source_hash"] == source_hash
            ):
                db.replace_mood_series(
                    conn,
                    entry_id=turn.id,
                    date=turn.date,
                    mood=row["mood"],
                    emotions=_json_list(row["emotions"]),
                    is_seeded=False,
                )
                skipped += 1
                continue

            retried += 1
            result = await extract.extract_entry(turn.text, call_model=call_model)
            if result.status == "done":
                db.finalize_extraction(
                    conn,
                    turn.id,
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
                    summary=result.summary or "",
                    extracted_at=result.extracted_at or "",
                    source_hash=source_hash,
                )
                db.replace_mood_series(
                    conn,
                    entry_id=turn.id,
                    date=turn.date,
                    mood=result.mood,
                    emotions=result.emotions,
                    is_seeded=False,
                )
                done += 1
            else:
                db.mark_null_stored(conn, turn.id, source_hash=source_hash)
                db.delete_mood_series(conn, turn.id)
                null_stored += 1

        db.rebuild_entries_fts(conn)
    finally:
        conn.close()

    log.info(
        "reextract: scanned=%d inserted=%d updated=%d pruned=%d skipped=%d "
        "retried=%d done=%d null_stored=%d",
        len(turns),
        inserted,
        updated,
        pruned,
        skipped,
        retried,
        done,
        null_stored,
    )
    return ReextractReport(
        scanned=len(turns),
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        pruned=pruned,
        extraction_created=extraction_created,
        skipped=skipped,
        retried=retried,
        done=done,
        null_stored=null_stored,
        fts_rebuilt=True,
    )
