"""Phase 5 — the journaling surface end to end (model + embedder stubbed).

These assert the journal API the surface adds on top of the Phase-2 capture
pipeline: a journal save flows through the same vault + extraction path, the
browse list and read-only day view read back correctly (including a hand-placed
older file that was never indexed), and the post-save acknowledgment is one
bounded model call that degrades to ``null`` when the model is absent.
"""

from __future__ import annotations

import importlib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

GOOD_JSON = (
    '{"mood": 2, "emotions": [], "entities": [], "themes": ["test"], "events": [], '
    '"stated_goals": [], "behaviors": [], "decisions": [], "open_loops": [], '
    '"self_judgments": [], "summary": "A short journal entry that the stub model '
    'turns into a valid extraction record for the journal surface test."}'
)


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    """A TestClient with an isolated vault, a present+stubbed model, no real embeds."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))

    # Reload the memory package so vault_dir() picks up the temp EVA_VAULT_DIR.
    import memory
    import memory.vault as vault_mod
    import memory.db as db_mod

    importlib.reload(memory)
    importlib.reload(vault_mod)
    importlib.reload(db_mod)

    from app import app
    from llm import client as llm_client
    from llm import server as llm_server
    from memory import extract, vector

    async def fake_extract_call(prompt, *, temperature, max_tokens):
        return GOOD_JSON

    embeds: list = []
    episode_embeds: list = []
    monkeypatch.setattr(extract, "_llama_server_call", fake_extract_call)
    monkeypatch.setattr(vector, "embed_summary", lambda **kw: embeds.append(kw))
    monkeypatch.setattr(vector, "embed_episodes", lambda **kw: episode_embeds.append(kw))
    monkeypatch.setattr(vector, "delete_entry_vectors", lambda entry_id: None)
    monkeypatch.setattr(llm_server, "model_present", lambda: True)

    async def fake_ack(messages, **kwargs):
        return "  That sounds like a full day.\nWhat stayed with you most?  "

    monkeypatch.setattr(llm_client, "complete_chat", fake_ack)

    return TestClient(app), {
        "vault": vault_mod,
        "db": db_mod,
        "llm_server": llm_server,
        "embeds": embeds,
        "episode_embeds": episode_embeds,
    }


def test_save_writes_vault_index_and_extraction(ctx):
    tc, mods = ctx
    resp = tc.post("/journal", json={"text": "Today I planted tomatoes and felt calm."})
    assert resp.status_code == 200
    body = resp.json()
    entry_id, date = body["id"], body["date"]

    # L0: the journal turn is in today's Markdown day file.
    day_text = mods["vault"].day_file(date).read_text()
    assert "Today I planted tomatoes" in day_text
    assert "· journal" in day_text

    # L1: an entry row + a finished extraction row (the background task ran).
    conn = mods["db"].connect()
    try:
        assert mods["db"].get_entry(conn, entry_id) is not None
        ext = mods["db"].get_extraction(conn, entry_id)
        assert ext["extraction_status"] == "done"
    finally:
        conn.close()


def test_acknowledge_returns_single_line(ctx):
    tc, _ = ctx
    entry_id = tc.post("/journal", json={"text": "A quiet, ordinary day."}).json()["id"]
    resp = tc.post("/journal/acknowledge", json={"entry_id": entry_id})
    assert resp.status_code == 200
    line = resp.json()["acknowledgment"]
    # Collapsed to one clean line (no stray newlines / edge whitespace).
    assert line == "That sounds like a full day. What stayed with you most?"


def test_acknowledge_null_when_model_absent(ctx, monkeypatch):
    tc, mods = ctx
    entry_id = tc.post("/journal", json={"text": "Another day."}).json()["id"]
    monkeypatch.setattr(mods["llm_server"], "model_present", lambda: False)
    resp = tc.post("/journal/acknowledge", json={"entry_id": entry_id})
    assert resp.status_code == 200
    assert resp.json()["acknowledgment"] is None


def test_acknowledge_unknown_entry_404(ctx):
    tc, _ = ctx
    resp = tc.post("/journal/acknowledge", json={"entry_id": "does-not-exist"})
    assert resp.status_code == 404


def test_days_list_includes_saved_and_handplaced(ctx):
    tc, mods = ctx
    # A real save (indexed in SQLite) ...
    saved = tc.post("/journal", json={"text": "Saved entry for the list."}).json()
    # ... and an OLDER hand-placed file that was never indexed (vault only).
    mods["vault"].save_entry(
        "An older entry placed by hand.", "journal",
        when=datetime(2026, 5, 1, 8, 30, 0),
    )

    days = tc.get("/journal/days").json()["days"]
    dates = [d["date"] for d in days]
    assert saved["date"] in dates
    assert "2026-05-01" in dates  # the hand-placed day is browseable too
    # Newest first.
    assert dates == sorted(dates, reverse=True)
    handplaced = next(d for d in days if d["date"] == "2026-05-01")
    assert "older entry placed by hand" in handplaced["preview"]


def test_day_view_renders_handplaced_file(ctx):
    tc, mods = ctx
    mods["vault"].save_entry(
        "First entry of that day.", "journal",
        when=datetime(2026, 5, 1, 8, 30, 0),
    )
    mods["vault"].save_entry(
        "A second, later entry.", "journal",
        when=datetime(2026, 5, 1, 21, 0, 0),
    )
    resp = tc.get("/journal/day/2026-05-01")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert [e["text"] for e in entries] == [
        "First entry of that day.",
        "A second, later entry.",
    ]
    assert entries[0]["time"] == "08:30:00"


def test_day_view_404_when_empty(ctx):
    tc, _ = ctx
    assert tc.get("/journal/day/2030-01-01").status_code == 404


def test_day_view_rejects_bad_date(ctx):
    tc, _ = ctx
    assert tc.get("/journal/day/not-a-date").status_code == 400


def test_chat_turns_excluded_from_journal_browse(ctx):
    tc, mods = ctx
    # A pure chat turn on an otherwise journal-free day must not appear as a
    # journal day, and its day view must 404.
    mods["vault"].save_entry(
        "just a chat turn", "chat", when=datetime(2026, 4, 2, 10, 0, 0),
    )
    dates = [d["date"] for d in tc.get("/journal/days").json()["days"]]
    assert "2026-04-02" not in dates
    assert tc.get("/journal/day/2026-04-02").status_code == 404


def test_put_entry_revisions_and_recomputes_before_return(ctx):
    tc, mods = ctx
    saved = tc.post("/journal", json={"text": "Original searchable body."}).json()

    resp = tc.put(f"/entries/{saved['id']}", json={"text": "Changed searchable body."})

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == saved["id"]
    assert body["text"] == "Changed searchable body."
    assert body["has_revisions"] is True
    assert body["original_text"] == "Original searchable body."

    conn = mods["db"].connect()
    try:
        entry = mods["db"].get_entry(conn, saved["id"])
        ext = mods["db"].get_extraction(conn, saved["id"])
        mood_rows = conn.execute(
            "SELECT COUNT(*) FROM mood_series WHERE entry_id=?", (saved["id"],)
        ).fetchone()[0]
        hits = conn.execute(
            """
            SELECT e.id
            FROM entries_fts
            JOIN entries e ON e.rowid = entries_fts.rowid
            WHERE entries_fts MATCH ?
            """,
            ("Changed",),
        ).fetchall()
    finally:
        conn.close()

    assert entry["text"] == "Changed searchable body."
    assert ext["extraction_status"] == "done"
    assert ext["source_hash"] == mods["vault"].source_hash("Changed searchable body.")
    assert mood_rows == 1
    assert [r["id"] for r in hits] == [saved["id"]]
    assert len(mods["embeds"]) == 2
    assert len(mods["episode_embeds"]) == 2


def test_journal_edit_route_remains_compatibility_wrapper(ctx):
    tc, _ = ctx
    saved = tc.post("/journal", json={"text": "Compatibility original."}).json()

    resp = tc.post(
        f"/journal/entry/{saved['id']}",
        json={"text": "Compatibility changed."},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == saved["id"]
    assert body["text"] == "Compatibility changed."
    assert body["original_text"] == "Compatibility original."


def test_get_journal_entry_exposes_original_after_edit(ctx):
    tc, _ = ctx
    saved = tc.post("/journal", json={"text": "Visible original."}).json()
    tc.put(f"/entries/{saved['id']}", json={"text": "Visible changed."})

    body = tc.get(f"/journal/entry/{saved['id']}").json()

    assert body["text"] == "Visible changed."
    assert body["has_revisions"] is True
    assert body["original_text"] == "Visible original."
