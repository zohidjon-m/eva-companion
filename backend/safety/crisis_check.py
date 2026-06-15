"""Interim crisis-care keyword scan — runs before the prompt reaches the model.

This is the Phase-4 stopgap that stays active until the real guardrail path is
built. It does ONE thing: a cheap, deterministic substring scan of the user's
message. On a match it returns a short crisis-aware addendum that the chat
handler appends to the persona block for that single turn — nothing more.

What it deliberately does NOT do (CLAUDE.md "Eva's behavior" + plan §2 Phase 4):
  * It never suppresses, delays, or replaces Eva's reply.
  * It never hands off to a canned bot script or a hotline auto-responder.
  * It never decides the words Eva says — it only sharpens the care she already
    owes (the persona's "Care" section already carries this duty in every turn).

The effect of a match is purely additive: Eva is reminded, for this turn, to
acknowledge the weight, stay present, and gently encourage reaching out to a
trusted person or professional. A false positive therefore costs nothing but a
little extra warmth, which is the right way for an interim floor to fail.

Matching is intentionally simple — normalize the text (lowercase, drop
apostrophes, collapse punctuation to spaces) then look for known phrases. A small
local model can miss these signals, so this floor catches the explicit ones
deterministically.
"""
# CRISIS-SEAM: replaced by NeMo guardrails + the full crisis-care path (plan §3).

from __future__ import annotations

import logging
import re

log = logging.getLogger("eva.safety.crisis_check")

# Phrases that signal possible self-harm / crisis, written in NORMALIZED form:
# lowercase, apostrophes removed ("don't" -> "dont"), so a single spelling here
# matches the common surface variants. Kept close to the plan's listed keywords
# plus their near neighbours. This is a care trigger, not a diagnosis — erring
# toward inclusion is intended.
_CRISIS_PHRASES: tuple[str, ...] = (
    # the plan's four anchors (+ spacing/apostrophe variants via normalization)
    "end my life",
    "kill myself",
    "kill my self",
    "dont want to be here",
    "do not want to be here",
    "hurt myself",
    # close variants of the same intent
    "killing myself",
    "take my own life",
    "end my own life",
    "end it all",
    "harm myself",
    "harming myself",
    "self harm",
    "want to die",
    "wanna die",
    "want to be dead",
    "wish i was dead",
    "wish i were dead",
    "better off dead",
    "dont want to be alive",
    "dont want to live",
    "dont want to exist",
    "no reason to live",
    "nothing to live for",
    "cant go on",
    "suicidal",
    "suicide",
)

# Collapse anything that isn't a letter or digit to a single space, so
# punctuation and odd spacing never hide a phrase.
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Lowercase, strip apostrophes, and collapse non-word chars to spaces.

    "Don't want to be here." -> "dont want to be here". This is what makes one
    listed phrase match its everyday variants without enumerating punctuation.
    """
    lowered = text.lower().replace("’", "'").replace("'", "")
    collapsed = _NON_WORD.sub(" ", lowered)
    return collapsed.strip()


def is_crisis(text: str) -> bool:
    """Return True if the message contains a known crisis phrase.

    Pure and side-effect free apart from a log line on a hit (so the interim
    floor is observable during the demo). Called once per turn, before the prompt
    is assembled.
    """
    if not text:
        return False
    normalized = _normalize(text)
    for phrase in _CRISIS_PHRASES:
        if phrase in normalized:
            log.warning("crisis keyword matched (%r); appending crisis-care addendum", phrase)
            return True
    return False


# The text appended to the persona block on a match. It re-asserts the persona's
# "Care" duty for this one turn; it is guidance to Eva, never a script she reads
# out. The "CRISIS-CARE" prefix is a stable marker the tests assert on.
_CRISIS_ADDENDUM = (
    "CRISIS-CARE — this message may signal that the person is in crisis or "
    "thinking about harming themselves. Take it seriously and respond with "
    "warmth, not procedure. First, acknowledge the weight of what they said and "
    "stay with them so they don't feel alone. Then, in this same reply, gently "
    "encourage them to reach out to someone they trust or a professional who can "
    "be with them in person — make sure your reply includes that encouragement "
    "to reach out, offered softly, never as an order. Do not give any details "
    "about methods of harm, do not lecture, and do not end the conversation. "
    "Stay in your own warm voice as Eva — this only sharpens the care you "
    "already give."
)


def crisis_addendum() -> str:
    """Return the crisis-care addendum to append to the persona block.

    A constant for now; exposed as a function so the wording lives in one place
    and the future guardrail path can swap in a richer, context-aware version
    without changing the call site.
    """
    return _CRISIS_ADDENDUM
