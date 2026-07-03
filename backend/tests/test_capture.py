"""Phase 2 Step B — capture pipeline: retry/null_stored, durability, wiring.

Exercises the orchestration in memory.extract + memory.capture with a *mock*
model (no llama-server, no network), plus the durability guarantees that matter
most: a failed extraction never loses the entry, and deleting the SQLite index
never touches the Markdown source of truth.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

from memory import extract


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
        return seq.pop(0)

    return _call


# ── extract_entry orchestration ──────────────────────────────────────────────
def test_extract_done_first_try():
    res = asyncio.run(extract.extract_entry("text", call_model=make_caller(GOOD_JSON)))
    assert res.status == "done"
    assert res.mood == 3
    assert res.summary.startswith("They hiked")
    assert res.extracted_at is not None


def test_extract_retry_then_success():
    res = asyncio.run(extract.extract_entry(
        "text", call_model=make_caller("garbage, no json", GOOD_JSON)
    ))
    assert res.status == "done"
    assert len(res.raw) == 2  # both attempts recorded


def test_extract_null_stored_after_two_failures():
    res = asyncio.run(extract.extract_entry(
        "text", call_model=make_caller("nope", "still nope")
    ))
    assert res.status == "null_stored"
    assert res.summary is None
    assert res.mood is None
    assert len(res.errors) == 2


def test_extract_null_stored_when_model_call_raises():
    async def boom(prompt, *, temperature, max_tokens):
        raise RuntimeError("connection refused")

    res = asyncio.run(extract.extract_entry("text", call_model=boom))
    assert res.status == "null_stored"
    assert all("model call failed" in e for e in res.errors)


# ── capture pipeline (vault + db + background extraction) ─────────────────────
@pytest.fixture()
def mem(tmp_path, monkeypatch):
    """Reload the memory stack rooted at a temp vault; stub out embedding."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    import memory
    import memory.db as db
    import memory.vault as vault
    import memory.capture as capture
    import memory.vector as vector

    for m in (memory, db, vault, vector, capture):
        importlib.reload(m)

    # Don't load ChromaDB/bge in unit tests — assert embedding is *requested*.
    calls = []
    monkeypatch.setattr(vector, "embed_summary", lambda **kw: calls.append(kw))
    # Episodes embedding (R4) is also on the done path; default it to a no-op so
    # these tests don't touch Chroma. The dedicated episode test re-patches it.
    monkeypatch.setattr(vector, "embed_episodes", lambda **kw: None)
    return capture, db, vault, calls


def test_capture_writes_vault_db_and_pending(mem):
    capture, db, vault, _ = mem
    rec = capture.capture_entry("Rough day, but I'm okay.", "chat")

    # L0 markdown exists and contains the text.
    assert rec.text in vault.day_file(rec.date).read_text()
    # L1 index row + a pending extraction row.
    conn = db.connect()
    assert db.count_entries(conn) == 1
    ext = db.get_extraction(conn, rec.id)
    assert ext["extraction_status"] == "pending"
    assert ext["source_hash"] == vault.source_hash(rec.text)
    conn.close()


def test_run_extraction_done_finalizes_and_embeds(mem):
    capture, db, vault, embed_calls = mem
    rec = capture.capture_entry("Hiked with Sam, felt great.", "journal")
    status = asyncio.run(capture.run_extraction_and_embed(
        rec.id, rec.text, rec.date, call_model=make_caller(GOOD_JSON)
    ))
    assert status == "done"

    conn = db.connect()
    ext = db.get_extraction(conn, rec.id)
    assert ext["extraction_status"] == "done"
    assert ext["mood"] == 3
    assert ext["summary"].startswith("They hiked")
    assert ext["source_hash"] == vault.source_hash(rec.text)
    # mood_series populated (capture-completeness write).
    ms = conn.execute("SELECT * FROM mood_series WHERE entry_id=?", (rec.id,)).fetchone()
    assert ms is not None and ms["mood"] == 3
    conn.close()

    # Embedding was requested with the right metadata.
    assert len(embed_calls) == 1
    assert embed_calls[0]["entry_id"] == rec.id
    assert embed_calls[0]["themes"] == ["nature", "rest"]


def test_run_extraction_also_embeds_episodes(mem, monkeypatch):
    """R4: the done path embeds open loops + events into ``episodes`` too."""
    capture, db, vault, _ = mem
    import memory.vector as vector

    ep_calls = []
    monkeypatch.setattr(vector, "embed_episodes", lambda **kw: ep_calls.append(kw))

    rec = capture.capture_entry("Hiked Eagle Ridge with Sam.", "journal")
    status = asyncio.run(capture.run_extraction_and_embed(
        rec.id, rec.text, rec.date, call_model=make_caller(GOOD_JSON)
    ))
    assert status == "done"

    # Episodes embed requested once, with the extraction's units (GOOD_JSON has
    # no open loops and one event) and the entry's id/metadata.
    assert len(ep_calls) == 1
    assert ep_calls[0]["entry_id"] == rec.id
    assert ep_calls[0]["open_loops"] == []
    assert ep_calls[0]["events"] == ["hiked eight miles"]
    assert ep_calls[0]["is_seeded"] is False


def test_malformed_model_output_yields_null_stored_but_entry_survives(mem):
    capture, db, vault, embed_calls = mem
    rec = capture.capture_entry("A short note.", "chat")
    status = asyncio.run(capture.run_extraction_and_embed(
        rec.id, rec.text, rec.date, call_model=make_caller("junk", "more junk")
    ))
    assert status == "null_stored"

    conn = db.connect()
    ext = db.get_extraction(conn, rec.id)
    assert ext["extraction_status"] == "null_stored"
    assert ext["mood"] is None and ext["summary"] is None
    assert ext["source_hash"] == vault.source_hash(rec.text)
    conn.close()

    # The vault entry is fully intact despite extraction failure.
    assert rec.text in vault.day_file(rec.date).read_text()
    # No embedding on a failed extraction.
    assert embed_calls == []


def test_markdown_survives_db_deletion(mem):
    capture, db, vault, _ = mem
    rec = capture.capture_entry("This must outlive the database.", "journal")
    md = vault.day_file(rec.date)
    assert md.exists()

    # Nuke the derived index entirely.
    db.db_path().unlink()
    assert not db.db_path().exists()

    # L0 is untouched and still complete.
    assert rec.text in md.read_text()


def test_unchanged_extraction_skips_model_and_embed(mem):
    capture, db, vault, embed_calls = mem
    rec = capture.capture_entry("Hiked with Sam, felt great.", "journal")
    first = asyncio.run(capture.run_extraction_and_embed(
        rec.id, rec.text, rec.date, call_model=make_caller(GOOD_JSON)
    ))
    assert first == "done"

    async def should_not_call(prompt, *, temperature, max_tokens):
        raise AssertionError("unchanged entry should not call the model")

    second = asyncio.run(capture.run_extraction_and_embed(
        rec.id, rec.text, rec.date, call_model=should_not_call
    ))

    assert second == "done"
    assert len(embed_calls) == 1


def test_changed_body_keeps_uid_but_changes_source_hash(mem):
    capture, db, vault, embed_calls = mem
    rec = capture.capture_entry("Original body.", "journal")
    status = asyncio.run(capture.run_extraction_and_embed(
        rec.id, rec.text, rec.date, call_model=make_caller(GOOD_JSON)
    ))
    assert status == "done"

    conn = db.connect()
    try:
        first_hash = db.get_extraction(conn, rec.id)["source_hash"]
    finally:
        conn.close()

    updated = vault.update_entry(rec.id, "Changed body.")
    assert updated is not None and updated.id == rec.id
    conn = db.connect()
    try:
        db.update_entry_text(
            conn, updated.id, text=updated.text, word_count=updated.word_count
        )
    finally:
        conn.close()

    status = asyncio.run(capture.run_extraction_and_embed(
        updated.id, updated.text, updated.date, call_model=make_caller(GOOD_JSON)
    ))

    conn = db.connect()
    try:
        second_hash = db.get_extraction(conn, rec.id)["source_hash"]
    finally:
        conn.close()
    assert status == "done"
    assert second_hash == vault.source_hash("Changed body.")
    assert second_hash != first_hash
    assert len(embed_calls) == 2


def test_entry_ids_survive_db_rebuild_from_markdown(mem):
    capture, db, vault, _ = mem
    first = capture.capture_entry("First durable entry.", "journal")
    second = capture.capture_entry("Second durable entry.", "chat")
    original_ids = {first.id, second.id}

    db.db_path().unlink()
    conn = db.get_or_create_db()
    try:
        for date in vault.list_day_dates():
            for turn in vault.read_day(date):
                db.insert_entry(
                    conn,
                    id=turn.id,
                    date=date,
                    type=turn.type,
                    text=turn.text,
                    word_count=len(turn.text.split()),
                    created_at=f"{date}T{turn.time}",
                )
        rebuilt_ids = {r["id"] for r in conn.execute("SELECT id FROM entries")}
    finally:
        conn.close()

    assert rebuilt_ids == original_ids
