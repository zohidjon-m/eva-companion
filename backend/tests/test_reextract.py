"""R3 - rebuild SQLite L1 from the Markdown vault."""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime

import pytest


GOOD_JSON = (
    '{"mood": 3, "emotions": [{"name": "joy", "intensity": 0.6}], '
    '"entities": [{"name": "Eagle Ridge", "type": "place", "normalized": "eagle ridge"}], '
    '"themes": ["nature", "rest"], "events": ["hiked eight miles"], '
    '"stated_goals": [{"text": "hike monthly", "is_new": true}], '
    '"behaviors": ["went for a long hike"], "decisions": ["plan next hike"], '
    '"open_loops": [], "self_judgments": [], '
    '"summary": "They hiked Eagle Ridge with a friend and felt restored and grateful, '
    'and resolved to make monthly hikes a habit."}'
)


def make_caller(*responses):
    """Build a mock ModelCaller that returns the given responses in order."""
    seq = list(responses)

    async def _call(prompt, *, temperature, max_tokens):
        if not seq:
            raise AssertionError("model called more times than expected")
        return seq.pop(0)

    return _call


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    """Reload the memory stack rooted at a temp vault for R3 tests."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    import memory
    import memory.db as db
    import memory.extract as extract
    import memory.reextract as reextract
    import memory.vault as vault

    for m in (memory, db, extract, vault, reextract):
        importlib.reload(m)
    return reextract, db, vault


def _conn(db):
    return db.get_or_create_db()


def test_fresh_rebuild_from_markdown_preserves_uids_and_l1(mem):
    reextract, db, vault = mem
    first = vault.save_entry(
        "A durable mountain note.", "journal",
        when=datetime(2026, 6, 15, 8, 0, 0),
    )
    second = vault.save_entry(
        "A second durable chat note.", "chat",
        when=datetime(2026, 6, 16, 9, 30, 0),
    )

    report = asyncio.run(
        reextract.reextract_all(call_model=make_caller(GOOD_JSON, GOOD_JSON))
    )

    conn = _conn(db)
    try:
        ids = {r["id"] for r in conn.execute("SELECT id FROM entries")}
        done = conn.execute(
            "SELECT COUNT(*) FROM extractions WHERE extraction_status='done'"
        ).fetchone()[0]
        moods = conn.execute("SELECT COUNT(*) FROM mood_series").fetchone()[0]
    finally:
        conn.close()

    assert report.scanned == 2
    assert report.inserted == 2
    assert report.done == 2
    assert report.fts_rebuilt is True
    assert ids == {first.id, second.id}
    assert done == 2
    assert moods == 2


def test_unchanged_done_extraction_is_hash_skipped(mem):
    reextract, db, vault = mem
    vault.save_entry(
        "Hiked with Sam and felt great.", "journal",
        when=datetime(2026, 6, 16, 9, 0, 0),
    )
    first = asyncio.run(reextract.reextract_all(call_model=make_caller(GOOD_JSON)))

    async def should_not_call(prompt, *, temperature, max_tokens):
        raise AssertionError("unchanged done extraction should not call model")

    second = asyncio.run(reextract.reextract_all(call_model=should_not_call))

    assert first.done == 1
    assert second.skipped == 1
    assert second.retried == 0
    assert second.done == 0


def test_changed_body_keeps_uid_changes_hash_and_replaces_mood(mem):
    reextract, db, vault = mem
    rec = vault.save_entry(
        "Original body.", "journal",
        when=datetime(2026, 6, 16, 9, 0, 0),
    )
    asyncio.run(reextract.reextract_all(call_model=make_caller(GOOD_JSON)))

    conn = _conn(db)
    try:
        first_hash = db.get_extraction(conn, rec.id)["source_hash"]
    finally:
        conn.close()

    updated = vault.update_entry(rec.id, "Changed body.")
    assert updated is not None

    report = asyncio.run(reextract.reextract_all(call_model=make_caller(GOOD_JSON)))

    conn = _conn(db)
    try:
        entry = db.get_entry(conn, rec.id)
        ext = db.get_extraction(conn, rec.id)
        mood_rows = conn.execute(
            "SELECT COUNT(*) FROM mood_series WHERE entry_id=?", (rec.id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert report.updated == 1
    assert report.retried == 1
    assert entry["id"] == rec.id
    assert ext["source_hash"] == vault.source_hash("Changed body.")
    assert ext["source_hash"] != first_hash
    assert mood_rows == 1


def test_null_stored_rows_are_retried_and_can_become_done(mem):
    reextract, db, vault = mem
    rec = vault.save_entry(
        "A short note that fails once.", "journal",
        when=datetime(2026, 6, 16, 9, 0, 0),
    )

    failed = asyncio.run(
        reextract.reextract_all(call_model=make_caller("junk", "more junk"))
    )
    conn = _conn(db)
    try:
        ext = db.get_extraction(conn, rec.id)
        mood_rows = conn.execute(
            "SELECT COUNT(*) FROM mood_series WHERE entry_id=?", (rec.id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert failed.null_stored == 1
    assert ext["extraction_status"] == "null_stored"
    assert mood_rows == 0

    repaired = asyncio.run(reextract.reextract_all(call_model=make_caller(GOOD_JSON)))
    conn = _conn(db)
    try:
        ext = db.get_extraction(conn, rec.id)
        mood_rows = conn.execute(
            "SELECT COUNT(*) FROM mood_series WHERE entry_id=?", (rec.id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert repaired.retried == 1
    assert repaired.done == 1
    assert ext["extraction_status"] == "done"
    assert mood_rows == 1


def test_malformed_reextract_clears_stale_fields_without_touching_l0(mem):
    reextract, db, vault = mem
    rec = vault.save_entry(
        "Original body.", "journal",
        when=datetime(2026, 6, 16, 9, 0, 0),
    )
    asyncio.run(reextract.reextract_all(call_model=make_caller(GOOD_JSON)))
    updated = vault.update_entry(rec.id, "Changed body that now fails.")
    assert updated is not None
    before = vault.day_file(updated.date).read_text(encoding="utf-8")

    report = asyncio.run(
        reextract.reextract_all(call_model=make_caller("bad", "still bad"))
    )

    conn = _conn(db)
    try:
        ext = db.get_extraction(conn, rec.id)
        mood_rows = conn.execute(
            "SELECT COUNT(*) FROM mood_series WHERE entry_id=?", (rec.id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert report.null_stored == 1
    assert ext["extraction_status"] == "null_stored"
    assert ext["summary"] is None
    assert ext["mood"] is None
    assert mood_rows == 0
    assert vault.day_file(updated.date).read_text(encoding="utf-8") == before


def test_rebuild_populates_fts_from_markdown(mem):
    reextract, db, vault = mem
    rec = vault.save_entry(
        "Durable search phrase lives in Markdown.", "journal",
        when=datetime(2026, 6, 16, 9, 0, 0),
    )

    asyncio.run(reextract.reextract_all(call_model=make_caller(GOOD_JSON)))

    conn = _conn(db)
    try:
        hits = conn.execute(
            """
            SELECT e.id
            FROM entries_fts
            JOIN entries e ON e.rowid = entries_fts.rowid
            WHERE entries_fts MATCH ?
            """,
            ("durable",),
        ).fetchall()
    finally:
        conn.close()

    assert [h["id"] for h in hits] == [rec.id]


def test_stale_db_only_entries_are_pruned(mem):
    reextract, db, vault = mem
    rec = vault.save_entry(
        "Only this entry exists in L0.", "journal",
        when=datetime(2026, 6, 16, 9, 0, 0),
    )
    conn = _conn(db)
    try:
        db.insert_entry(
            conn,
            id="stale-db-only",
            date="2026-06-01",
            type="journal",
            text="not in markdown",
            word_count=3,
            created_at="2026-06-01T09:00:00",
        )
        db.create_pending_extraction(conn, "stale-db-only", source_hash="old")
    finally:
        conn.close()

    report = asyncio.run(reextract.reextract_all(call_model=make_caller(GOOD_JSON)))

    conn = _conn(db)
    try:
        ids = {r["id"] for r in conn.execute("SELECT id FROM entries")}
        stale_ext = db.get_extraction(conn, "stale-db-only")
    finally:
        conn.close()

    assert report.pruned == 1
    assert ids == {rec.id}
    assert stale_ext is None


def test_missing_markdown_uid_aborts_without_guessing(mem):
    reextract, db, vault = mem
    path = vault.day_file("2026-06-16")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 2026-06-16\n\n## 09:00:00 · journal\n\nbody\n", encoding="utf-8")

    with pytest.raises(reextract.ReextractError, match="backfill_entry_uids"):
        asyncio.run(reextract.reextract_all(call_model=make_caller(GOOD_JSON)))

    assert not db.db_path().exists()
