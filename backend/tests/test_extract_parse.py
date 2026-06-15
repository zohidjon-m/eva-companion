"""Phase 2 Step B — the extraction JSON parser handles every bad-output case.

These are pure, model-free tests of the parse/validate layer in memory.extract —
the safety net that turns whatever a small model emits into either a clean L1
record or a clean failure (which the caller stores as ``null_stored``).
"""

from __future__ import annotations

import pytest

from memory import extract

GOOD = (
    '{"mood": -2, "emotions": [{"name": "anxiety", "intensity": 0.7}], '
    '"entities": [{"name": "Sam", "type": "person", "normalized": "sam"}], '
    '"themes": ["work"], "events": ["had a tense meeting"], '
    '"stated_goals": [{"text": "stay calm", "is_new": false}], '
    '"behaviors": ["apologized"], "decisions": ["call her tomorrow"], '
    '"open_loops": [{"description": "unresolved chat", "status": "open"}], '
    '"self_judgments": ["felt I overreacted"], '
    '"summary": "They had a hard day at work and felt anxious before a meeting. '
    'They apologized afterward and resolved to follow up the next day."}'
)


def test_clean_object_parses():
    rec = extract.parse_extraction(GOOD)
    assert rec["mood"] == -2
    assert rec["emotions"][0]["name"] == "anxiety"
    assert rec["entities"][0]["type"] == "person"
    assert rec["summary"].startswith("They had a hard day")
    # All eleven canonical keys present.
    assert set(rec) == {
        "mood", "emotions", "entities", "themes", "events", "stated_goals",
        "behaviors", "decisions", "open_loops", "self_judgments", "summary",
    }


@pytest.mark.parametrize("bad", [
    "",                              # empty string
    "   \n  ",                       # whitespace only
    "I could not do that",           # prose, no JSON
    '{"mood": 1, "summary": "x"',    # truncated / unbalanced
    "{not valid json at all}",       # braces but not JSON
])
def test_unrecoverable_inputs_raise(bad):
    with pytest.raises(ValueError):
        extract.parse_extraction(bad)


def test_valid_json_without_summary_is_rejected():
    # Parses as JSON but has no usable summary → not a 'done' extraction.
    with pytest.raises(ValueError):
        extract.parse_extraction('{"mood": 1, "themes": ["x"]}')


def test_summary_too_short_is_rejected():
    with pytest.raises(ValueError):
        extract.parse_extraction('{"summary": "ok"}')


def test_prose_and_code_fence_wrapping_is_tolerated():
    wrapped = f"Sure! Here is the JSON:\n```json\n{GOOD}\n```\nHope that helps."
    rec = extract.parse_extraction(wrapped)
    assert rec["summary"].startswith("They had a hard day")


def test_hyphenated_keys_are_normalized():
    obj = (
        '{"summary": "A reasonably long summary sentence about the day and feelings.", '
        '"self-judgments": ["was too harsh"], "stated-goals": [{"text": "rest", "is_new": true}], '
        '"open-loops": [{"description": "talk to mom", "status": "open"}]}'
    )
    rec = extract.parse_extraction(obj)
    assert rec["self_judgments"] == ["was too harsh"]
    assert rec["stated_goals"][0]["text"] == "rest"
    assert rec["open_loops"][0]["description"] == "talk to mom"


def test_field_coercion_and_clamping():
    obj = (
        '{"summary": "A reasonably long summary sentence about the day and the feelings within.", '
        '"mood": 99, '
        '"emotions": [{"name": "JOY", "intensity": 5}, {"name": "x"}], '
        '"entities": [{"name": "Acme", "type": "company", "normalized": "acme"}, '
        '             {"name": "Mara", "type": "person", "normalized": "mara"}], '
        '"open_loops": [{"description": "thing", "status": "nonsense"}], '
        '"themes": ["work", "", 42]}'
    )
    rec = extract.parse_extraction(obj)
    assert rec["mood"] == 5                       # clamped from 99 into -5..5
    assert rec["emotions"][0]["intensity"] == 1.0  # clamped from 5 into 0..1
    assert rec["emotions"][0]["name"] == "joy"     # lowercased
    # Invalid entity type dropped; valid one kept.
    assert [e["name"] for e in rec["entities"]] == ["Mara"]
    # Bad open-loop status coerced to 'open'.
    assert rec["open_loops"][0]["status"] == "open"
    # Empty theme dropped, number coerced to string.
    assert rec["themes"] == ["work", "42"]


def test_mood_may_be_null():
    obj = '{"summary": "A bare factual note with no emotional content whatsoever today.", "mood": null}'
    rec = extract.parse_extraction(obj)
    assert rec["mood"] is None
