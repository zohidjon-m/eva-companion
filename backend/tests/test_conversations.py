"""Chat history — the conversation transcript store + its read/delete endpoints.

These assert the chat-history feature added on top of the live /chat stream: a
captured turn persists BOTH sides into a conversation, the conversation is
listable and reopenable (newest first), a client-supplied id is honored, and the
DELETE endpoint removes it. The live model, capture pipeline, and recall are
stubbed — only the transcript persistence is under test here.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    """TestClient with an isolated vault, a present+stubbed model, no real capture."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))

    # Reload the memory modules so the temp EVA_VAULT_DIR is the active vault.
    import memory
    import memory.vault as vault_mod
    import memory.db as db_mod
    import memory.conversations as conv_mod

    importlib.reload(memory)
    importlib.reload(vault_mod)
    importlib.reload(db_mod)
    importlib.reload(conv_mod)

    from app import app
    from llm import client as llm_client
    from llm import server as llm_server
    from memory import capture, retrieval

    async def fake_stream(messages, **kwargs):
        for piece in ["I hear ", "you."]:
            yield piece

    def fake_capture(text, entry_type):
        return capture.vault.EntryRecord(
            id="t", date="2026-06-18", type=entry_type, text=text,
            word_count=len(text.split()), created_at="2026-06-18T00:00:00",
        )

    async def fake_extract(*a, **k):
        return "done"

    monkeypatch.setattr(llm_server, "model_present", lambda: True)
    monkeypatch.setattr(llm_client, "stream_chat", fake_stream)
    monkeypatch.setattr(capture, "capture_entry", fake_capture)
    monkeypatch.setattr(capture, "run_extraction_and_embed", fake_extract)
    monkeypatch.setattr(retrieval, "recall_memories", lambda *a, **k: [])

    return TestClient(app), conv_mod


def _drain(ws):
    """Read one reply (start..done), tolerating citations/memory frames."""
    while True:
        frame = ws.receive_json()
        if frame["type"] == "done":
            return


# --- the store module itself ------------------------------------------------
def test_store_roundtrip(ctx):
    _, conv = ctx
    cid = conv.start_conversation("I had a rough day with Daniel")
    conv.append_turn(cid, "user", "I had a rough day with Daniel")
    conv.append_turn(cid, "eva", "That sounds exhausting.")
    got = conv.get_conversation(cid)
    assert got is not None
    assert [t["role"] for t in got["turns"]] == ["user", "eva"]
    assert got["title"].startswith("I had a rough day")
    assert conv.list_conversations()[0]["turn_count"] == 2


def test_ensure_is_insert_or_ignore(ctx):
    _, conv = ctx
    conv.ensure_conversation("fixed-id", "first message")
    conv.ensure_conversation("fixed-id", "a different later message")
    rows = [c for c in conv.list_conversations() if c["id"] == "fixed-id"]
    assert len(rows) == 1
    # Title stays the first message; the second ensure() didn't overwrite it.
    assert rows[0]["title"] == "first message"


# --- the /chat persistence path + endpoints ---------------------------------
def test_chat_turn_persists_both_sides(ctx):
    tc, conv = ctx
    with tc.websocket_connect("/chat") as ws:
        ws.send_json({"text": "I had a rough day", "conversation_id": "conv-1"})
        _drain(ws)

    convo = tc.get("/chat/conversation/conv-1").json()
    assert [(t["role"], t["text"]) for t in convo["turns"]] == [
        ("user", "I had a rough day"),
        ("eva", "I hear you."),
    ]


def test_conversations_list_and_delete(ctx):
    tc, _ = ctx
    with tc.websocket_connect("/chat") as ws:
        ws.send_json({"text": "first thread", "conversation_id": "c-a"})
        _drain(ws)
        ws.send_json({"text": "second thread", "conversation_id": "c-b"})
        _drain(ws)

    listed = tc.get("/chat/conversations").json()["conversations"]
    assert {c["id"] for c in listed} == {"c-a", "c-b"}

    assert tc.delete("/chat/conversation/c-a").json()["deleted"] is True
    remaining = {c["id"] for c in tc.get("/chat/conversations").json()["conversations"]}
    assert remaining == {"c-b"}
    # The deleted conversation now 404s.
    assert tc.get("/chat/conversation/c-a").status_code == 404


def test_retry_does_not_duplicate_user_turn(ctx):
    tc, _ = ctx
    with tc.websocket_connect("/chat") as ws:
        ws.send_json({"text": "only once", "conversation_id": "c-r"})
        _drain(ws)
        # A retry (capture=False) must not append the user turn again.
        ws.send_json({"text": "only once", "capture": False, "conversation_id": "c-r"})
        _drain(ws)

    turns = tc.get("/chat/conversation/c-r").json()["turns"]
    user_turns = [t for t in turns if t["role"] == "user"]
    assert len(user_turns) == 1
