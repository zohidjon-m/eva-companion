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
from memory import capture, conversations, retrieval


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
    # These tests don't isolate the vault and don't assert on chat history, so stub
    # the transcript writers to no-ops — otherwise the /chat handler would persist
    # conversations into the real local_vault. (Persistence is covered by
    # test_conversations.py with an isolated vault.)
    monkeypatch.setattr(conversations, "start_conversation", lambda *a, **k: "test-conv")
    monkeypatch.setattr(conversations, "ensure_conversation", lambda *a, **k: None)
    monkeypatch.setattr(conversations, "append_turn", lambda *a, **k: None)

    def spy_retrieve(text, **k):
        recorder.setdefault("retrieve_calls", []).append(text)
        return passages

    monkeypatch.setattr(retrieval, "retrieve_corpus", spy_retrieve)
    # Phase 11 memory recall runs on every turn but is independent of the corpus
    # gate under test here; stub it off so these tests see only the corpus path
    # (no memory frame, no memory block in the prompt). Recall is covered in
    # test_retrieval.py.
    monkeypatch.setattr(retrieval, "recall_memories", lambda *a, **k: [])


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


# ── Phase 11: memory recall wiring at the socket boundary ────────────────────


def _drain_with_memory(ws):
    """Read one reply; return (text, citations_frame_or_None, memory_frame_or_None)."""
    assert ws.receive_json() == {"type": "start"}
    citations = memories = None
    out = []
    while True:
        frame = ws.receive_json()
        if frame["type"] == "done":
            break
        if frame["type"] == "citations":
            citations = frame["citations"]
            continue
        if frame["type"] == "memory":
            memories = frame["memories"]
            continue
        assert frame["type"] == "token"
        out.append(frame["content"])
    return "".join(out), citations, memories


def test_recall_injects_memory_block_and_emits_chip(monkeypatch):
    # A vent turn (no corpus retrieval) that nonetheless recalls a past entry:
    # recall is NOT gated by the listen-first intent rule, so Eva can remember even
    # while listening. The recalled summary lands in the prompt and a memory frame
    # carries the date chip to the UI.
    rec: dict = {}
    _setup(monkeypatch, rec, passages=[])  # vent → corpus stays empty
    memory = retrieval.Memory(
        entry_id="e1", date="2026-06-03",
        summary="You wrote about the strain of moving apartments.",
        mood=-1, themes=["home"], distance=0.25,
    )
    monkeypatch.setattr(retrieval, "recall_memories", lambda *a, **k: [memory])

    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("I've been so drained lately.")
        _, citations, memories = _drain_with_memory(ws)

    # No corpus citation (vent), but the recalled memory surfaced as a chip…
    assert citations is None
    assert memories == [{"date": "2026-06-03", "label": "Jun 3"}]
    # …and the summary was injected under the (friend-framed) memory header.
    prompt = rec["messages"][0]["content"]
    assert "shared with you before" in prompt
    assert "the strain of moving apartments" in prompt


def test_no_recall_means_no_memory_frame_or_block(monkeypatch):
    # The honesty path: recall returns nothing → no memory frame and no memory
    # block in the prompt, so Eva has no past entry to (mis)reference.
    rec: dict = {}
    _setup(monkeypatch, rec, passages=[])  # _setup already stubs recall → []
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("Just a brand new thought I've never written down.")
        _, citations, memories = _drain_with_memory(ws)

    assert citations is None
    assert memories is None
    assert "Context from past journal entries" not in rec["messages"][0]["content"]
