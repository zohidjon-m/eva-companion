"""Five-class intent classifier — gates retrieval before every chat turn.

Classifies one user message into exactly one of the V2 taxonomy labels
(EVA_SYSTEM_DESIGN §5.11 / §7.1):

    vent        — expressing feelings out loud, not seeking input.
    process     — reflecting, making meaning of an experience out loud.
    ask_info    — asking for information or a factual answer.
    ask_advice  — asking for guidance, advice, or help deciding what to do.
    ambient     — a greeting, acknowledgment, or social aside — not a substantive
                  turn.

Only ``ask_info`` and ``ask_advice`` pull the corpus; ``vent``, ``process`` and
``ambient`` bypass retrieval entirely (the listen-first rule,
EVA_MEMORY_ARCHITECTURE §5.9 — the discipline is enforced by what reaches the
context window, not by the prompt).

Two layers, cheapest first:

  1. **Rule layer** (:func:`classify_rules`) — pure, deterministic, no model.
     Advice phrases ("what should I do", "any advice") win first; then a question
     mark or interrogative opener marks ``ask_info``; short greetings/acks mark
     ``ambient``; reflective "meaning-making" markers mark ``process``; an explicit
     "I don't know what to do" kind of stuck-ness is treated as *ambiguous* and
     deferred; a message with none of these signals is ``vent``. This resolves the
     vast majority of turns with zero latency and a result the tests can pin exactly.

  2. **Model fallback** (:func:`classify`) — only for the ambiguous residue the
     rules return ``None`` for. One tiny, low-temperature model call labels it.
     If the model is missing or fails, we fall back to ``vent`` — the listen-first
     safe default: when unsure, don't reach for advice.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from llm import client as llm_client

log = logging.getLogger("eva.intent.classifier")

# The five V2 labels this classifier emits. Named so the seam and tests share one
# source of truth (EVA_SYSTEM_DESIGN §5.11).
VENT = "vent"
PROCESS = "process"
ASK_INFO = "ask_info"
ASK_ADVICE = "ask_advice"
AMBIENT = "ambient"

# The full label set, for the model-fallback prompt and validation.
LABELS = frozenset({VENT, PROCESS, ASK_INFO, ASK_ADVICE, AMBIENT})

# Labels that pull the corpus. Only the two "ask" intents retrieve; vent, process
# and ambient are all listen-first (the model literally cannot reach for advice).
_RETRIEVING_LABELS = frozenset({ASK_INFO, ASK_ADVICE})


@dataclass(frozen=True)
class IntentResult:
    """One classification outcome.

    ``label`` is one of the five class constants. ``method`` records how it was
    decided (``"rule"`` or ``"model"``) so the demo can show — and the tests can
    assert — that the fallback only fires on genuinely ambiguous turns.
    """

    label: str
    method: str

    @property
    def retrieves(self) -> bool:
        """Whether this intent should pull the corpus (ask_info/ask_advice only)."""
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

# Ambient turns: greetings, sign-offs, and short acknowledgments that carry no
# substance to reflect on or retrieve for ("hey", "thanks", "goodnight"). A whole
# message made only of these tokens — and short — is ambient, so trivial turns
# don't drag in memory/profile context or a full reflective reply. Checked as a
# set of tokens plus a small set of fixed phrases; kept narrow so a real short
# message ("scared") is never mistaken for filler.
_AMBIENT_TOKENS: frozenset[str] = frozenset(
    {"hi", "hey", "hello", "yo", "sup", "hiya", "heya",
     "thanks", "thank", "thx", "ty", "cheers",
     "ok", "okay", "k", "kk", "cool", "nice", "great", "awesome", "sweet",
     "got", "it", "gotcha", "sounds", "good", "fine", "alright",
     "night", "goodnight", "morning", "evening", "afternoon",
     "bye", "later", "cya", "ttyl", "peace",
     "lol", "haha", "hah", "hehe", "yep", "yeah", "yup", "nope", "no", "yes",
     "you", "u", "so", "much", "a", "lot", "how", "are", "whats", "up", "im",
     "doing", "well", "and"}
)
_AMBIENT_PHRASES: tuple[str, ...] = (
    "how are you",
    "hows it going",
    "whats up",
    "good morning",
    "good night",
    "good evening",
    "have a good one",
    "talk to you later",
    "thank you so much",
    "thanks so much",
    "sounds good",
    "have a good day",
)
_AMBIENT_MAX_WORDS = 4

# Reflective / meaning-making markers: the person is thinking *out loud* about what
# something means, not just naming a feeling (vent) and not asking for input
# (advice). ``process`` bypasses retrieval exactly like vent — it is still
# listen-first — but is labelled distinctly so the engine and the debug panel can
# tell reflection from raw venting. Kept to phrases that clearly signal
# meaning-making rather than a plain feeling report.
_PROCESS_MARKERS: tuple[str, ...] = (
    "i think it means",
    "trying to figure out",
    "im trying to figure",
    "i keep coming back to",
    "i keep thinking about",
    "part of me",
    "makes me wonder",
    "i wonder if",
    "i realize",
    "i realized",
    "the more i think",
    "im starting to see",
    "what i notice is",
    "i guess what",
    "im processing",
    "sitting with",
    "why it bothered",
    "why that bothered",
    "trying to understand why",
    "makes me think about",
)


def _is_ambient(normalized: str, words: list[str]) -> bool:
    """Whether a message is a greeting/ack/aside with nothing to reflect on.

    True when the whole (short) message is a known ambient phrase, or is made up
    only of ambient tokens. Length-guarded so a terse but substantive line
    ("i'm scared", "everything hurts") is never swallowed as filler.
    """
    if not words:
        return False
    if normalized in _AMBIENT_PHRASES:
        return True
    return len(words) <= _AMBIENT_MAX_WORDS and all(w in _AMBIENT_TOKENS for w in words)

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

      1. an advice phrase   -> ``ask_advice`` (even with a trailing "?")
      2. a stuck-ness marker -> ``None`` (defer to the model fallback)
      3. a greeting/ack/aside -> ``ambient``
      4. a question mark, or an interrogative opener -> ``ask_info``
      5. a reflective meaning-making marker -> ``process``
      6. nothing of the above -> ``vent``

    The empty string is treated as ``ambient`` (nothing to reflect on or retrieve
    for). Ambient is checked before the question rule so "what's up?" reads as a
    greeting, not an information request.
    """
    if not text or not text.strip():
        return AMBIENT

    normalized = _normalize(text)
    words = normalized.split()

    # 1. Explicit ask for guidance wins outright.
    for phrase in _ADVICE_PHRASES:
        if phrase in normalized:
            return ASK_ADVICE

    # 2. "I don't know what to do" — genuinely could be a plea or a sigh. Defer.
    for marker in _AMBIGUOUS_MARKERS:
        if marker in normalized:
            return None

    # 3. A greeting, sign-off, or bare acknowledgment — no substance to retrieve
    #    for or reflect on. Checked before the question rule so social openers that
    #    happen to be phrased as questions ("how are you?") stay ambient.
    if _is_ambient(normalized, words):
        return AMBIENT

    # 4. A question mark, or a sentence that opens with an interrogative word.
    if "?" in text or (words and words[0] in _QUESTION_OPENERS):
        return ASK_INFO

    # 5. Thinking out loud / making meaning of an experience — still listen-first
    #    (no retrieval), but distinct from raw venting.
    for marker in _PROCESS_MARKERS:
        if marker in normalized:
            return PROCESS

    # 6. No request or reflection signal at all — they are venting.
    return VENT


# The fallback classification prompt. Gemma has no system role (CLAUDE.md), so the
# instruction is folded into the user message at the call site, exactly like the
# chat and journal-ack paths. Asking for a single bare label keeps parsing trivial.
_FALLBACK_INSTRUCTION = (
    "Classify the person's message into exactly one of these labels:\n"
    "- vent: they are expressing feelings out loud, not seeking input.\n"
    "- process: they are reflecting or making meaning of an experience out loud.\n"
    "- ask_info: they are asking for information or a factual answer.\n"
    "- ask_advice: they are asking for guidance, advice, or help deciding what to do.\n"
    "- ambient: a greeting, acknowledgment, or social aside, not a real turn.\n"
    "Reply with ONLY the single label word (vent, process, ask_info, ask_advice, "
    "or ambient) and nothing else."
)

# Tiny, deterministic call: a few tokens out, low temperature for a stable label.
_FALLBACK_MAX_TOKENS = 8
_FALLBACK_TEMPERATURE = 0.0


def _parse_label(reply: str) -> str | None:
    """Extract a known label from the model's reply, or ``None`` if none present.

    Tolerant of stray words/punctuation. Order matters: the more specific "ask_*"
    labels are checked before the plainer ones so a substring can't shadow them
    ("ask_advice" contains "advice"; "ask_info" contains "info").
    """
    normalized = _normalize(reply)
    if "ask advice" in normalized or "advice" in normalized:
        return ASK_ADVICE
    if "ask info" in normalized or "info" in normalized:
        return ASK_INFO
    if AMBIENT in normalized:
        return AMBIENT
    if PROCESS in normalized:
        return PROCESS
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

    # Ambiguous. Defer to the model — but only if a provider is actually
    # configured. Checking provider readiness (not just the local GGUF) means an
    # online-provider setup still reaches the classifier: in online mode the local
    # model can be absent while the selected provider is fully configured.
    if not llm_client.provider_configured():
        log.info("intent=%s (rule, no provider configured for fallback)", VENT)
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
