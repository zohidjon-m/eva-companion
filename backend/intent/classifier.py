"""Three-class intent classifier — gates retrieval before every chat turn.

Classifies one user message into exactly one of:

    vent            — expressing or processing feelings out loud, not seeking input.
    question        — asking for information or an answer.
    advice_request  — asking for guidance, advice, or help deciding what to do.

Only ``question`` and ``advice_request`` pull the corpus; ``vent`` bypasses
retrieval entirely (the listen-first rule, EVA_MEMORY_ARCHITECTURE §5.9 — the
discipline is enforced by what reaches the context window, not by the prompt).

Two layers, cheapest first:

  1. **Rule layer** (:func:`classify_rules`) — pure, deterministic, no model.
     Advice phrases ("what should I do", "any advice") win first; then a question
     mark or interrogative opener marks a ``question``; an explicit "I don't know
     what to do" kind of stuck-ness is treated as *ambiguous* and deferred; a
     message with none of these signals is ``vent``. This resolves the vast
     majority of turns with zero latency and a result the tests can pin exactly.

  2. **Model fallback** (:func:`classify`) — only for the ambiguous residue the
     rules return ``None`` for. One tiny, low-temperature model call labels it.
     If the model is missing or fails, we fall back to ``vent`` — the listen-first
     safe default: when unsure, don't reach for advice.
"""
# INTENT-SEAM: replace with full 5-class classifier (vent/process/ask_info/
# ask_advice/ambient). The real engine plugs in at :func:`classify`; the rest of
# the app only depends on IntentResult.label / .retrieves, which it will preserve.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from llm import client as llm_client
from llm import server as llm_server

log = logging.getLogger("eva.intent.classifier")

# The three labels this stub emits. Named so the seam and tests share one source.
VENT = "vent"
QUESTION = "question"
ADVICE_REQUEST = "advice_request"

# Labels that pull the corpus. ``vent`` is deliberately absent (listen-first).
_RETRIEVING_LABELS = frozenset({QUESTION, ADVICE_REQUEST})


@dataclass(frozen=True)
class IntentResult:
    """One classification outcome.

    ``label`` is one of the three class constants. ``method`` records how it was
    decided (``"rule"`` or ``"model"``) so the demo can show — and the tests can
    assert — that the fallback only fires on genuinely ambiguous turns.
    """

    label: str
    method: str

    @property
    def retrieves(self) -> bool:
        """Whether this intent should pull the corpus (question/advice only)."""
        return self.label in _RETRIEVING_LABELS


# Phrases that unambiguously ask for guidance. Written in NORMALIZED form
# (lowercase, apostrophes removed) so one spelling matches everyday variants —
# the same normalization trick as safety/crisis_check.py. Checked first because
# "what should I do about X?" is advice even though it also ends in a question
# mark. The plan's three anchors ("what should I do", "any advice", "help me
# think") are here plus their close neighbours.
_ADVICE_PHRASES: tuple[str, ...] = (
    "what should i do",
    "what do i do",
    "what would you do",
    "what do you think i should",
    "should i",
    "how do i",
    "how should i",
    "how can i",
    "how do you",
    "any advice",
    "some advice",
    "your advice",
    "give me advice",
    "need advice",
    "help me think",
    "help me decide",
    "help me figure",
    "help me understand",
    "what do you think",
    "do you think i",
    "is it a good idea",
    "would it be wise",
)

# Stuck-ness markers: the person is at a loss, but it is genuinely unclear whether
# they are asking Eva to help or just naming the feeling. These are NOT resolved
# by the rules — they hand off to the model fallback. "I don't know what to do"
# is the canonical case (plan §2 Phase 7 test). Kept narrow on purpose; a broad
# net here would route too many turns through the model.
_AMBIGUOUS_MARKERS: tuple[str, ...] = (
    "dont know what to do",
    "not sure what to do",
    "no idea what to do",
    "dont know what to think",
    "dont know what to say",
    "cant decide",
    "what to do",
)

# Interrogative openers that signal a question even without a question mark
# ("how was the book"). "should"/"how" are absent here on purpose — those are
# caught by the advice layer first.
_QUESTION_OPENERS: frozenset[str] = frozenset(
    {"what", "why", "when", "where", "who", "whom", "whose", "which",
     "can", "could", "would", "is", "are", "was", "were", "do", "does",
     "did", "will", "am", "may", "might"}
)

# Collapse anything that isn't a letter or digit to a single space (mirrors
# crisis_check), so punctuation and odd spacing never hide a phrase.
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Lowercase, strip apostrophes, collapse non-word chars to spaces.

    "What should I do?" -> "what should i do". This is what lets one listed
    phrase match its everyday punctuation/spacing variants.
    """
    lowered = text.lower().replace("’", "'").replace("'", "")
    return _NON_WORD.sub(" ", lowered).strip()


def classify_rules(text: str) -> str | None:
    """Classify by deterministic rules, or return ``None`` if ambiguous.

    Pure and side-effect free. Order matters and encodes the priority:

      1. an advice phrase  -> ``advice_request`` (even with a trailing "?")
      2. a stuck-ness marker -> ``None`` (defer to the model fallback)
      3. a question mark, or an interrogative opener -> ``question``
      4. nothing of the above -> ``vent``

    The empty string is treated as ``vent`` (there is nothing to retrieve for).
    """
    if not text or not text.strip():
        return VENT

    normalized = _normalize(text)
    words = normalized.split()

    # 1. Explicit ask for guidance wins outright.
    for phrase in _ADVICE_PHRASES:
        if phrase in normalized:
            return ADVICE_REQUEST

    # 2. "I don't know what to do" — genuinely could be a plea or a sigh. Defer.
    for marker in _AMBIGUOUS_MARKERS:
        if marker in normalized:
            return None

    # 3. A question mark, or a sentence that opens with an interrogative word.
    if "?" in text or (words and words[0] in _QUESTION_OPENERS):
        return QUESTION

    # 4. No request signal at all — they are venting/processing.
    return VENT


# The fallback classification prompt. Gemma has no system role (CLAUDE.md), so the
# instruction is folded into the user message at the call site, exactly like the
# chat and journal-ack paths. Asking for a single bare label keeps parsing trivial.
_FALLBACK_INSTRUCTION = (
    "Classify the person's message into exactly one of these labels:\n"
    "- vent: they are expressing or processing feelings out loud, not seeking input.\n"
    "- question: they are asking for information or a factual answer.\n"
    "- advice_request: they are asking for guidance, advice, or help deciding what to do.\n"
    "Reply with ONLY the single label word (vent, question, or advice_request) "
    "and nothing else."
)

# Tiny, deterministic call: a few tokens out, low temperature for a stable label.
_FALLBACK_MAX_TOKENS = 8
_FALLBACK_TEMPERATURE = 0.0


def _parse_label(reply: str) -> str | None:
    """Extract a known label from the model's reply, or ``None`` if none present.

    Tolerant of stray words/punctuation: checks for advice first (so the
    substring ``question`` inside a longer reply can't shadow it) then question,
    then vent.
    """
    normalized = _normalize(reply)
    if ADVICE_REQUEST.replace("_", " ") in normalized or "advice" in normalized:
        return ADVICE_REQUEST
    if QUESTION in normalized:
        return QUESTION
    if VENT in normalized:
        return VENT
    return None


async def classify(text: str) -> IntentResult:
    """Classify one message, using the model only for the ambiguous residue.

    The rule layer answers first; on a confident answer we return immediately
    (``method="rule"``). Only when the rules return ``None`` do we make one small
    model call (``method="model"``). If the model is unavailable or the call
    fails, we default to ``vent`` — the listen-first safe choice — so a missing
    model never causes Eva to reach for advice she wasn't clearly asked for.

    Always logs the decision, so the vent-bypass is observable in the demo logs
    exactly as the Phase-7 acceptance test requires.
    """
    label = classify_rules(text)
    if label is not None:
        result = IntentResult(label=label, method="rule")
        log.info("intent=%s (rule)", result.label)
        return result

    # Ambiguous. Defer to the model — but only if it is actually available.
    if not llm_server.model_present():
        log.info("intent=%s (rule, model unavailable for fallback)", VENT)
        return IntentResult(label=VENT, method="rule")

    messages = [{"role": "user", "content": f"{_FALLBACK_INSTRUCTION}\n\nMessage: {text}"}]
    try:
        reply = await llm_client.complete_chat(
            messages,
            max_tokens=_FALLBACK_MAX_TOKENS,
            temp=_FALLBACK_TEMPERATURE,
            top_p=None,
            top_k=None,
            priority=True,  # on the critical path before the live reply
        )
    except Exception:  # noqa: BLE001 — a failed classification must never break chat
        log.exception("intent fallback model call failed; defaulting to vent")
        return IntentResult(label=VENT, method="model")

    parsed = _parse_label(reply)
    if parsed is None:
        log.info("intent fallback returned %r (unparseable); defaulting to vent", reply.strip())
        return IntentResult(label=VENT, method="model")
    log.info("intent=%s (model fallback)", parsed)
    return IntentResult(label=parsed, method="model")
