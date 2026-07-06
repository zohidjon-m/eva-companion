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
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import app

# A §7.2-conformant profile fixture used across these tests. It deliberately
# includes a "gym" goal so the on-topic slice test ("should I skip the gym?")
# has something to surface. (Self-contained here so the suite doesn't depend on
# any seed script.)
DEMO_PROFILE: dict = {
    "schema_version": 2,
    "identity": {
        "stated_self": "a good, masculine Muslim man",
        "principles": ["honesty", "discipline", "loyalty"],
        "provenance": {
            "stated_self": {"evidence": ["seed-entry-0001"], "source": "model", "last_seen": "2026-06-15"},
            "principles": {"evidence": ["seed-entry-0006"], "source": "model", "last_seen": "2026-06-15"},
        },
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
        "provenance": {
            "typical_mood": {"source": "code", "evidence": ["seed-entry-0003", "seed-entry-0008"]},
            "known_triggers": {"source": "model", "evidence": ["seed-entry-0003"], "last_seen": "2026-06-08"},
            "what_helps": {"source": "model", "evidence": ["seed-entry-0008"], "last_seen": "2026-06-08"},
        },
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
    _seed_evidence_entries(DEMO_PROFILE)
    prof.save_profile(prof.Profile.from_dict(DEMO_PROFILE))
    return prof


def _collect_evidence_ids(value) -> set[str]:
    """Collect every evidence uid from a nested profile fixture."""
    found: set[str] = set()
    if isinstance(value, dict):
        evidence = value.get("evidence")
        if isinstance(evidence, list):
            found.update(str(e) for e in evidence)
        for child in value.values():
            found.update(_collect_evidence_ids(child))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_evidence_ids(item))
    return found


def _seed_evidence_entries(raw_profile: dict) -> None:
    """Insert real non-seeded entries for the profile fixture evidence ids."""
    from memory import db

    start = datetime(2026, 6, 1, 8, 0, 0)
    conn = db.get_or_create_db()
    try:
        for i, entry_id in enumerate(sorted(_collect_evidence_ids(raw_profile))):
            when = start + timedelta(days=i)
            text = (
                f"Evidence {entry_id}: gym discipline prayer exercise Daniel "
                "work stress communication fatigue conflict routine."
            )
            db.insert_entry(
                conn,
                id=entry_id,
                date=when.date().isoformat(),
                type="journal",
                text=text,
                word_count=len(text.split()),
                created_at=when.isoformat(),
            )
    finally:
        conn.close()


# ── §7.2 schema conformance ──────────────────────────────────────────────────


def test_profile_json_conforms_to_7_2(seeded):
    raw = json.loads((seeded._profile_json_path()).read_text(encoding="utf-8"))
    # Top-level keys, in §7.2 order.
    assert list(raw.keys()) == [
        "schema_version", "identity", "goals", "patterns", "relationships",
        "emotional_baseline", "open_loops", "watch_list", "anchors",
    ]
    assert raw["schema_version"] == 2
    assert set(raw["identity"]) >= {"stated_self", "principles", "provenance"}
    assert isinstance(raw["identity"]["principles"], list)
    # v2 provenance is a field-keyed dict, each field carrying its own evidence.
    assert isinstance(raw["identity"]["provenance"], dict)
    assert set(raw["identity"]["provenance"]) >= {"stated_self", "principles"}
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
        "typical_mood", "known_triggers", "what_helps", "provenance",
    }
    assert raw["emotional_baseline"]["provenance"]["typical_mood"]["source"] == "code"
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


def test_unknown_topic_does_not_surface_profile_facts(seeded):
    slices = seeded.get_slices("just thinking out loud about nothing in particular")
    assert slices == []


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


def test_retrieve_slices_returns_typed_evidence_backed_claims(seeded):
    slices = seeded.retrieve_slices("should I skip the gym today?", advice_mode=True)
    assert slices
    gym = next(s for s in slices if s.kind == "goal" and "gym" in s.text.lower())
    assert gym.evidence_ids
    assert gym.source == "model"
    assert gym.confidence == pytest.approx(0.71)


def test_profile_evidence_resolution_reuses_one_db_connection(seeded, monkeypatch):
    calls = 0
    real_get_or_create_db = seeded.db.get_or_create_db

    def counted_get_or_create_db(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_get_or_create_db(*args, **kwargs)

    monkeypatch.setattr(seeded.db, "get_or_create_db", counted_get_or_create_db)

    slices = seeded.retrieve_slices("gym discipline Daniel routine", advice_mode=True)
    assert slices
    assert calls == 1

    calls = 0
    sections = seeded.profile_sections()
    assert sections
    assert calls == 1


def test_model_claim_with_missing_evidence_is_not_prompt_injected(prof):
    broken = dict(DEMO_PROFILE)
    broken["goals"] = [
        {
            "id": "g-missing",
            "text": "Train at the gym",
            "status": "active",
            "confidence": 0.9,
            "evidence": ["missing-entry"],
            "source": "model",
        }
    ]
    broken["identity"] = {"provenance": {}}
    broken["patterns"] = []
    broken["relationships"] = []
    broken["emotional_baseline"] = {"provenance": {}}
    broken["open_loops"] = []
    broken["watch_list"] = []
    prof.save_profile(prof.Profile.from_dict(broken))

    assert prof.retrieve_slices("gym") == []
    sections = prof.profile_sections()
    claim = sections[0]["claims"][0]
    assert claim["evidence_status"] == "missing"
    assert claim["evidence"][0]["available"] is False


# ── semantic relevance gate (augments the lexical match) ──────────────────────
# These stub vector.semantic_scores so the gate logic is exercised deterministically
# and offline (no embedding model needed). The message "…bailing on my workout"
# shares NO content word with the "Train at the gym…" goal, so any surfacing is
# purely the semantic half at work.


def test_semantic_paraphrase_surfaces_goal(seeded, monkeypatch):
    """A goal with no lexical overlap still surfaces when it's semantically close."""
    def fake_scores(query, texts):
        return [0.9 if "gym" in t.lower() else 0.0 for t in texts]

    monkeypatch.setattr(seeded.vector, "semantic_scores", fake_scores)

    slices = seeded.retrieve_slices("thinking of bailing on my workout")
    assert any(s.kind == "goal" and "gym" in s.text.lower() for s in slices)


def test_semantic_gate_respects_threshold(seeded, monkeypatch):
    """A claim scoring just below the threshold is not injected (precision guard)."""
    below = seeded.SEMANTIC_SLICE_THRESHOLD - 0.05
    monkeypatch.setattr(seeded.vector, "semantic_scores", lambda q, texts: [below] * len(texts))

    # No lexical overlap either, so below-threshold semantic → nothing surfaces.
    assert seeded.retrieve_slices("thinking of bailing on my workout") == []


def test_semantic_flag_off_is_pure_lexical(seeded, monkeypatch):
    """With the flag off, semantic scores are never consulted — lexical only."""
    monkeypatch.setattr(seeded, "SEMANTIC_SLICE_MATCHING", False)
    # Would score everything maximally relevant — but the flag must short-circuit it.
    monkeypatch.setattr(seeded.vector, "semantic_scores", lambda q, texts: [0.99] * len(texts))

    assert seeded.retrieve_slices("thinking of bailing on my workout") == []


def test_candidate_texts_match_loop_strings(seeded, monkeypatch):
    """_candidate_texts must list exactly the strings the loop relevance-checks.

    Guards against the two field-walks drifting apart (the semantic map is keyed by
    these strings, so a mismatch would silently disable semantic matching for a claim).
    """
    monkeypatch.setattr(seeded.vector, "semantic_scores", lambda q, texts: [0.0] * len(texts))
    prof = seeded.get_profile()
    expected = set(seeded._candidate_texts(prof))

    seen: set[str] = set()
    real_is_relevant = seeded._is_relevant

    def spy(claim_text, tokens):
        seen.add(claim_text)
        return real_is_relevant(claim_text, tokens)

    monkeypatch.setattr(seeded, "_is_relevant", spy)
    # A topic with a real token (so no early return) that lexically matches nothing,
    # ensuring every candidate is still visited by the relevance gate.
    seeded.retrieve_slices("xyzzy")
    assert seen == expected


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


def test_identity_edit_registers_field_anchor(seeded):
    md = seeded.read_markdown()
    edited = md.replace("honesty, discipline, loyalty", "honesty, patience")
    seeded.save_markdown(edited)
    p = seeded.get_profile()
    # A corrected identity field is anchored by its synthetic path and marked
    # source=user, so the model's update engine may not overwrite it (R7.5).
    assert "identity.principles" in p.anchors
    assert p.identity["provenance"]["principles"]["source"] == "user"
    assert seeded.is_field_anchored(p, "identity.principles")


def test_unedited_identity_field_is_not_anchored(seeded):
    # Saving the profile unchanged must not spuriously anchor identity/baseline.
    md = seeded.read_markdown()
    seeded.save_markdown(md)
    p = seeded.get_profile()
    assert "identity.stated_self" not in p.anchors
    assert "baseline.known_triggers" not in p.anchors


def test_baseline_edit_registers_field_anchor(seeded):
    md = seeded.read_markdown()
    assert "fatigue, conflict, work deadlines" in md
    edited = md.replace("fatigue, conflict, work deadlines", "fatigue, poor sleep")
    seeded.save_markdown(edited)
    p = seeded.get_profile()
    assert p.emotional_baseline["known_triggers"] == ["fatigue", "poor sleep"]
    assert "baseline.known_triggers" in p.anchors
    assert p.emotional_baseline["provenance"]["known_triggers"]["source"] == "user"


def test_out_of_range_typical_mood_is_rejected_with_warning(seeded):
    md = seeded.read_markdown()
    assert "Typical mood: +1" in md
    edited = md.replace("Typical mood: +1", "Typical mood: +99")
    _new_md, warnings = seeded.save_markdown(edited)
    assert any("between" in w.lower() for w in warnings)
    p = seeded.get_profile()
    # The invalid value is not stored, and the field is not anchored to a typo.
    assert p.emotional_baseline["typical_mood"] == 1
    assert "baseline.typical_mood" not in p.anchors


def test_in_range_typical_mood_edit_is_stored_and_anchored(seeded):
    md = seeded.read_markdown()
    edited = md.replace("Typical mood: +1", "Typical mood: -2")
    seeded.save_markdown(edited)
    p = seeded.get_profile()
    assert p.emotional_baseline["typical_mood"] == -2
    assert "baseline.typical_mood" in p.anchors
    assert p.emotional_baseline["provenance"]["typical_mood"]["source"] == "user"


def test_v1_profile_migrates_to_v2_on_read(prof):
    v1 = {
        "schema_version": 1,
        "identity": {"stated_self": "x", "principles": ["a"], "provenance": ["seed-1"]},
        "goals": [], "patterns": [], "relationships": [],
        "emotional_baseline": {
            "typical_mood": 1, "known_triggers": ["t"], "what_helps": ["h"],
            "evidence": ["seed-2"],
        },
        "open_loops": [], "watch_list": [], "anchors": [],
    }
    path = prof._profile_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(v1), encoding="utf-8")

    p = prof.get_profile()
    assert p.schema_version == 2
    # Old flat provenance/evidence lists become field-keyed dicts (empty until a
    # rebuild re-derives them); the stale top-level baseline evidence is dropped.
    assert isinstance(p.identity["provenance"], dict)
    assert isinstance(p.emotional_baseline["provenance"], dict)
    assert "evidence" not in p.emotional_baseline
    # The migrated version is what persists on the next save.
    prof.save_profile(p)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2


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
    assert r.json() == {"present": False, "markdown": None, "sections": []}

    prof.save_profile(prof.Profile.from_dict(DEMO_PROFILE))
    r = client.get("/profile")
    body = r.json()
    assert body["present"] is True
    assert "## Your goals" in body["markdown"]
    assert any(s["id"] == "goals" for s in body["sections"])


def test_profile_evidence_endpoint_returns_full_local_entry(seeded):
    client = TestClient(app)
    r = client.get("/profile/evidence/seed-entry-0008")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "seed-entry-0008"
    assert body["type"] == "journal"
    assert "Evidence seed-entry-0008" in body["text"]


def test_put_profile_applies_edit(seeded):
    client = TestClient(app)
    md = seeded.read_markdown().replace("honesty, discipline, loyalty", "honesty")
    r = client.put("/profile", json={"markdown": md})
    assert r.status_code == 200
    body = r.json()
    assert body["present"] is True
    assert body["warnings"] == []
    assert body["sections"]
    assert seeded.get_profile().identity["principles"] == ["honesty"]


def test_put_profile_404_when_absent(prof):
    client = TestClient(app)
    r = client.put("/profile", json={"markdown": "# nothing here"})
    assert r.status_code == 404
