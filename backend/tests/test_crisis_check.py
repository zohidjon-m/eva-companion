"""Phase 4 — interim crisis-care keyword scan.

Confirms the four plan-anchor phrases (and common variants/punctuation) trip the
scan, ordinary messages do not, and the addendum is care-shaped guidance (never a
script, never a suppression).
"""

from __future__ import annotations

import pytest

from safety import crisis_check


@pytest.mark.parametrize(
    "message",
    [
        "I want to end my life",
        "sometimes I just want to kill myself",
        "Don't want to be here anymore",  # apostrophe + capitalization
        "I keep thinking about how to hurt myself.",
        "honestly I just want to die",
        "I feel like everyone would be better off dead",
        "kill   myself",  # odd spacing
    ],
)
def test_crisis_phrases_match(message):
    assert crisis_check.is_crisis(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "I had a long day and I'm exhausted",
        "I want to end my workday early and go home",
        "the gym killed me today, my legs are dead",
        "",
        "I'm proud I held back during the meeting",
    ],
)
def test_ordinary_messages_do_not_match(message):
    assert crisis_check.is_crisis(message) is False


def test_addendum_is_care_guidance_not_a_script():
    text = crisis_check.crisis_addendum()
    assert text.startswith("CRISIS-CARE")
    lowered = text.lower()
    # Encourages reaching out, warmth over procedure...
    assert "reach out" in lowered
    assert "warmth" in lowered
    # ...and explicitly does not end the conversation or hand off to a script.
    assert "do not end the conversation" in lowered
