"""Phase 13 — the L3 profile seam: schema, slices, the md↔json sync, degrade.

Covers what the plan's "Done when" and tests require:
  * profile.json conforms EXACTLY to EVA_MEMORY_ARCHITECTURE §7.2 (the demo seed
    is the fixture);
  * get_slices surfaces the user's stated goal for an on-topic question
    ("should I skip the gym?") and stays quiet on an off-topic one;
  * editing profile.md round-trips into profile.json and registers a user anchor,
    and the lenient parser leaves an unparseable section unchanged + warns;
  * deleting profile.json degrades gracefully (no profile, no crash) everywhere —
    the read interface, the chat slice helper, and the GET/PUT endpoints.
All pointed at a temp vault.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import app

# A §7.2-conformant profile fixture used across these tests. It deliberately
# includes a "gym" goal so the on-topic slice test ("should I skip the gym?")
# has something to surface. (Self-contained here so the suite doesn't depend on
# any seed script.)
DEMO_PROFILE: dict = {
    "schema_version": 1,
    "identity": {
        "stated_self": "a good, masculine Muslim man",
        "principles": ["honesty", "discipline", "loyalty"],
        "provenance": ["seed-entry-0001", "seed-entry-0006"],
    },
    "goals": [
        {
            "id": "g-7a1f9c20-3b54-4e8d-9a11-1f0c2d3e4a5b",
            "text": "Pray fajr consistently",
            "status": "active", "confidence": 0.82, "last_seen": "2026-06-15",
            "evidence": ["seed-entry-0006", "seed-entry-0010"], "source": "model",
        },
        {
            "id": "g-2c4e6a80-9d12-4f33-8b77-5e6f7a8b9c0d",
            "text": "Train at the gym four times a week",
            "status": "active", "confidence": 0.71, "last_seen": "2026-06-13",
            "evidence": ["seed-entry-0008", "seed-entry-0013"], "source": "model",
        },
        {
            "id": "g-9b3d5f70-1a23-4c44-9e88-6f7a8b9c0d1e",
            "text": "Be steadier under work pressure instead of spiralling",
            "status": "active", "confidence": 0.64, "last_seen": "2026-06-12",
            "evidence": ["seed-entry-0004", "seed-entry-0017"], "source": "model",
        },
    ],
    "patterns": [
        {
            "id": "p-4f6a8c20-5b34-4d55-8a99-7b8c9d0e1f2a",
            "text": "Avoids difficult conversations when tired",
            "type": "behavior", "confidence": 0.74, "last_seen": "2026-06-09",
            "evidence": ["seed-entry-0002", "seed-entry-0009"], "source": "model",
        },
        {
            "id": "p-8a0c2e40-7d56-4e66-9b00-8c9d0e1f2a3b",
            "text": "Skips workouts and routines first when work stress spikes",
            "type": "behavior", "confidence": 0.68, "last_seen": "2026-06-12",
            "evidence": ["seed-entry-0004", "seed-entry-0017"], "source": "model",
        },
    ],
    "relationships": [
        {
            "name": "Daniel", "type": "friend",
            "summary": "Close, but tension flares around communication when stressed",
            "evidence": ["seed-entry-0002", "seed-entry-0014"], "last_seen": "2026-06-10",
        },
        {
            "name": "his mother", "type": "family",
            "summary": "Loving but recurring friction about visiting",
            "evidence": ["seed-entry-0009"], "last_seen": "2026-06-07",
        },
    ],
    "emotional_baseline": {
        "typical_mood": 1,
        "known_triggers": ["fatigue", "conflict", "work deadlines"],
        "what_helps": ["prayer", "exercise", "an early night"],
        "evidence": ["seed-entry-0003", "seed-entry-0008"],
    },
    "open_loops": [
        {
            "id": "o-1d3f5a70-9b78-4f77-8c11-9d0e1f2a3b4c",
            "description": "Wants to rebuild a steady morning routine",
            "status": "updated", "opened": "2026-06-04", "last_updated": "2026-06-13",
            "evidence": ["seed-entry-0004", "seed-entry-0013"],
        }
    ],
    "watch_list": [
        {
            "pattern_id": "p-8a0c2e40-7d56-4e66-9b00-8c9d0e1f2a3b",
            "conflicting_goal_id": "g-2c4e6a80-9d12-4f33-8b77-5e6f7a8b9c0d",
            "description": "Skipping the gym when tired contradicts the fitness goal he set",
            "evidence": ["seed-entry-0004", "seed-entry-0017"],
        }
    ],
    "anchors": [],
}


@pytest.fixture()
def prof(tmp_path, monkeypatch):
    """Fresh profile module pointed at a temp vault (no profile yet)."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    from memory import profile

    return profile


@pytest.fixture()
def seeded(prof):
    """A temp vault pre-seeded with the demo profile."""
    prof.save_profile(prof.Profile.from_dict(DEMO_PROFILE))
    return prof


# ── §7.2 schema conformance ──────────────────────────────────────────────────


def test_profile_json_conforms_to_7_2(seeded):
    raw = json.loads((seeded._profile_json_path()).read_text(encoding="utf-8"))
    # Top-level keys, in §7.2 order.
    assert list(raw.keys()) == [
        "schema_version", "identity", "goals", "patterns", "relationships",
        "emotional_baseline", "open_loops", "watch_list", "anchors",
    ]
    assert raw["schema_version"] == 1
    assert set(raw["identity"]) >= {"stated_self", "principles", "provenance"}
    assert isinstance(raw["identity"]["principles"], list)
    assert set(raw["goals"][0]) >= {
        "id", "text", "status", "confidence", "last_seen", "evidence", "source",
    }
    assert set(raw["patterns"][0]) >= {
        "id", "text", "type", "confidence", "last_seen", "evidence", "source",
    }
    assert set(raw["relationships"][0]) >= {
        "name", "type", "summary", "evidence", "last_seen",
    }
    assert set(raw["emotional_baseline"]) >= {
        "typical_mood", "known_triggers", "what_helps", "evidence",
    }
    assert set(raw["open_loops"][0]) >= {
        "id", "description", "status", "opened", "last_updated", "evidence",
    }
    assert set(raw["watch_list"][0]) >= {
        "pattern_id", "conflicting_goal_id", "description", "evidence",
    }
    assert raw["anchors"] == []
    # Types that the real engine relies on.
    assert isinstance(raw["goals"][0]["confidence"], float)
    assert isinstance(raw["emotional_baseline"]["typical_mood"], int)


def test_to_dict_round_trips_through_from_dict(seeded):
    p = seeded.get_profile()
    assert seeded.Profile.from_dict(p.to_dict()).to_dict() == p.to_dict()


# ── get_slices (the chat payoff) ──────────────────────────────────────────────


def test_gym_question_surfaces_the_fitness_goal_unprompted(seeded):
    slices = seeded.get_slices("should I skip the gym today?")
    blob = " ".join(slices).lower()
    # The stated fitness goal is present (core is always included)…
    assert "gym" in blob
    assert any("goal of theirs" in s.lower() and "gym" in s.lower() for s in slices)
    # …and the relevant watch-list tension is surfaced for this topic.
    assert any("tension" in s.lower() for s in slices)


def test_core_identity_and_values_always_present(seeded):
    slices = seeded.get_slices("just thinking out loud about nothing in particular")
    blob = " ".join(slices).lower()
    assert "muslim man" in blob  # stated_self
    assert "discipline" in blob  # a principle
    assert "goal of theirs" in blob  # active goals are core


def test_off_topic_message_does_not_surface_a_specific_relationship(seeded):
    # "Daniel" should only appear when the message touches that relationship.
    slices = seeded.get_slices("what should I cook for dinner tonight?")
    assert not any("daniel" in s.lower() for s in slices)
    # A message about Daniel does surface him.
    slices2 = seeded.get_slices("I had an argument with Daniel about communication")
    assert any("daniel" in s.lower() for s in slices2)


def test_format_slices_drops_empty(seeded):
    assert seeded.format_slices([]) == ""
    out = seeded.format_slices(["A.", "B."])
    assert out == "- A.\n- B."


# ── profile.md ↔ profile.json sync (§7.2) ─────────────────────────────────────


def test_editing_a_goal_round_trips_and_anchors(seeded):
    md = seeded.read_markdown()
    assert "Train at the gym four times a week" in md
    edited = md.replace(
        "Train at the gym four times a week",
        "Train at the gym five times a week",
    )
    new_md, warnings = seeded.save_markdown(edited)
    assert warnings == []
    assert "five times a week" in new_md

    p = seeded.get_profile()
    gym = next(g for g in p.goals if "gym" in g["text"])
    assert gym["text"] == "Train at the gym five times a week"
    # The edit is recorded as a user correction the model may not overwrite (§7.2).
    assert gym["source"] == "user"
    assert gym["id"] in p.anchors


def test_lenient_parser_leaves_changed_length_section_unchanged(seeded):
    md = seeded.read_markdown()
    lines = md.splitlines()
    gi = lines.index("## Your goals")
    # Remove the first goal bullet → the list length no longer matches.
    for j in range(gi + 1, len(lines)):
        if lines[j].startswith("- "):
            del lines[j]
            break
    new_md, warnings = seeded.save_markdown("\n".join(lines))
    assert warnings, "a changed-length list must produce a warning"
    # The goals are left exactly as they were — nothing silently dropped.
    assert len(seeded.get_profile().goals) == len(DEMO_PROFILE["goals"])


def test_identity_edit_persists(seeded):
    md = seeded.read_markdown()
    edited = md.replace("honesty, discipline, loyalty", "honesty, patience")
    seeded.save_markdown(edited)
    assert seeded.get_profile().identity["principles"] == ["honesty", "patience"]


# ── graceful degradation ──────────────────────────────────────────────────────


def test_no_profile_degrades_gracefully(prof):
    assert prof.get_profile() is None
    assert prof.get_slices("anything at all") == []
    assert prof.slices_for_prompt("anything at all") == ""
    assert prof.read_markdown() is None
    with pytest.raises(prof.NoProfileError):
        prof.save_markdown("# anything")


def test_deleting_profile_json_degrades(seeded):
    seeded._profile_json_path().unlink()
    assert seeded.get_profile() is None
    assert seeded.slices_for_prompt("should I skip the gym?") == ""


def test_corrupt_profile_json_is_treated_as_absent(prof):
    path = prof._profile_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    assert prof.get_profile() is None  # no crash


# ── the GET/PUT /profile endpoints ────────────────────────────────────────────


def test_get_profile_absent_then_present(prof):
    client = TestClient(app)
    r = client.get("/profile")
    assert r.status_code == 200
    assert r.json() == {"present": False, "markdown": None}

    prof.save_profile(prof.Profile.from_dict(DEMO_PROFILE))
    r = client.get("/profile")
    body = r.json()
    assert body["present"] is True
    assert "## Your goals" in body["markdown"]


def test_put_profile_applies_edit(seeded):
    client = TestClient(app)
    md = seeded.read_markdown().replace("honesty, discipline, loyalty", "honesty")
    r = client.put("/profile", json={"markdown": md})
    assert r.status_code == 200
    body = r.json()
    assert body["present"] is True
    assert body["warnings"] == []
    assert seeded.get_profile().identity["principles"] == ["honesty"]


def test_put_profile_404_when_absent(prof):
    client = TestClient(app)
    r = client.put("/profile", json={"markdown": "# nothing here"})
    assert r.status_code == 404
