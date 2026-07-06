"""R8 — the consolidation write loop: nightly/weekly cadences, miners, rollups.

Covers the phase's acceptance checks (IMPLEMENTATION_PLAN_V2 Phase 14), all with an
injected fake model so no real llama-server is needed:

  * the weekly miner surfaces a behavior-vs-goal contradiction *with evidence
    counts*, the model narrates only the cited entries, and L3 gains the matching
    ``watch_list`` item;
  * an open loop opened one day is marked resolved by a later entry (a bounded
    yes/no gates the resolution);
  * a claim flagged ``needs_revalidation`` (as an edit leaves it) is re-audited on
    the nightly pass and its flag cleared;
  * digests roll up: a month digest reduces its week digests (map-reduce), reading
    only the level below.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from memory import consolidate, db
from memory import profile as profile_mod
from memory.profile import Profile


# ── fixtures & helpers ────────────────────────────────────────────────────────
@pytest.fixture()
def vault_env(tmp_path, monkeypatch):
    """Point the whole memory stack at a fresh temp vault (no reload needed)."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    return tmp_path


def _seed(
    entry_id,
    *,
    date,
    summary,
    mood=0,
    themes=None,
    stated_goals=None,
    behaviors=None,
    open_loops=None,
):
    """Insert one real done extraction (the same path capture takes) for a window."""
    conn = db.get_or_create_db()
    try:
        db.insert_entry(
            conn, id=entry_id, date=date, type="journal",
            text=summary, word_count=len(summary.split()),
            created_at=f"{date}T08:00:00",
        )
        db.create_pending_extraction(conn, entry_id, source_hash=f"h-{entry_id}")
        db.finalize_extraction(
            conn, entry_id, mood=mood, emotions=[], entities=[],
            themes=themes or [], events=[], stated_goals=stated_goals or [],
            behaviors=behaviors or [], decisions=[], open_loops=open_loops or [],
            self_judgments=[], summary=summary, extracted_at=f"{date}T08:00:05",
            source_hash=f"h-{entry_id}",
        )
    finally:
        conn.close()


def scripted(*rules, default="[]"):
    """A content-aware fake ModelCaller: the first rule whose marker is in the prompt
    wins. Lets one caller serve every distinct call in a cadence (ops generation, a
    yes/no check, digest narration) without depending on call order."""

    async def _call(prompt, *, temperature, max_tokens):
        for marker, response in rules:
            if marker in prompt:
                return response
        return default

    return _call


# ── weekly behavior-vs-goal miner ─────────────────────────────────────────────
def test_weekly_mines_behavior_vs_goal_with_evidence_and_adds_watch_item(vault_env):
    # A week where the goal is stated twice and contradicted four times. Goals are
    # seeded in their REAL L1 object shape ({"text","is_new"}) to exercise flattening.
    _seed("e1", date="2026-07-01", summary="Said I'd exercise regularly.",
          themes=["exercise"], stated_goals=[{"text": "exercise regularly", "is_new": True}])
    _seed("e2", date="2026-07-02", summary="Skipped exercise, stayed in.",
          themes=["exercise"], behaviors=["skipped exercise"])
    _seed("e3", date="2026-07-03", summary="Skipped exercise again.",
          themes=["exercise"], behaviors=["skipped exercise"])
    _seed("e4", date="2026-07-04", summary="Told myself exercise regularly matters.",
          themes=["exercise"], stated_goals=[{"text": "exercise regularly", "is_new": False}])
    _seed("e5", date="2026-07-05", summary="Skipped exercise, too tired.",
          themes=["exercise"], behaviors=["skipped exercise"])
    _seed("e6", date="2026-07-06", summary="Skipped exercise once more.",
          themes=["exercise"], behaviors=["skipped exercise"])

    ops = (
        '[{"op":"add_goal","text":"exercise regularly","evidence":["e1","e4"]},'
        '{"op":"add_pattern","text":"skips exercise sessions","type":"behavior",'
        '"evidence":["e2","e3","e5","e6"]}]'
    )
    caller = scripted(
        ("profile-update component", ops),                   # the L3 ops generation
        ("work AGAINST that goal", "yes"),                   # model confirms the tension
        ("High-impact claim", "yes"),                        # R10 evidence verification
        ("Write one short", "A week centred on exercise."),  # digest narration
    )

    report = asyncio.run(consolidate.run_weekly("2026-07-07", call_model=caller))

    # Code did the counting: one candidate, four contradicting entries.
    assert len(report.contradictions) == 1
    cand = report.contradictions[0]
    assert cand.count == 4
    assert set(cand.behavior_evidence) == {"e2", "e3", "e5", "e6"}
    assert cand.goal_text == "exercise regularly"   # flattened from the object shape

    # L3 gained the watch-list item, citing ONLY those mined entries.
    assert report.watch_items_added == 1
    prof = profile_mod.get_profile()
    assert len(prof.watch_list) == 1
    assert set(prof.watch_list[0]["evidence"]) == {"e2", "e3", "e5", "e6"}

    # The week digest was rolled up, and month/era rollups reduced the level below.
    assert report.week_digest_id is not None
    assert report.month_digest_id is not None
    assert report.era_digest_id is not None
    conn = db.get_or_create_db()
    try:
        digest = db.latest_digest(conn, "week")
    finally:
        conn.close()
    assert digest is not None
    assert json.loads(digest["stats"])["entry_count"] == 6


def test_weekly_does_not_flag_aligned_behavior_as_a_contradiction(vault_env):
    # Same-topic behaviors that recur but run TOWARD the goal must not be mislabelled.
    _seed("e1", date="2026-07-01", summary="Goal: exercise regularly.",
          themes=["exercise"], stated_goals=[{"text": "exercise regularly", "is_new": True}])
    _seed("e2", date="2026-07-02", summary="Got my exercise session in.",
          themes=["exercise"], behaviors=["exercise session"])
    _seed("e3", date="2026-07-03", summary="Another exercise session done.",
          themes=["exercise"], behaviors=["exercise session"])

    ops = (
        '[{"op":"add_goal","text":"exercise regularly","evidence":["e1"]},'
        '{"op":"add_pattern","text":"exercise session habit","type":"behavior",'
        '"evidence":["e2","e3"]}]'
    )
    caller = scripted(
        ("profile-update component", ops),
        ("work AGAINST that goal", "no"),      # model judges the behavior aligned
        ("Write one short", "A steady week."),
    )

    report = asyncio.run(consolidate.run_weekly("2026-07-07", call_model=caller))

    # The candidate is mined (same-topic behaviors recur) ...
    assert len(report.contradictions) == 1
    # ... but the model judged it aligned, so nothing lands on the watch list ...
    assert report.watch_items_added == 0
    assert report.confirmed_contradictions == []
    assert profile_mod.get_profile().watch_list == []
    # ... and it is NOT recorded as a contradiction in the digest either.
    conn = db.get_or_create_db()
    try:
        digest = db.latest_digest(conn, "week")
    finally:
        conn.close()
    assert json.loads(digest["stats"])["contradictions"] == []


def test_weekly_drops_confirmed_tension_when_verification_rejects_it(vault_env):
    _seed("e1", date="2026-07-01", summary="Goal: exercise regularly.",
          themes=["exercise"], stated_goals=[{"text": "exercise regularly", "is_new": True}])
    _seed("e2", date="2026-07-02", summary="Skipped exercise.",
          themes=["exercise"], behaviors=["skipped exercise"])
    _seed("e3", date="2026-07-03", summary="Skipped exercise again.",
          themes=["exercise"], behaviors=["skipped exercise"])

    ops = (
        '[{"op":"add_goal","text":"exercise regularly","evidence":["e1"]},'
        '{"op":"add_pattern","text":"skips exercise","type":"behavior",'
        '"evidence":["e2","e3"]}]'
    )
    caller = scripted(
        ("profile-update component", ops),
        ("work AGAINST that goal", "yes"),
        ("High-impact claim", "no"),
        ("Write one short", "A week centred on exercise."),
    )

    report = asyncio.run(consolidate.run_weekly("2026-07-07", call_model=caller))

    assert report.watch_items_added == 0
    assert report.confirmed_contradictions == []
    assert profile_mod.get_profile().watch_list == []


def test_late_extraction_is_picked_up_however_far_behind(vault_env):
    # An entry whose extraction is still pending when its day's nightly runs must not
    # be stranded — even if it completes long after (past any fixed lookback window):
    # nightly drains the oldest un-consolidated entries, so it is still folded in.
    conn = db.get_or_create_db()
    try:
        db.insert_entry(conn, id="late", date="2026-07-06", type="journal",
                        text="pending one", word_count=2, created_at="2026-07-06T23:59:00")
        db.create_pending_extraction(conn, "late", source_hash="h-late")
    finally:
        conn.close()

    # Nightly for that day sees no done extraction yet — nothing consolidated.
    r1 = asyncio.run(consolidate.run_nightly("2026-07-06", call_model=scripted()))
    assert r1.entries == 0

    # The extraction completes much later (a slow re-extraction / model outage).
    conn = db.get_or_create_db()
    try:
        db.finalize_extraction(
            conn, "late", mood=1, emotions=[], entities=[], themes=["home"], events=[],
            stated_goals=[], behaviors=[], decisions=[],
            open_loops=[{"description": "fix the sink", "status": "open"}],
            self_judgments=[], summary="Meant to fix the sink.", extracted_at="2026-07-20T02:00:00",
            source_hash="h-late",
        )
    finally:
        conn.close()

    # A nightly two weeks later — well past any short lookback — still picks it up.
    r2 = asyncio.run(consolidate.run_nightly("2026-07-20", call_model=scripted()))
    assert r2.entries == 1
    assert any(l["description"] == "fix the sink" for l in profile_mod.get_profile().open_loops)


def test_nightly_does_not_consolidate_when_l3_generation_fails(vault_env):
    # A model/parse failure must not mark entries processed — they'd never reach L3.
    _seed("e1", date="2026-07-05", summary="A quiet day.", themes=["home"])

    async def failing(prompt, *, temperature, max_tokens):
        raise RuntimeError("model down")

    r1 = asyncio.run(consolidate.run_nightly("2026-07-05", call_model=failing))
    assert r1.entries == 1

    # Still un-consolidated, so a later healthy nightly reprocesses it.
    r2 = asyncio.run(consolidate.run_nightly("2026-07-06", call_model=scripted()))
    assert r2.entries == 1


def test_weekly_forms_patterns_even_when_entries_already_consolidated(vault_env):
    # The scheduler runs nightlies (which consolidate entries) before the weekly.
    # The weekly reduce must still see the whole week so the model can form a
    # recurrence pattern — otherwise it can mine a tension but have no L3 pattern to
    # hang the watch item on.
    _seed("e1", date="2026-07-01", summary="Goal: exercise regularly.",
          themes=["exercise"], stated_goals=[{"text": "exercise regularly", "is_new": True}])
    for eid, day in [("e2", "2026-07-02"), ("e3", "2026-07-03"),
                     ("e5", "2026-07-05"), ("e6", "2026-07-06")]:
        _seed(eid, date=day, summary="Skipped exercise.", themes=["exercise"],
              behaviors=["skipped exercise"])

    # Simulate the nightlies having already folded these entries in and created the
    # goal claim — but NOT the recurrence pattern (a single day isn't a pattern).
    conn = db.get_or_create_db()
    try:
        db.mark_entries_consolidated(conn, ["e1", "e2", "e3", "e5", "e6"])
    finally:
        conn.close()
    profile_mod.save_profile(Profile(goals=[{
        "id": "g1", "text": "exercise regularly", "status": "active", "confidence": 0.6,
        "last_seen": "2026-07-06", "evidence": ["e1"], "source": "model",
    }]))

    ops = ('[{"op":"add_pattern","text":"skips exercise sessions","type":"behavior",'
           '"evidence":["e2","e3","e5","e6"]}]')
    caller = scripted(
        ("profile-update component", ops),
        ("work AGAINST that goal", "yes"),
        ("High-impact claim", "yes"),
        ("Write one short", "A week centred on exercise."),
    )

    report = asyncio.run(consolidate.run_weekly("2026-07-07", call_model=caller))
    # The reduce pass created the pattern and linked the confirmed tension.
    assert report.watch_items_added == 1
    assert len(profile_mod.get_profile().watch_list) == 1


def test_entries_are_consolidated_only_once(vault_env):
    # A second nightly over the same day does not re-fold already-consolidated entries.
    _seed("e1", date="2026-07-05", summary="A quiet day.", themes=["home"])

    r1 = asyncio.run(consolidate.run_nightly("2026-07-05", call_model=scripted()))
    assert r1.entries == 1
    r2 = asyncio.run(consolidate.run_nightly("2026-07-05", call_model=scripted()))
    assert r2.entries == 0        # nothing left unconsolidated to process


def test_real_object_shaped_l1_is_flattened_not_stringified(vault_env):
    # stated_goals/open_loops come from extract.py as objects; a nightly must lift a
    # real string description, never a stringified dict.
    _seed("mon", date="2026-07-06",
          summary="I really need to call the dentist about that tooth.",
          stated_goals=[{"text": "sort out the tooth", "is_new": True}],
          open_loops=[{"description": "call the dentist about the tooth", "status": "open"}])

    asyncio.run(consolidate.run_nightly("2026-07-06", call_model=scripted()))

    loop = profile_mod.get_profile().open_loops[0]
    assert loop["description"] == "call the dentist about the tooth"
    assert "{" not in loop["description"]        # not "{'description': ...}"


# ── open-loop reconciliation across days ──────────────────────────────────────
def test_open_loop_opened_one_day_is_resolved_by_a_later_entry(vault_env):
    _seed("mon", date="2026-07-06",
          summary="Still haven't called the dentist about that tooth.",
          open_loops=["call the dentist about the tooth"])
    # Monday: the loop is lifted into L3, still open. (No ops, no resolution.)
    asyncio.run(consolidate.run_nightly("2026-07-06", call_model=scripted()))
    prof = profile_mod.get_profile()
    assert len(prof.open_loops) == 1
    assert prof.open_loops[0]["status"] == "open"

    _seed("thu", date="2026-07-09",
          summary="Finally called the dentist; appointment is booked.")
    caller = scripted(("indicate this open loop is now resolved", "yes"))
    report = asyncio.run(consolidate.run_nightly("2026-07-09", call_model=caller))

    assert report.loops_resolved == 1
    loop = profile_mod.get_profile().open_loops[0]
    assert loop["status"] == "resolved"
    assert "thu" in loop["evidence"]        # the resolving entry is pinned


def test_open_loop_is_not_resolved_when_the_model_says_no(vault_env):
    _seed("mon", date="2026-07-06",
          summary="Still need to book the dentist.",
          open_loops=["book the dentist"])
    asyncio.run(consolidate.run_nightly("2026-07-06", call_model=scripted()))

    _seed("tue", date="2026-07-07",
          summary="Thought about the dentist again but did nothing.")
    caller = scripted(("indicate this open loop is now resolved", "no"))
    report = asyncio.run(consolidate.run_nightly("2026-07-07", call_model=caller))

    assert report.loops_resolved == 0
    assert profile_mod.get_profile().open_loops[0]["status"] == "open"


# ── revalidation of edited claims ─────────────────────────────────────────────
def test_nightly_revalidates_a_flagged_claim_and_clears_the_flag(vault_env):
    _seed("e1", date="2026-07-05",
          summary="They kept going to the gym all week.", themes=["gym"])
    flagged = {
        "id": "g1", "text": "Go to the gym regularly", "status": "active",
        "confidence": 0.6, "last_seen": "2026-07-05", "evidence": ["e1"],
        "source": "model", "needs_revalidation": True,
    }
    profile_mod.save_profile(Profile(goals=[flagged]))

    caller = scripted(("still supported by this evidence", "yes"))
    report = asyncio.run(consolidate.run_nightly("2026-07-05", call_model=caller))

    assert report.claims_revalidated == 1
    assert report.claims_cleared == 1
    assert "needs_revalidation" not in profile_mod.get_profile().goals[0]


def test_nightly_weakens_a_flagged_claim_the_evidence_no_longer_supports(vault_env):
    _seed("e1", date="2026-07-05",
          summary="Actually they quit the gym and took up reading.", themes=["reading"])
    flagged = {
        "id": "g1", "text": "Go to the gym regularly", "status": "active",
        "confidence": 0.6, "last_seen": "2026-07-05", "evidence": ["e1"],
        "source": "model", "needs_revalidation": True,
    }
    profile_mod.save_profile(Profile(goals=[flagged]))

    caller = scripted(("still supported by this evidence", "no"))
    report = asyncio.run(consolidate.run_nightly("2026-07-05", call_model=caller))

    assert report.claims_cleared == 0
    assert report.claims_decayed == 1
    goal = profile_mod.get_profile().goals[0]
    assert goal["confidence"] == pytest.approx(0.45)          # 0.6 − 0.15
    assert "needs_revalidation" not in goal


# ── rollups ───────────────────────────────────────────────────────────────────
def test_month_digest_reduces_its_week_digests(vault_env):
    conn = db.get_or_create_db()
    try:
        db.insert_digest(
            conn, level="week", period_start="2026-07-01", period_end="2026-07-07",
            summary="w1",
            stats={"entry_count": 5, "avg_mood": 2.0, "top_themes": [{"theme": "gym", "count": 3}]},
            created_at="2026-07-07",
        )
        db.insert_digest(
            conn, level="week", period_start="2026-07-08", period_end="2026-07-14",
            summary="w2",
            stats={"entry_count": 3, "avg_mood": 0.0, "top_themes": [{"theme": "work", "count": 2}]},
            created_at="2026-07-14",
        )
    finally:
        conn.close()

    digest_id = consolidate.rollup_digests("month", "2026-07-01", "2026-07-31")
    assert digest_id is not None

    conn = db.get_or_create_db()
    try:
        row = db.latest_digest(conn, "month")
    finally:
        conn.close()
    stats = json.loads(row["stats"])
    assert stats["entry_count"] == 8                 # 5 + 3, from the level below
    assert stats["child_digests"] == 2
    # weighted mean (2.0×5 + 0.0×3)/8 = 1.25, rounded to 1 dp with Python's
    # round-half-to-even → 1.2 (same rounding growth.py uses).
    assert stats["avg_mood"] == 1.2


def test_month_rollup_does_not_double_count_a_boundary_week(vault_env):
    # A week straddling the Jun/Jul boundary (ends Jul 2) belongs to exactly one
    # month — the one its period_end falls in — never summed into both.
    conn = db.get_or_create_db()
    try:
        db.insert_digest(
            conn, level="week", period_start="2026-06-29", period_end="2026-07-02",
            summary="w0", stats={"entry_count": 4, "avg_mood": None, "top_themes": []},
            created_at="2026-07-02",
        )
        db.insert_digest(
            conn, level="week", period_start="2026-07-06", period_end="2026-07-12",
            summary="w1", stats={"entry_count": 3, "avg_mood": None, "top_themes": []},
            created_at="2026-07-12",
        )
    finally:
        conn.close()

    # June contains no week that *ends* in June → nothing to roll up.
    assert consolidate.rollup_digests("month", "2026-06-01", "2026-06-30") is None
    # July owns the boundary week; both weeks counted exactly once.
    assert consolidate.rollup_digests("month", "2026-07-01", "2026-07-31") is not None
    conn = db.get_or_create_db()
    try:
        july = db.latest_digest(conn, "month")
    finally:
        conn.close()
    stats = json.loads(july["stats"])
    assert stats["entry_count"] == 7          # 4 + 3, not 4 + 3 + 4
    assert stats["child_digests"] == 2


def test_rollup_returns_none_when_nothing_below(vault_env):
    assert consolidate.rollup_digests("era", "2026-01-01", "2026-12-31") is None


# ── empty windows never crash ─────────────────────────────────────────────────
def test_nightly_and_weekly_are_no_ops_on_an_empty_vault(vault_env):
    nightly = asyncio.run(consolidate.run_nightly("2026-07-05", call_model=scripted()))
    weekly = asyncio.run(consolidate.run_weekly("2026-07-05", call_model=scripted()))
    assert nightly.entries == 0
    assert weekly.entries == 0 and weekly.watch_items_added == 0
