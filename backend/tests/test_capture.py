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
    # mood_series populated (capture-completeness write).
    ms = conn.execute("SELECT * FROM mood_series WHERE entry_id=?", (rec.id,)).fetchone()
    assert ms is not None and ms["mood"] == 3
    conn.close()

    # Embedding was requested with the right metadata.
    assert len(embed_calls) == 1
    assert embed_calls[0]["entry_id"] == rec.id
    assert embed_calls[0]["themes"] == ["nature", "rest"]


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
