"""Phase 2 Step B — POST /entry captures synchronously and extracts in background.

Uses FastAPI's TestClient, which runs background tasks after the response, so we
can assert the full pipeline ran. The model and the embedder are stubbed — this
test is about the HTTP wiring, not the model.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from memory import db, extract, vault, vector

GOOD_JSON = (
    '{"mood": 1, "emotions": [], "entities": [], "themes": ["test"], "events": [], '
    '"stated_goals": [], "behaviors": [], "decisions": [], "open_loops": [], '
    '"self_judgments": [], "summary": "A short test entry that the stub model '
    'turns into a valid extraction record for the endpoint test."}'
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))

    async def fake_call(prompt, *, temperature, max_tokens):
        return GOOD_JSON

    monkeypatch.setattr(extract, "_llama_server_call", fake_call)
    embeds = []
    monkeypatch.setattr(vector, "embed_summary", lambda **kw: embeds.append(kw))
    return TestClient(app), embeds


def test_post_entry_saves_and_extracts(client):
    tc, embeds = client
    resp = tc.post("/entry", json={"text": "Hello Eva, testing capture.", "type": "chat"})
    assert resp.status_code == 200
    entry_id = resp.json()["id"]

    # Synchronous save: markdown + index row exist immediately.
    conn = db.connect()
    assert db.get_entry(conn, entry_id) is not None
    # Background task has run by now (TestClient runs it after the response):
    ext = db.get_extraction(conn, entry_id)
    assert ext["extraction_status"] == "done"
    assert ext["summary"].startswith("A short test entry")
    conn.close()

    assert len(embeds) == 1 and embeds[0]["entry_id"] == entry_id


def test_post_entry_rejects_bad_type(client):
    tc, _ = client
    resp = tc.post("/entry", json={"text": "hi", "type": "note"})
    assert resp.status_code == 400


def test_post_entry_rejects_empty_text(client):
    tc, _ = client
    resp = tc.post("/entry", json={"text": "", "type": "chat"})
    # Pydantic min_length rejects before our handler → 422.
    assert resp.status_code == 422
