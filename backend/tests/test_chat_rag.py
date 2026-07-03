"""R6 — RAG wiring in the /chat socket (model, capture, vector mocked).

Asserts the listen-first contract end to end at the socket boundary, now via the
engine pipeline:
  * vent / process / ambient turns never call retrieval and send no citations;
  * an ask_info / ask_advice turn retrieves, injects the passages + grounding rule
    into the system prompt, and emits a citations frame;
  * an ask_info turn with no relevant passage retrieves but cites nothing (no
    frame), so Eva can be honest about the gap without a fabricated source;
  * every turn emits a ``meta`` frame carrying the intent/persona/retrieval counts.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from intent import classifier as intent_classifier
from llm import client as llm_client
from memory import capture, conversations, retrieval
from support import stub_chat_provider_ready


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

    stub_chat_provider_ready(monkeypatch)
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
    # Memory recall and recent-episode assembly run on every turn but are
    # independent of the corpus gate under test here; stub them off so these tests
    # see only the corpus path. Recall is covered in test_retrieval.py; recent
    # episodes in test_engine_turn.py.
    monkeypatch.setattr(retrieval, "recall_memories", lambda *a, **k: [])
    monkeypatch.setattr(retrieval, "recent_episodes", lambda *a, **k: [])


def _drain(ws):
    """Read one reply; return (joined_text, citations_frame_or_None)."""
    frame = ws.receive_json()
    if frame["type"] == "error":
        raise AssertionError(f"unexpected chat error: {frame}")
    assert frame == {"type": "start"}
    citations = None
    out = []
    while True:
        frame = ws.receive_json()
        if frame["type"] == "error":
            raise AssertionError(f"unexpected chat error: {frame}")
        if frame["type"] == "done":
            break
        if frame["type"] == "meta":
            continue
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


def test_process_turn_bypasses_retrieval(monkeypatch):
    # A reflective "process" turn is still listen-first — no corpus, no citations.
    rec: dict = {}
    _setup(monkeypatch, rec, passages=[])
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("I keep coming back to why that day bothered me so much.")
        _, citations = _drain(ws)

    assert rec.get("retrieve_calls") is None
    assert citations is None
    assert "Passages from their library" not in rec["messages"][0]["content"]


def test_ambient_turn_bypasses_retrieval(monkeypatch):
    # A greeting/ack carries nothing to retrieve for.
    rec: dict = {}
    _setup(monkeypatch, rec, passages=[])
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("thanks so much")
        _, citations = _drain(ws)

    assert rec.get("retrieve_calls") is None
    assert citations is None


def test_ask_info_turn_retrieves_grounds_and_cites(monkeypatch):
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


def test_ask_advice_turn_retrieves(monkeypatch):
    # An explicit ask for guidance opens the corpus gate exactly like ask_info.
    passage = retrieval.Passage(
        text="Rest is a discipline, not a reward.",
        source_file="book.pdf", page=7, section=None, distance=0.2,
    )
    rec: dict = {}
    _setup(monkeypatch, rec, passages=[passage])
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("What should I do to rest without feeling guilty?")
        _, citations = _drain(ws)

    assert rec["retrieve_calls"] == ["What should I do to rest without feeling guilty?"]
    assert citations is not None and citations[0]["source_file"] == "book.pdf"


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


# ── meta frame (intent / persona / retrieval counts) ─────────────────────────


def test_meta_frame_carries_intent_and_persona(monkeypatch):
    rec: dict = {}
    _setup(monkeypatch, rec, passages=[])
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text('{"text": "I am so drained lately.", "mode": "coach"}')
        assert ws.receive_json() == {"type": "start"}
        meta = ws.receive_json()

    assert meta["type"] == "meta"
    assert meta["intent"] == intent_classifier.VENT
    assert meta["method"] == "rule"
    assert meta["persona"] == "coach"
    assert meta["retrieved"] == {"corpus": 0, "memory": 0, "episodes": 0}


# ── memory recall wiring at the socket boundary ──────────────────────────────


def _drain_with_memory(ws):
    """Read one reply; return (text, citations_frame_or_None, memory_frame_or_None)."""
    frame = ws.receive_json()
    if frame["type"] == "error":
        raise AssertionError(f"unexpected chat error: {frame}")
    assert frame == {"type": "start"}
    citations = memories = None
    out = []
    while True:
        frame = ws.receive_json()
        if frame["type"] == "error":
            raise AssertionError(f"unexpected chat error: {frame}")
        if frame["type"] == "done":
            break
        if frame["type"] == "meta":
            continue
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
    assert "shared with you before" not in rec["messages"][0]["content"]
