"""Phase 4 — the /chat surface end to end (model + capture mocked).

These assert the *wiring* the chat surface adds on top of the Phase-1 stream:
the persona system prompt is assembled and sent, the crisis addendum is injected
only on a crisis turn, the reply is capped at 450 tokens, and session history is
carried across turns. Storage and the live model are stubbed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from llm import client as llm_client
from memory import capture, conversations, profile, retrieval
from prompts import assembly
from support import stub_chat_provider_ready


def _setup(monkeypatch, recorder):
    """Wire a fake model that records its call args, present model, no real capture."""

    async def fake_stream(messages, **kwargs):
        recorder["messages"] = messages
        recorder["kwargs"] = kwargs
        for piece in ["Okay.", ""]:
            if piece:
                yield piece

    stub_chat_provider_ready(monkeypatch)
    monkeypatch.setattr(llm_client, "stream_chat", fake_stream)

    def fake_capture(text, entry_type):
        recorder.setdefault("captured", []).append(text)
        return capture.vault.EntryRecord(
            id="t", date="2026-06-16", type=entry_type, text=text,
            word_count=len(text.split()), created_at="2026-06-16T00:00:00",
        )

    async def fake_extract(*a, **k):
        return "done"

    monkeypatch.setattr(capture, "capture_entry", fake_capture)
    monkeypatch.setattr(capture, "run_extraction_and_embed", fake_extract)
    # These tests don't isolate the vault; stub the chat-transcript writers so the
    # /chat handler doesn't persist conversations into the real local_vault.
    # (Persistence is covered by test_conversations.py with an isolated vault.)
    monkeypatch.setattr(conversations, "start_conversation", lambda *a, **k: "test-conv")
    monkeypatch.setattr(conversations, "ensure_conversation", lambda *a, **k: None)
    monkeypatch.setattr(conversations, "append_turn", lambda *a, **k: None)
    # Phase 11 recall is orthogonal to what these persona/history tests assert;
    # stub it off so no memory block leaks into the prompt and no memory frame
    # appears on the wire (recall has its own tests in test_retrieval.py).
    monkeypatch.setattr(retrieval, "recall_memories", lambda *a, **k: [])
    # Recent-episode assembly (R6) also runs every turn and reads the DB; stub it
    # off so these persona/history tests see only the persona + history wiring.
    monkeypatch.setattr(retrieval, "recent_episodes", lambda *a, **k: [])
    monkeypatch.setattr(profile, "retrieve_slices", lambda *a, **k: [])


def _drain(ws):
    """Read one full reply (start..done) and return the joined token text."""
    frame = ws.receive_json()
    if frame["type"] == "error":
        raise AssertionError(f"unexpected chat error: {frame}")
    assert frame == {"type": "start"}
    out = []
    while True:
        frame = ws.receive_json()
        if frame["type"] == "error":
            raise AssertionError(f"unexpected chat error: {frame}")
        if frame["type"] == "done":
            break
        if frame["type"] == "meta":
            continue
        assert frame["type"] == "token"
        out.append(frame["content"])
    return "".join(out)


def test_persona_prompt_and_cap_are_applied(monkeypatch):
    rec: dict = {}
    _setup(monkeypatch, rec)
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("hello Eva")
        _drain(ws)

    # The reply is capped at 450 tokens (the persona's reply cap).
    assert rec["kwargs"]["max_tokens"] == assembly.REPLY_MAX_TOKENS == 450
    # The system prompt is folded into the first (user) message and is the persona.
    first = rec["messages"][0]
    assert first["role"] == "user"
    assert first["content"].startswith("You are Eva")
    assert "hello Eva" in first["content"]
    # An ordinary turn carries NO crisis addendum.
    assert "CRISIS-CARE" not in first["content"]


def test_crisis_turn_injects_addendum(monkeypatch):
    rec: dict = {}
    _setup(monkeypatch, rec)
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("I want to kill myself")
        _drain(ws)

    # The crisis-care addendum rode along on the persona block for this turn,
    # and the reply was NOT suppressed (we streamed tokens normally).
    assert "CRISIS-CARE" in rec["messages"][0]["content"]
    assert rec["captured"] == ["I want to kill myself"]


def test_session_history_is_carried(monkeypatch):
    rec: dict = {}
    _setup(monkeypatch, rec)
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("first message")
        _drain(ws)
        ws.send_text("second message")
        _drain(ws)

    # On the second turn the model sees the prior user+assistant turns, then the
    # new user message — proving in-session history is threaded through.
    msgs = rec["messages"]
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user"]
    assert "first message" in msgs[0]["content"]
    assert msgs[-1]["content"] == "second message"


def test_retry_frame_does_not_recapture(monkeypatch):
    rec: dict = {}
    _setup(monkeypatch, rec)
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        import json

        ws.send_text(json.dumps({"text": "retry me", "capture": False}))
        _drain(ws)

    # capture=False (a UI retry of an already-saved turn) must not write again.
    assert rec.get("captured") is None
