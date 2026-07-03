"""R6 — the conversation-engine pipeline steps in isolation.

These exercise each step over a bare :class:`engine.TurnState`, with the
collaborators stubbed, so the listen-first gate, recent-episode de-duplication,
the crisis-care input seam, and the no-invented-citations output seam are pinned
independently of the socket.
"""

from __future__ import annotations

import asyncio

import engine
from intent import classifier as intent_classifier
from memory import profile, retrieval
from safety import crisis_check


def _intent(monkeypatch, label, method="rule"):
    async def fake_classify(text):
        return intent_classifier.IntentResult(label=label, method=method)

    monkeypatch.setattr(intent_classifier, "classify", fake_classify)


def _stub_context(monkeypatch, *, memories=(), episodes=(), passages=(), slices=""):
    monkeypatch.setattr(retrieval, "recall_memories", lambda *a, **k: list(memories))
    monkeypatch.setattr(retrieval, "recent_episodes", lambda *a, **k: list(episodes))
    monkeypatch.setattr(retrieval, "retrieve_corpus", lambda *a, **k: list(passages))
    monkeypatch.setattr(profile, "slices_for_prompt", lambda topic: slices)


# ── classify ─────────────────────────────────────────────────────────────────


def test_classify_sets_intent(monkeypatch):
    _intent(monkeypatch, intent_classifier.ASK_ADVICE)
    state = engine.TurnState(text="what should I do?")
    asyncio.run(engine.classify(state))
    assert state.intent.label == intent_classifier.ASK_ADVICE
    assert state.intent.retrieves is True


# ── assemble_context: the listen-first gate ──────────────────────────────────


def test_vent_intent_never_retrieves_corpus(monkeypatch):
    calls = {"n": 0}

    def spy_corpus(*a, **k):
        calls["n"] += 1
        return []

    _intent(monkeypatch, intent_classifier.VENT)
    _stub_context(monkeypatch)
    monkeypatch.setattr(retrieval, "retrieve_corpus", spy_corpus)

    state = engine.TurnState(text="today was heavy")
    asyncio.run(engine.classify(state))
    asyncio.run(engine.assemble_context(state))

    assert calls["n"] == 0  # the gate withheld retrieval entirely
    assert state.citations == []
    assert state.corpus_context == ""


def test_ask_info_intent_retrieves_corpus(monkeypatch):
    passage = retrieval.Passage(
        text="p", source_file="book.pdf", page=1, section=None, distance=0.1,
    )
    _intent(monkeypatch, intent_classifier.ASK_INFO)
    _stub_context(monkeypatch, passages=[passage])

    state = engine.TurnState(text="what does the book say?")
    asyncio.run(engine.classify(state))
    asyncio.run(engine.assemble_context(state))

    assert state.passages == [passage]
    assert state.citations == [passage.as_citation()]
    assert "p" in state.corpus_context


def test_recent_episodes_dedup_against_recall(monkeypatch):
    # An entry surfaced by relevance recall must not also appear as a recent
    # episode — otherwise the same day is injected into the prompt twice.
    mem = retrieval.Memory(
        entry_id="e1", date="2026-06-01", summary="recalled one",
        mood=0, themes=[], distance=0.2,
    )
    ep_dup = retrieval.RecentEpisode(
        entry_id="e1", date="2026-06-01", summary="recalled one", mood=0, themes=[],
    )
    ep_new = retrieval.RecentEpisode(
        entry_id="e2", date="2026-06-02", summary="only in episodes", mood=0, themes=[],
    )
    _intent(monkeypatch, intent_classifier.VENT)
    _stub_context(monkeypatch, memories=[mem], episodes=[ep_dup, ep_new])

    state = engine.TurnState(text="lately")
    asyncio.run(engine.classify(state))
    asyncio.run(engine.assemble_context(state))

    assert "only in episodes" in state.episodes_context
    assert "recalled one" not in state.episodes_context  # de-duplicated
    # The meta count reflects what actually reached the prompt (dedup applied),
    # never the pre-dedup total — so the audit contract can't over-report.
    assert state.meta_frame()["retrieved"]["episodes"] == 1


# ── check_in: crisis-care seam + prompt finalization ─────────────────────────


def test_check_in_injects_crisis_addendum_and_builds_messages(monkeypatch):
    monkeypatch.setattr(crisis_check, "is_crisis", lambda text: True)
    monkeypatch.setattr(crisis_check, "crisis_addendum", lambda: "CRISIS-CARE-TEXT")

    state = engine.TurnState(text="I want to disappear")
    asyncio.run(engine.check_in(state))

    assert state.addendum == "CRISIS-CARE-TEXT"
    assert state.messages  # the model input was assembled
    assert "CRISIS-CARE-TEXT" in state.messages[0]["content"]
    assert "I want to disappear" in state.messages[0]["content"]


def test_check_in_no_crisis_leaves_no_addendum(monkeypatch):
    monkeypatch.setattr(crisis_check, "is_crisis", lambda text: False)
    state = engine.TurnState(text="a calm ordinary day")
    asyncio.run(engine.check_in(state))
    assert state.addendum == ""
    assert "CRISIS-CARE" not in state.messages[0]["content"]


# ── check_out: no invented citations ─────────────────────────────────────────


def test_check_out_drops_unbacked_citation():
    backed = retrieval.Passage(
        text="real", source_file="book.pdf", page=1, section=None, distance=0.1,
    )
    state = engine.TurnState(text="q")
    state.passages = [backed]
    state.citations = [
        backed.as_citation(),
        # A citation with no backing passage — the thing check_out must drop.
        {"source_file": "ghost.pdf", "page": 9, "section": None,
         "label": "ghost.pdf · p. 9", "text": "invented"},
    ]

    engine.check_out(state)
    assert state.citations == [backed.as_citation()]


def test_check_out_keeps_all_backed_citations():
    p = retrieval.Passage(
        text="real", source_file="book.pdf", page=1, section=None, distance=0.1,
    )
    state = engine.TurnState(text="q")
    state.passages = [p]
    state.citations = [p.as_citation()]
    engine.check_out(state)
    assert state.citations == [p.as_citation()]


# ── meta frame ───────────────────────────────────────────────────────────────


def test_meta_frame_shape(monkeypatch):
    p = retrieval.Passage(
        text="p", source_file="b.pdf", page=1, section=None, distance=0.1,
    )
    mem = retrieval.Memory(
        entry_id="e1", date="2026-06-01", summary="s", mood=0, themes=[], distance=0.2,
    )
    ep = retrieval.RecentEpisode(
        entry_id="e2", date="2026-06-02", summary="s2", mood=0, themes=[],
    )
    _intent(monkeypatch, intent_classifier.ASK_INFO)
    _stub_context(monkeypatch, memories=[mem], episodes=[ep], passages=[p])

    state = engine.TurnState(text="what does the book say?", mode="mentor")
    asyncio.run(engine.classify(state))
    asyncio.run(engine.assemble_context(state))

    meta = state.meta_frame()
    assert meta["type"] == "meta"
    assert meta["intent"] == intent_classifier.ASK_INFO
    assert meta["persona"] == "mentor"
    assert meta["retrieved"] == {"corpus": 1, "memory": 1, "episodes": 1}
