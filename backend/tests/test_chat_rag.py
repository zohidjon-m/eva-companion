"""Phase 7 — RAG wiring in the /chat socket (model, capture, vector mocked).

Asserts the listen-first contract end to end at the socket boundary:
  * a venting turn never calls retrieval and sends no citations frame;
  * a question turn retrieves, injects the passages + grounding rule into the
    system prompt, and emits a citations frame before the tokens;
  * a question with no relevant passage retrieves but cites nothing (no frame),
    so Eva can be honest about the gap without a fabricated source.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from intent import classifier as intent_classifier
from llm import client as llm_client
from llm import server as llm_server
from memory import capture, retrieval


def _setup(monkeypatch, recorder, *, passages):
    """Fake model + capture; record the system prompt and whether retrieval ran.

    ``passages`` is the list :func:`retrieval.retrieve_corpus` should return for
    this test; a spy records every call so we can assert a vent never retrieves.
    """

    async def fake_stream(messages, **kwargs):
        recorder["messages"] = messages
        for piece in ["Okay.", ""]:
            if piece:
                yield piece

    monkeypatch.setattr(llm_server, "model_present", lambda: True)
    monkeypatch.setattr(llm_client, "stream_chat", fake_stream)

    def fake_capture(text, entry_type):
        return capture.vault.EntryRecord(
            id="t", date="2026-06-16", type=entry_type, text=text,
            word_count=len(text.split()), created_at="2026-06-16T00:00:00",
        )

    async def fake_extract(*a, **k):
        return "done"

    monkeypatch.setattr(capture, "capture_entry", fake_capture)
    monkeypatch.setattr(capture, "run_extraction_and_embed", fake_extract)

    def spy_retrieve(text, **k):
        recorder.setdefault("retrieve_calls", []).append(text)
        return passages

    monkeypatch.setattr(retrieval, "retrieve_corpus", spy_retrieve)


def _drain(ws):
    """Read one reply; return (joined_text, citations_frame_or_None)."""
    assert ws.receive_json() == {"type": "start"}
    citations = None
    out = []
    while True:
        frame = ws.receive_json()
        if frame["type"] == "done":
            break
        if frame["type"] == "citations":
            citations = frame["citations"]
            continue
        assert frame["type"] == "token"
        out.append(frame["content"])
    return "".join(out), citations


def test_vent_turn_bypasses_retrieval(monkeypatch):
    rec: dict = {}
    _setup(monkeypatch, rec, passages=[])
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("I'm so worn out, today was just heavy.")  # pure vent
        _, citations = _drain(ws)

    # Retrieval was never called (listen-first), and no citations frame was sent.
    assert rec.get("retrieve_calls") is None
    assert citations is None
    # No corpus block leaked into the system prompt.
    assert "Passages from their library" not in rec["messages"][0]["content"]


def test_question_turn_retrieves_grounds_and_cites(monkeypatch):
    passage = retrieval.Passage(
        text="Patience is repeatedly praised.",
        source_file="book.pdf", page=42, section=None, distance=0.2,
    )
    rec: dict = {}
    _setup(monkeypatch, rec, passages=[passage])
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("What does the book say about patience?")
        _, citations = _drain(ws)

    # Retrieval fired on the question.
    assert rec["retrieve_calls"] == ["What does the book say about patience?"]
    # The passage and the hard grounding rule are both in the system prompt.
    prompt = rec["messages"][0]["content"]
    assert "Patience is repeatedly praised." in prompt
    assert "never invent" in prompt  # the grounding rule's no-fabrication clause
    # A citations frame carried the source chip to the UI.
    assert citations == [
        {
            "source_file": "book.pdf",
            "page": 42,
            "section": None,
            "label": "book.pdf · p. 42",
            "text": "Patience is repeatedly praised.",
        }
    ]


def test_question_with_no_match_cites_nothing(monkeypatch):
    rec: dict = {}
    _setup(monkeypatch, rec, passages=[])  # retrieval finds nothing relevant
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("What does the book say about quantum computing?")
        _, citations = _drain(ws)

    # Retrieval was attempted, but nothing cleared the threshold → no frame, no
    # corpus block. Eva is left to say she doesn't find it, never to invent one.
    assert rec["retrieve_calls"] == ["What does the book say about quantum computing?"]
    assert citations is None
    assert "Passages from their library" not in rec["messages"][0]["content"]
