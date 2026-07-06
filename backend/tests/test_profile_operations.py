"""R7 — the real L3 update engine: the §7.3 operation grammar and its guards.

Covers the phase's acceptance checks:
  * a strengthen op backed by three real entries raises a claim's confidence and
    grows its evidence to three pointers;
  * an operation whose evidence cites no known entry uid (or an unknown claim) is
    silently rejected and never mutates the profile;
  * strengthen/weaken/status changes on a user anchor are refused;
  * nightly decay fades unseen non-anchor claims, flags the long-stale ones, and
    leaves anchors untouched;
  * rebuild_profile replays L1 into profile.json and preserves user anchors
    byte-for-byte;
  * editing an entry flags every L3 claim resting on it as evidence-stale;
  * the engine runs on an empty vault without crashing.
The pure grammar/decay tests need no vault; the rest use a temp vault.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from memory import operations
from memory.profile import Profile


# ── helpers ───────────────────────────────────────────────────────────────────
def make_caller(*responses):
    """A mock ModelCaller returning the given raw strings in order."""
    seq = list(responses)

    async def _call(prompt, *, temperature, max_tokens):
        if not seq:
            raise AssertionError("model called more times than expected")
        return seq.pop(0)

    return _call


def _goal(gid, text, *, conf=0.5, evidence=None, source="model", status="active"):
    return {
        "id": gid, "text": text, "status": status, "confidence": conf,
        "last_seen": "2026-06-01", "evidence": list(evidence or []), "source": source,
    }


# ── evidence gate + apply ─────────────────────────────────────────────────────
def test_strengthen_raises_confidence_and_grows_evidence_to_three():
    base = Profile(goals=[_goal("g1", "Train at the gym", conf=0.5, evidence=["e1"])])
    ops = [{"op": "strengthen", "claim_id": "g1", "evidence": ["e2", "e3"]}]

    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1", "e2", "e3"}, today="2026-07-05"
    )

    goal = updated.goals[0]
    assert goal["confidence"] == pytest.approx(0.6)         # 0.5 + 0.1
    assert goal["evidence"] == ["e1", "e2", "e3"]           # three pointers
    assert goal["last_seen"] == "2026-07-05"
    assert report.strengthened == 1 and report.rejected == 0


def test_add_goal_and_add_pattern_start_at_half_confidence():
    base = Profile()
    ops = [
        {"op": "add_goal", "text": "Learn piano", "evidence": ["e1"]},
        {"op": "add_pattern", "text": "Skips workouts when stressed",
         "type": "behavior", "evidence": ["e1"]},
    ]
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1"}, today="2026-07-05"
    )
    assert report.added == 2
    assert updated.goals[0]["confidence"] == 0.5
    assert updated.goals[0]["status"] == "active"
    assert updated.goals[0]["source"] == "model"
    assert updated.patterns[0]["type"] == "behavior"


def test_weaken_records_counter_evidence_and_reason_without_polluting_support():
    base = Profile(goals=[_goal("g1", "Train at the gym", conf=0.6, evidence=["e1"])])
    ops = [{"op": "weaken", "claim_id": "g1", "reason": "skipped all week", "evidence": ["e2"]}]
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1", "e2"}, today="2026-07-05"
    )
    assert report.weakened == 1
    g = updated.goals[0]
    assert g["confidence"] == pytest.approx(0.45)     # 0.6 − 0.15
    assert g["counter_evidence"] == ["e2"]            # the contradicting entry is kept
    assert g["evidence"] == ["e1"]                    # supporting evidence untouched
    assert g["weaken_reason"] == "skipped all week"


def test_add_goal_dedupes_against_existing_text_as_a_strengthen():
    # A model slip re-adding an existing goal (within a batch, or across the
    # overlapping nightly/weekly passes) must fold into it, not duplicate it.
    base = Profile(goals=[_goal("g1", "Train at the gym", conf=0.5, evidence=["e1"])])
    ops = [{"op": "add_goal", "text": "train at the GYM", "evidence": ["e2"]}]  # same, cased
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1", "e2"}, today="2026-07-05"
    )
    assert len(updated.goals) == 1                       # no duplicate
    assert updated.goals[0]["confidence"] == pytest.approx(0.6)  # strengthened
    assert updated.goals[0]["evidence"] == ["e1", "e2"]
    assert report.added == 0 and report.strengthened == 1


def test_add_pattern_dedupes_and_never_overwrites_an_anchor():
    anchored = _goal("p1", "Skips workouts when stressed", conf=0.9, source="user")
    anchored["type"] = "behavior"
    base = Profile(patterns=[anchored], anchors=["p1"])
    ops = [{"op": "add_pattern", "text": "skips workouts when stressed",
            "type": "behavior", "evidence": ["e1"]}]
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1"}, today="2026-07-05"
    )
    assert len(updated.patterns) == 1
    assert updated.patterns[0]["confidence"] == 0.9      # anchor untouched
    assert report.added == 0 and report.strengthened == 0 and report.rejected == 1


def test_add_pattern_rejects_type_outside_the_enum():
    base = Profile()
    ops = [
        {"op": "add_pattern", "text": "valid one", "type": "Emotional", "evidence": ["e1"]},
        {"op": "add_pattern", "text": "bad type", "type": "vibes", "evidence": ["e1"]},
    ]
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1"}, today="2026-07-05"
    )
    assert len(updated.patterns) == 1
    assert updated.patterns[0]["type"] == "emotional"   # normalised to lowercase
    assert report.added == 1 and report.rejected == 1


def test_operation_without_valid_evidence_is_rejected():
    base = Profile(goals=[_goal("g1", "existing")])
    ops = [
        {"op": "add_goal", "text": "phantom goal", "evidence": ["nope-999"]},  # unknown uid
        {"op": "add_goal", "text": "empty evidence", "evidence": []},          # no evidence
        {"op": "strengthen", "claim_id": "g-unknown", "evidence": ["e1"]},     # unknown claim
    ]
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1"}, today="2026-07-05"
    )
    assert updated.goals == base.goals          # nothing added or changed
    assert report.added == 0 and report.strengthened == 0
    assert report.rejected == 3


def test_validate_operations_is_the_pure_evidence_gate():
    ops = [
        {"op": "add_goal", "text": "ok", "evidence": ["e1"]},
        {"op": "add_goal", "text": "bad", "evidence": ["ghost"]},
        {"op": "frobnicate", "evidence": ["e1"]},
    ]
    valid, reasons = operations.validate_operations(ops, {"e1"})
    assert len(valid) == 1 and valid[0]["text"] == "ok"
    assert len(reasons) == 2


# ── anchor protection ─────────────────────────────────────────────────────────
def test_user_anchor_cannot_be_weakened_or_strengthened():
    anchored = _goal("g1", "Pray fajr", conf=0.9, source="user")
    base = Profile(goals=[anchored], anchors=["g1"])
    ops = [
        {"op": "weaken", "claim_id": "g1", "reason": "model thinks it's fading",
         "evidence": ["e1"]},
        {"op": "strengthen", "claim_id": "g1", "evidence": ["e1"]},
        {"op": "update_goal_status", "goal_id": "g1", "status": "abandoned",
         "evidence": ["e1"]},
    ]
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1"}, today="2026-07-05"
    )
    assert updated.goals[0] == anchored          # untouched
    assert report.weakened == 0 and report.strengthened == 0 and report.status_changed == 0
    assert report.rejected == 3


def test_weaken_below_threshold_flags_for_review():
    base = Profile(goals=[_goal("g1", "shaky", conf=0.3)])
    ops = [{"op": "weaken", "claim_id": "g1", "reason": "contradicted", "evidence": ["e1"]}]
    updated, _ = operations.apply_operations(
        base, ops, known_entry_ids={"e1"}, today="2026-07-05"
    )
    assert updated.goals[0]["confidence"] == pytest.approx(0.15)  # 0.3 - 0.15
    assert updated.goals[0]["needs_review"] is True


def test_weaken_without_evidence_is_rejected():
    base = Profile(goals=[_goal("g1", "real claim", conf=0.6)])
    ops = [{"op": "weaken", "claim_id": "g1", "reason": "just a hunch"}]  # no evidence
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1"}, today="2026-07-05"
    )
    assert updated.goals[0]["confidence"] == 0.6   # untouched
    assert report.weakened == 0 and report.rejected == 1


# ── decay ─────────────────────────────────────────────────────────────────────
def test_decay_fades_unseen_claims_and_flags_the_long_stale():
    goals = [_goal("g1", "recent", conf=0.8)]
    goals[0]["last_seen"] = "2026-07-04"                     # 1 day before "today"
    patterns = [_goal("p1", "old", conf=0.55)]
    patterns[0]["last_seen"] = "2026-05-01"                  # 65 days before "today"

    base = Profile(goals=goals, patterns=patterns)
    decayed = operations.apply_decay(base, today="2026-07-05")

    assert decayed.goals[0]["confidence"] == pytest.approx(0.79)   # 0.8 - 0.01×1
    assert "stale" not in decayed.goals[0]
    # 0.55 - 0.01×65 = -0.1 → floored to 0.0, and stale (below 0.5, ≥ 60 days)
    assert decayed.patterns[0]["confidence"] == 0.0
    assert decayed.patterns[0]["stale"] is True


def test_decay_is_idempotent_within_a_day():
    goals = [_goal("g1", "old", conf=0.8)]
    goals[0]["last_seen"] = "2026-05-01"                     # 65 days stale
    base = Profile(goals=goals)

    once = operations.apply_decay(base, today="2026-07-05")
    twice = operations.apply_decay(once, today="2026-07-05")   # same day, re-run

    assert once.goals[0]["confidence"] == twice.goals[0]["confidence"]
    assert once.goals[0]["decayed_through"] == "2026-07-05"


def test_decay_does_not_compound_across_days():
    goals = [_goal("g1", "old", conf=0.9)]
    goals[0]["last_seen"] = "2026-07-01"
    base = Profile(goals=goals)

    day5 = operations.apply_decay(base, today="2026-07-05")    # 4 days → 0.90 - 0.04
    day6 = operations.apply_decay(day5, today="2026-07-06")    # +1 day → 0.86 - 0.01

    assert day5.goals[0]["confidence"] == pytest.approx(0.86)
    assert day6.goals[0]["confidence"] == pytest.approx(0.85)  # not 0.86 - 0.05


def test_decay_never_touches_anchors():
    goals = [_goal("g1", "anchored", conf=0.6, source="user")]
    goals[0]["last_seen"] = "2026-01-01"                     # very old
    base = Profile(goals=goals, anchors=["g1"])
    decayed = operations.apply_decay(base, today="2026-07-05")
    assert decayed.goals[0]["confidence"] == 0.6             # unchanged
    assert "stale" not in decayed.goals[0]


# ── loops & contradictions ────────────────────────────────────────────────────
def test_mark_resolved_and_note_contradiction():
    base = Profile(
        goals=[_goal("g1", "gym")],
        patterns=[_goal("p1", "skips gym")],
        open_loops=[{"id": "o1", "description": "unresolved", "status": "open",
                     "evidence": []}],
    )
    ops = [
        {"op": "mark_resolved", "loop_id": "o1", "evidence": ["e1"]},
        {"op": "note_contradiction", "claim_id_a": "p1", "claim_id_b": "g1",
         "description": "skipping gym contradicts fitness goal", "evidence": ["e1"]},
    ]
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1"}, today="2026-07-05"
    )
    assert updated.open_loops[0]["status"] == "resolved"
    assert report.resolved == 1
    assert len(updated.watch_list) == 1
    assert updated.watch_list[0]["pattern_id"] == "p1"


def test_note_contradiction_rejects_unknown_claim_ids():
    base = Profile(goals=[_goal("g1", "gym")], patterns=[_goal("p1", "skips gym")])
    ops = [
        # claim_id_a is not a real pattern → must not reach the watch-list
        {"op": "note_contradiction", "claim_id_a": "p-ghost", "claim_id_b": "g1",
         "description": "fabricated", "evidence": ["e1"]},
        # claim_id_b is not a real goal
        {"op": "note_contradiction", "claim_id_a": "p1", "claim_id_b": "g-ghost",
         "description": "fabricated", "evidence": ["e1"]},
    ]
    updated, report = operations.apply_operations(
        base, ops, known_entry_ids={"e1"}, today="2026-07-05"
    )
    assert updated.watch_list == []
    assert report.contradictions == 0 and report.rejected == 2


# ── JSON array extraction (mirrors extract_json_object) ───────────────────────
def test_extract_json_array_tolerates_prose_and_fences():
    raw = "Sure! Here are the ops:\n```json\n[{\"op\": \"add_goal\"}]\n```\nHope that helps."
    assert operations.extract_json_array(raw) == [{"op": "add_goal"}]


def test_extract_json_array_raises_on_no_array():
    with pytest.raises(ValueError):
        operations.extract_json_array("no array here")


# ── vault-backed: orchestration, rebuild, self-heal, degradation ──────────────
@pytest.fixture()
def vault_env(tmp_path, monkeypatch):
    """Point the whole memory stack at a fresh temp vault (no reload needed)."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    return tmp_path


def _seed_l1_entry(db, entry_id, *, date, summary, themes, mood=1, created_at=None):
    """Insert one real (non-seeded) done extraction so real_extractions sees it.

    Writes ``extractions.mood`` (the canonical mood column, source for both the L3
    typical_mood and the denormalised ``mood_series`` chart cache). It intentionally
    does not populate ``mood_series`` — typical_mood is derived from
    ``extractions.mood`` via :func:`db.mood_history`, not the chart cache.
    """
    conn = db.get_or_create_db()
    try:
        db.insert_entry(
            conn, id=entry_id, date=date, type="journal",
            text=summary, word_count=len(summary.split()),
            created_at=created_at or f"{date}T08:00:00",
        )
        db.create_pending_extraction(conn, entry_id, source_hash=f"h-{entry_id}")
        db.finalize_extraction(
            conn, entry_id, mood=mood, emotions=[], entities=[], themes=themes,
            events=[], stated_goals=[], behaviors=[], decisions=[], open_loops=[],
            self_judgments=[], summary=summary, extracted_at=f"{date}T08:00:05",
            source_hash=f"h-{entry_id}",
        )
    finally:
        conn.close()


def test_update_profile_from_entries_grows_a_young_profile(vault_env):
    from memory import operations, profile

    entries = [{"entry_id": "e1", "date": "2026-07-05",
                "summary": "They started learning piano.", "themes": ["piano"]}]
    caller = make_caller('[{"op":"add_goal","text":"Learn piano","evidence":["e1"]}]')

    saved, report = asyncio.run(
        operations.update_profile_from_entries(entries, call_model=caller, today="2026-07-05")
    )

    assert report.added == 1
    on_disk = profile.get_profile()
    assert on_disk is not None
    assert any("piano" in g["text"].lower() for g in on_disk.goals)


def test_rebuild_replays_l1_and_preserves_user_anchor(vault_env):
    from memory import db, profile, rebuild_profile

    anchored = _goal("g-anchor", "Pray fajr consistently", conf=0.9, source="user")
    stale_model = _goal("g-old", "an old model guess", conf=0.6)
    profile.save_profile(Profile(goals=[anchored, stale_model], anchors=["g-anchor"]))
    anchored_before = json.dumps(anchored, sort_keys=True)

    for i in (1, 2, 3):
        _seed_l1_entry(db, f"e{i}", date=f"2026-07-0{i}",
                       summary=f"They went to the gym, day {i}.", themes=["gym"])

    caller = make_caller('[{"op":"add_goal","text":"Train at the gym","evidence":["e1","e2","e3"]}]')
    report = asyncio.run(rebuild_profile.rebuild_profile(call_model=caller, today="2026-07-05"))

    assert report.entries == 3
    assert report.anchors_preserved == 1
    rebuilt = profile.get_profile()
    ids = {g["id"] for g in rebuilt.goals}
    assert "g-anchor" in ids            # anchor survived
    assert "g-old" not in ids           # non-anchor model claim was cleared
    gym = next(g for g in rebuilt.goals if "gym" in g["text"].lower())
    assert gym["evidence"] == ["e1", "e2", "e3"]
    # the anchor is byte-for-byte identical
    kept = next(g for g in rebuilt.goals if g["id"] == "g-anchor")
    assert json.dumps(kept, sort_keys=True) == anchored_before


def test_editing_an_entry_flags_dependent_claims_stale(vault_env):
    from memory import capture, profile, vault

    rec = vault.save_entry("First version about the gym.", "journal")
    profile.save_profile(Profile(goals=[
        _goal("g1", "gym goal", evidence=[rec.id]),
        _goal("g2", "unrelated goal", evidence=["other-entry"]),
    ]))

    vault.update_entry(rec.id, "Edited: actually about cooking now.")
    caller = make_caller(
        '{"mood": 0, "emotions": [], "entities": [], "themes": ["cooking"], '
        '"events": [], "stated_goals": [], "behaviors": [], "decisions": [], '
        '"open_loops": [], "self_judgments": [], '
        '"summary": "They wrote about cooking dinner and enjoying the evening."}'
    )
    asyncio.run(capture.recompute_entry(rec.id, call_model=caller))

    reread = profile.get_profile()
    g1 = next(g for g in reread.goals if g["id"] == "g1")
    g2 = next(g for g in reread.goals if g["id"] == "g2")
    assert g1.get("needs_revalidation") is True     # its evidence entry changed
    assert "needs_revalidation" not in g2           # untouched


def test_editing_a_counter_evidence_entry_flags_the_weakened_claim(vault_env):
    from memory import capture, profile, vault

    rec = vault.save_entry("A day I skipped the gym.", "journal")
    # A claim whose confidence was eroded by a weaken citing rec.id as counter-evidence
    # (its support lives elsewhere). Editing that entry must still trigger a re-audit.
    profile.save_profile(Profile(goals=[_goal("g1", "gym goal", evidence=["support-entry"])]))
    p = profile.get_profile()
    p.goals[0]["counter_evidence"] = [rec.id]
    profile.save_profile(p)

    vault.update_entry(rec.id, "Edited: actually I did go.")
    caller = make_caller(
        '{"mood": 2, "emotions": [], "entities": [], "themes": ["gym"], '
        '"events": [], "stated_goals": [], "behaviors": [], "decisions": [], '
        '"open_loops": [], "self_judgments": [], '
        '"summary": "They went to the gym after all."}'
    )
    asyncio.run(capture.recompute_entry(rec.id, call_model=caller))

    assert profile.get_profile().goals[0].get("needs_revalidation") is True


def test_failed_recompute_still_flags_dependent_claims(vault_env):
    from memory import capture, profile, vault

    rec = vault.save_entry("First version about the gym.", "journal")
    profile.save_profile(Profile(goals=[_goal("g1", "gym goal", evidence=[rec.id])]))

    vault.update_entry(rec.id, "Edited body, extraction will fail.")
    # Model returns unparseable text twice → extraction degrades to null_stored,
    # but the entry's body still changed, so the dependent claim must be flagged.
    caller = make_caller("not json at all", "still not json")
    status = asyncio.run(capture.recompute_entry(rec.id, call_model=caller))

    assert status == "null_stored"
    g1 = profile.get_profile().goals[0]
    assert g1.get("needs_revalidation") is True


def test_rebuild_batches_bounded_model_calls(vault_env, monkeypatch):
    from memory import db, operations, profile, rebuild_profile

    monkeypatch.setattr(operations, "BATCH_SIZE", 2)   # force multiple batches
    for i in (1, 2, 3):
        _seed_l1_entry(db, f"e{i}", date=f"2026-07-0{i}",
                       summary=f"Gym day {i}.", themes=["gym"])

    calls = []

    async def _counting_caller(prompt, *, temperature, max_tokens):
        calls.append(prompt)
        return '[{"op":"add_goal","text":"Train at the gym","evidence":["e1"]}]'

    report = asyncio.run(
        rebuild_profile.rebuild_profile(call_model=_counting_caller, today="2026-07-05")
    )

    # 3 entries with BATCH_SIZE=2 → two bounded batches → two model calls.
    assert len(calls) == 2
    assert report.entries == 3
    assert any("gym" in g["text"].lower() for g in profile.get_profile().goals)


def test_same_day_entries_replay_in_stable_order(vault_env):
    from memory import db

    # Insert three same-day entries out of created_at order.
    _seed_l1_entry(db, "b", date="2026-07-01", summary="second", themes=[],
                   created_at="2026-07-01T10:00:00")
    _seed_l1_entry(db, "a", date="2026-07-01", summary="first", themes=[],
                   created_at="2026-07-01T08:00:00")
    _seed_l1_entry(db, "c", date="2026-07-01", summary="third", themes=[],
                   created_at="2026-07-01T14:00:00")

    conn = db.get_or_create_db()
    try:
        order = [row["entry_id"] for row in db.real_extractions(conn)]
    finally:
        conn.close()
    assert order == ["a", "b", "c"]     # by created_at, not insertion order


def test_engine_runs_on_empty_vault_without_crashing(vault_env):
    from memory import operations, profile

    caller = make_caller("[]")     # model proposes nothing
    saved, report = asyncio.run(
        operations.update_profile_from_entries(
            [{"entry_id": "e1", "date": "2026-07-05", "summary": "quiet day", "themes": []}],
            call_model=caller, today="2026-07-05",
        )
    )
    assert report.added == 0
    assert profile.get_profile() is not None    # an (empty) profile was written


# ── R7.5: evidence-backed identity & emotional baseline ───────────────────────
def test_set_identity_and_add_principle_are_evidence_backed():
    ops = [
        {"op": "set_identity", "text": "A disciplined Muslim man", "evidence": ["e1"]},
        {"op": "add_principle", "text": "honesty", "evidence": ["e2"]},
        {"op": "add_principle", "text": "discipline", "evidence": ["e2"]},
    ]
    updated, report = operations.apply_operations(
        Profile(), ops, known_entry_ids={"e1", "e2"}, today="2026-07-05"
    )
    assert report.identity_set == 1
    assert report.principles_added == 2
    assert updated.identity["stated_self"] == "A disciplined Muslim man"
    assert updated.identity["principles"] == ["honesty", "discipline"]
    prov = updated.identity["provenance"]
    assert prov["stated_self"] == {
        "evidence": ["e1"], "source": "model", "last_seen": "2026-07-05"
    }
    assert prov["principles"]["evidence"] == ["e2"]
    assert prov["principles"]["source"] == "model"


def test_add_principle_dedupes_case_insensitively():
    base = Profile(identity={"stated_self": "", "principles": ["Honesty"]})
    ops = [{"op": "add_principle", "text": "honesty", "evidence": ["e1"]}]
    updated, report = operations.apply_operations(base, ops, known_entry_ids={"e1"})
    assert report.principles_added == 0
    assert report.rejected == 1
    assert updated.identity["principles"] == ["Honesty"]


def test_add_baseline_item_appends_and_rejects_invalid_field():
    ops = [
        {"op": "add_baseline_item", "field": "known_triggers", "text": "poor sleep", "evidence": ["e1"]},
        {"op": "add_baseline_item", "field": "what_helps", "text": "running", "evidence": ["e2"]},
        # typical_mood is code-owned — no verb may write it.
        {"op": "add_baseline_item", "field": "typical_mood", "text": "3", "evidence": ["e1"]},
    ]
    updated, report = operations.apply_operations(
        Profile(), ops, known_entry_ids={"e1", "e2"}, today="2026-07-05"
    )
    assert report.baseline_items_added == 2
    assert updated.emotional_baseline["known_triggers"] == ["poor sleep"]
    assert updated.emotional_baseline["what_helps"] == ["running"]
    assert "typical_mood" not in updated.emotional_baseline
    assert any("invalid field" in r for r in report.reasons)


def test_identity_baseline_ops_rejected_without_known_evidence():
    ops = [
        {"op": "set_identity", "text": "x", "evidence": ["ghost"]},
        {"op": "add_principle", "text": "y", "evidence": []},
        {"op": "add_baseline_item", "field": "what_helps", "text": "z", "evidence": ["ghost"]},
    ]
    updated, report = operations.apply_operations(Profile(), ops, known_entry_ids={"e1"})
    assert report.identity_set == 0
    assert report.principles_added == 0
    assert report.baseline_items_added == 0
    assert report.rejected == 3
    assert updated.identity.get("stated_self") in (None, "")
    assert updated.emotional_baseline.get("what_helps") in (None, [])


def test_anchored_identity_baseline_fields_reject_model_overwrite():
    base = Profile(
        identity={
            "stated_self": "as the user put it",
            "principles": ["kindness"],
            "provenance": {"stated_self": {"source": "user"}, "principles": {"source": "user"}},
        },
        emotional_baseline={
            "known_triggers": ["as the user put it"],
            "provenance": {"known_triggers": {"source": "user"}},
        },
        anchors=["identity.stated_self", "identity.principles", "baseline.known_triggers"],
    )
    ops = [
        {"op": "set_identity", "text": "model rewrite", "evidence": ["e1"]},
        {"op": "add_principle", "text": "ruthlessness", "evidence": ["e1"]},
        {"op": "add_baseline_item", "field": "known_triggers", "text": "model trigger", "evidence": ["e1"]},
    ]
    updated, report = operations.apply_operations(base, ops, known_entry_ids={"e1"})
    assert report.rejected == 3
    assert updated.identity["stated_self"] == "as the user put it"
    assert updated.identity["principles"] == ["kindness"]
    assert updated.emotional_baseline["known_triggers"] == ["as the user put it"]


def test_derive_typical_mood_matches_hand_computed_average():
    points = [
        {"entry_id": "e1", "mood": 2},
        {"entry_id": "e2", "mood": -1},
        {"entry_id": "e3", "mood": 3},
        {"entry_id": "e4", "mood": None},     # skipped — no substitution for a gap
    ]
    value, evidence = operations.derive_typical_mood(points)
    assert value == round((2 - 1 + 3) / 3)     # code counts; == 1
    assert evidence == ["e1", "e2", "e3"]       # only entries that carried a mood


def test_derive_typical_mood_none_when_no_moods():
    assert operations.derive_typical_mood([{"entry_id": "e1", "mood": None}]) == (None, [])


def test_rebuild_derives_identity_and_baseline_from_l1_not_seed(vault_env):
    from memory import db, profile, rebuild_profile

    # A demo profile whose identity + baseline are pure seed values, no anchors —
    # the rebuild must replace them with evidence-backed L1 claims.
    profile.save_profile(Profile(
        identity={"stated_self": "seed self", "principles": ["seed value"]},
        emotional_baseline={
            "typical_mood": -5, "known_triggers": ["seed trigger"], "what_helps": ["seed help"],
        },
    ))

    moods = {"e1": 2, "e2": 4, "e3": 3}
    for i in (1, 2, 3):
        _seed_l1_entry(db, f"e{i}", date=f"2026-07-0{i}",
                       summary=f"Reflecting on discipline, day {i}.",
                       themes=["discipline"], mood=moods[f"e{i}"])

    caller = make_caller(
        '[{"op":"set_identity","text":"someone building discipline","evidence":["e1","e2"]},'
        '{"op":"add_principle","text":"discipline","evidence":["e3"]},'
        '{"op":"add_baseline_item","field":"what_helps","text":"routine","evidence":["e2"]}]'
    )
    report = asyncio.run(rebuild_profile.rebuild_profile(call_model=caller, today="2026-07-05"))

    rebuilt = profile.get_profile()
    # Seed values are gone; the model claims carry evidence into real L1 entries.
    assert rebuilt.identity["stated_self"] == "someone building discipline"
    assert rebuilt.identity["provenance"]["stated_self"]["evidence"] == ["e1", "e2"]
    assert "discipline" in rebuilt.identity["principles"]
    assert "seed value" not in rebuilt.identity["principles"]
    assert rebuilt.emotional_baseline["what_helps"] == ["routine"]
    assert "seed help" not in rebuilt.emotional_baseline["what_helps"]
    # typical_mood is the hand-computed mean of the mood series, code-sourced.
    assert rebuilt.emotional_baseline["typical_mood"] == round((2 + 4 + 3) / 3)
    tm_prov = rebuilt.emotional_baseline["provenance"]["typical_mood"]
    assert tm_prov["source"] == "code"
    assert set(tm_prov["evidence"]) == {"e1", "e2", "e3"}
    assert report.baseline_fields >= 1


def test_update_seam_refreshes_typical_mood_from_full_history(vault_env):
    from memory import db, operations, profile

    # Three real entries with moods already in L1; the incremental seam is handed
    # only the newest, but typical_mood must be the all-time mean over ALL of them,
    # not just this batch (regression guard for the "stale until full rebuild" gap).
    moods = {"e1": 1, "e2": 1, "e3": 5}
    for i in (1, 2, 3):
        _seed_l1_entry(db, f"e{i}", date=f"2026-07-0{i}",
                       summary=f"Day {i}.", themes=["day"], mood=moods[f"e{i}"])

    batch = [{"entry_id": "e3", "date": "2026-07-03", "summary": "Day 3.", "themes": ["day"]}]
    saved, _ = asyncio.run(
        operations.update_profile_from_entries(batch, call_model=make_caller("[]"), today="2026-07-05")
    )

    # round((1+1+5)/3) == 2 (full history), not 5 (batch-only) — proves the source.
    assert saved.emotional_baseline["typical_mood"] == 2
    tm = saved.emotional_baseline["provenance"]["typical_mood"]
    assert tm["source"] == "code"
    assert set(tm["evidence"]) == {"e1", "e2", "e3"}
    assert profile.get_profile().emotional_baseline["typical_mood"] == 2   # persisted


def test_update_seam_leaves_anchored_typical_mood_untouched(vault_env):
    from memory import db, operations, profile

    profile.save_profile(Profile(
        emotional_baseline={"typical_mood": -3, "provenance": {"typical_mood": {"source": "user"}}},
        anchors=["baseline.typical_mood"],
    ))
    _seed_l1_entry(db, "e1", date="2026-07-01", summary="Day.", themes=["day"], mood=5)

    saved, _ = asyncio.run(
        operations.update_profile_from_entries(
            [{"entry_id": "e1", "date": "2026-07-01", "summary": "Day.", "themes": ["day"]}],
            call_model=make_caller("[]"), today="2026-07-05",
        )
    )
    assert saved.emotional_baseline["typical_mood"] == -3   # user anchor survives


def test_rebuild_preserves_user_anchored_identity_field(vault_env):
    from memory import db, profile, rebuild_profile

    profile.save_profile(Profile(
        identity={
            "stated_self": "exactly how I see myself",
            "principles": [],
            "provenance": {"stated_self": {"source": "user"}},
        },
        anchors=["identity.stated_self"],
    ))

    _seed_l1_entry(db, "e1", date="2026-07-01", summary="A day.", themes=["day"], mood=1)
    caller = make_caller('[{"op":"set_identity","text":"model rewrite","evidence":["e1"]}]')
    asyncio.run(rebuild_profile.rebuild_profile(call_model=caller, today="2026-07-05"))

    rebuilt = profile.get_profile()
    assert rebuilt.identity["stated_self"] == "exactly how I see myself"
    assert "identity.stated_self" in rebuilt.anchors
