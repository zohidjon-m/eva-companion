"""R6 — the listen-first intent gate (V2 five-class taxonomy).

Covers the rule layer (deterministic, no model), the ambiguous → model fallback
path (model mocked), and the safe defaults: vent/process/ambient never retrieve;
an unparseable or unavailable model falls back to vent (never to advice).
"""

from __future__ import annotations

import asyncio

import pytest

from intent import classifier
from llm import client as llm_client


# ── rule layer (pure, no model) ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "I'm so drained today, work has been crushing me.",
        "I just needed to get that off my chest.",
        "Today felt heavy.",
    ],
)
def test_plain_venting_is_vent(text):
    # No question mark, no advice phrase, no reflective marker → vent.
    assert classifier.classify_rules(text) == classifier.VENT


@pytest.mark.parametrize(
    "text",
    [
        "I keep coming back to why it bothered me so much.",
        "Part of me thinks I already knew the answer.",
        "I'm trying to figure out what that day actually meant.",
        "The more I think about it, the less sure I am.",
    ],
)
def test_reflection_is_process(text):
    # Meaning-making out loud → process. Still listen-first (no retrieval).
    assert classifier.classify_rules(text) == classifier.PROCESS


@pytest.mark.parametrize(
    "text",
    [
        "What does the book say about patience?",
        "How was the retreat described?",
        "Is rest considered a virtue here?",
        "Tell me what page mentions forgiveness?",
    ],
)
def test_questions_are_ask_info(text):
    assert classifier.classify_rules(text) == classifier.ASK_INFO


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
def test_advice_requests_are_ask_advice(text):
    assert classifier.classify_rules(text) == classifier.ASK_ADVICE


@pytest.mark.parametrize(
    "text",
    [
        "hey",
        "thanks so much",
        "goodnight",
        "ok cool",
        "how are you?",
        "what's up?",
    ],
)
def test_greetings_and_acks_are_ambient(text):
    assert classifier.classify_rules(text) == classifier.AMBIENT


def test_advice_beats_question_mark():
    # "what should I do ...?" ends in a question mark but is clearly advice.
    assert classifier.classify_rules("What should I do?") == classifier.ASK_ADVICE


def test_short_substantive_line_is_not_ambient():
    # A terse but real feeling must not be swallowed as filler.
    assert classifier.classify_rules("I'm scared.") == classifier.VENT


def test_ambiguous_returns_none():
    # The canonical stuck-ness case is deferred to the model fallback.
    assert classifier.classify_rules("I don't know what to do.") is None


def test_empty_text_is_ambient():
    assert classifier.classify_rules("") == classifier.AMBIENT
    assert classifier.classify_rules("   ") == classifier.AMBIENT


def test_retrieves_property():
    assert classifier.IntentResult(classifier.VENT, "rule").retrieves is False
    assert classifier.IntentResult(classifier.PROCESS, "rule").retrieves is False
    assert classifier.IntentResult(classifier.AMBIENT, "rule").retrieves is False
    assert classifier.IntentResult(classifier.ASK_INFO, "rule").retrieves is True
    assert classifier.IntentResult(classifier.ASK_ADVICE, "rule").retrieves is True


# ── model fallback (ambiguous residue only) ──────────────────────────────────
# Async like the rest of the suite (test_llm.py / test_capture.py): driven with
# asyncio.run rather than a pytest-asyncio marker, so no extra plugin is needed.

def test_confident_rule_never_calls_model(monkeypatch):
    called = {"n": 0}

    async def fake_complete(*a, **k):
        called["n"] += 1
        return "vent"

    monkeypatch.setattr(llm_client, "provider_configured", lambda: True)
    monkeypatch.setattr(llm_client, "complete_chat", fake_complete)

    result = asyncio.run(classifier.classify("I'm exhausted and sad."))
    assert result.label == classifier.VENT
    assert result.method == "rule"
    assert called["n"] == 0  # rules resolved it; the model was never asked


def test_ambiguous_uses_model_fallback(monkeypatch):
    # A configured provider (local OR online) is reached on an ambiguous turn.
    monkeypatch.setattr(llm_client, "provider_configured", lambda: True)

    async def fake_complete(messages, **k):
        return "ask_advice"

    monkeypatch.setattr(llm_client, "complete_chat", fake_complete)

    result = asyncio.run(classifier.classify("I don't know what to do."))
    assert result.label == classifier.ASK_ADVICE
    assert result.method == "model"
    assert result.retrieves is True


def test_fallback_defaults_to_vent_when_no_provider(monkeypatch):
    # No provider configured → we must not reach for advice on an ambiguous turn.
    monkeypatch.setattr(llm_client, "provider_configured", lambda: False)
    result = asyncio.run(classifier.classify("I don't know what to do."))
    assert result.label == classifier.VENT
    assert result.retrieves is False


def test_fallback_defaults_to_vent_on_unparseable_reply(monkeypatch):
    monkeypatch.setattr(llm_client, "provider_configured", lambda: True)

    async def fake_complete(messages, **k):
        return "hmm, that's hard to say"

    monkeypatch.setattr(llm_client, "complete_chat", fake_complete)
    result = asyncio.run(classifier.classify("I don't know what to do."))
    assert result.label == classifier.VENT


def test_fallback_defaults_to_vent_on_model_error(monkeypatch):
    monkeypatch.setattr(llm_client, "provider_configured", lambda: True)

    async def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(llm_client, "complete_chat", boom)
    result = asyncio.run(classifier.classify("I don't know what to do."))
    assert result.label == classifier.VENT
