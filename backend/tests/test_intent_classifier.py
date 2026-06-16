"""Phase 7 — the listen-first intent gate.

Covers the rule layer (deterministic, no model), the ambiguous → model fallback
path (model mocked), and the safe defaults: a vent never retrieves; an
unparseable or unavailable model falls back to vent (never to advice).
"""

from __future__ import annotations

import asyncio

import pytest

from intent import classifier
from llm import client as llm_client
from llm import server as llm_server


# ── rule layer (pure, no model) ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "I'm so drained today, work has been crushing me.",
        "I just needed to get that off my chest.",
        "Today felt heavy and I don't really know why.",
    ],
)
def test_plain_venting_is_vent(text):
    # No question mark, no advice phrase, no interrogative opener → vent.
    assert classifier.classify_rules(text) == classifier.VENT


@pytest.mark.parametrize(
    "text",
    [
        "What does the book say about patience?",
        "How was the retreat described?",
        "Is rest considered a virtue here?",
        "Tell me what page mentions forgiveness?",
    ],
)
def test_questions_are_question(text):
    assert classifier.classify_rules(text) == classifier.QUESTION


@pytest.mark.parametrize(
    "text",
    [
        "What should I do about my brother?",
        "Should I quit my job?",
        "Any advice on staying disciplined?",
        "Help me think through this decision.",
        "How do I forgive someone who won't apologize?",
    ],
)
def test_advice_requests_are_advice(text):
    assert classifier.classify_rules(text) == classifier.ADVICE_REQUEST


def test_advice_beats_question_mark():
    # "what should I do ...?" ends in a question mark but is clearly advice.
    assert classifier.classify_rules("What should I do?") == classifier.ADVICE_REQUEST


def test_ambiguous_returns_none():
    # The canonical stuck-ness case is deferred to the model fallback.
    assert classifier.classify_rules("I don't know what to do.") is None


def test_empty_text_is_vent():
    assert classifier.classify_rules("") == classifier.VENT
    assert classifier.classify_rules("   ") == classifier.VENT


def test_retrieves_property():
    assert classifier.IntentResult(classifier.VENT, "rule").retrieves is False
    assert classifier.IntentResult(classifier.QUESTION, "rule").retrieves is True
    assert classifier.IntentResult(classifier.ADVICE_REQUEST, "rule").retrieves is True


# ── model fallback (ambiguous residue only) ──────────────────────────────────
# Async like the rest of the suite (test_llm.py / test_capture.py): driven with
# asyncio.run rather than a pytest-asyncio marker, so no extra plugin is needed.

def test_confident_rule_never_calls_model(monkeypatch):
    called = {"n": 0}

    async def fake_complete(*a, **k):
        called["n"] += 1
        return "vent"

    monkeypatch.setattr(llm_server, "model_present", lambda: True)
    monkeypatch.setattr(llm_client, "complete_chat", fake_complete)

    result = asyncio.run(classifier.classify("I'm exhausted and sad."))
    assert result.label == classifier.VENT
    assert result.method == "rule"
    assert called["n"] == 0  # rules resolved it; the model was never asked


def test_ambiguous_uses_model_fallback(monkeypatch):
    monkeypatch.setattr(llm_server, "model_present", lambda: True)

    async def fake_complete(messages, **k):
        return "advice_request"

    monkeypatch.setattr(llm_client, "complete_chat", fake_complete)

    result = asyncio.run(classifier.classify("I don't know what to do."))
    assert result.label == classifier.ADVICE_REQUEST
    assert result.method == "model"
    assert result.retrieves is True


def test_fallback_defaults_to_vent_when_model_missing(monkeypatch):
    # No model → we must not reach for advice on an ambiguous turn.
    monkeypatch.setattr(llm_server, "model_present", lambda: False)
    result = asyncio.run(classifier.classify("I don't know what to do."))
    assert result.label == classifier.VENT
    assert result.retrieves is False


def test_fallback_defaults_to_vent_on_unparseable_reply(monkeypatch):
    monkeypatch.setattr(llm_server, "model_present", lambda: True)

    async def fake_complete(messages, **k):
        return "hmm, that's hard to say"

    monkeypatch.setattr(llm_client, "complete_chat", fake_complete)
    result = asyncio.run(classifier.classify("I don't know what to do."))
    assert result.label == classifier.VENT


def test_fallback_defaults_to_vent_on_model_error(monkeypatch):
    monkeypatch.setattr(llm_server, "model_present", lambda: True)

    async def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(llm_client, "complete_chat", boom)
    result = asyncio.run(classifier.classify("I don't know what to do."))
    assert result.label == classifier.VENT
