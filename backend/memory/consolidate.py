"""L3/L4 write loop — the consolidation cadences (R8 / V2 Phase 14).

This is the *write loop*: the background pass that turns real entries into Eva's
deeper understanding (L3 profile) and rolled-up summaries (L4 digests). The read
loop (chat/journal save) stays fast because everything expensive happens here, on
the scheduler's cadence, off the real-time path.

The one discipline that governs every function in this module
(EVA_MEMORY_ARCHITECTURE §3, IMPLEMENTATION_PLAN_V2 Phase 14): **code does all the
counting; the model only narrates.** Frequencies, evidence gathering, open-loop
matching, and the anti-hallucination evidence gate are deterministic Python; the
model is only ever asked to write prose (claims, a digest sentence) or answer a
tiny bounded yes/no. No model call in R8 ever sees more than one bounded window.

Three cadences, all reusing machinery built in R5–R7.5:

* :func:`on_save` — the post-capture hook (L0→L1→L2 already wired by
  :mod:`memory.capture`); it just runs extraction + embed and returns.
* :func:`run_nightly` — today only: refresh metrics, reconcile open loops, update
  L3 via the R7 operation engine, and act on the ``needs_revalidation`` flags that
  R5 edits leave behind (:func:`memory.capture._flag_claims_for_revalidation`).
* :func:`run_weekly` — the reduce step: deterministic miners (theme/emotion
  frequency, behavior-vs-goal contradictions) with evidence counts, then bounded
  narration, then the L3 reconcile and the week/month/era digest rollups.

Scheduling (when these fire, and never during a chat turn) is :mod:`scheduler`.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import asyncio

from . import capture, db, operations
from . import profile as profile_mod
from .operations import ModelCaller
from .profile import Profile

log = logging.getLogger("eva.memory.consolidate")

# One consolidation job runs at a time, process-wide. Both the scheduler and the
# manual POST /consolidate trigger acquire this before touching L3/L4, so a manual
# run can never race the scheduler (or another manual run) into a half-written
# profile or digest. Cheap: consolidation is off the real-time path, so serialising
# it costs nothing that matters (EVA_SYSTEM_DESIGN §8).
_job_lock = asyncio.Lock()

# ── tuning knobs (deterministic; no model) ────────────────────────────────────
# Token-overlap coefficient (intersection / smaller set) above which two short
# texts are treated as "the same topic" — used to dedupe lifted open loops and to
# match a mined candidate to an existing L3 claim. The overlap coefficient (not
# Jaccard) is deliberate: content words in short journal phrases barely overlap
# under Jaccard, so "exercise regularly" vs "skipped exercise" would never match.
_MATCH_RATIO = 0.5
# A behavior-vs-goal tension needs at least this many goal-relevant behaviors in
# the week before it becomes a candidate (a one-off slip is not a pattern).
_MIN_CONTRADICTION_COUNT = 2
# Nightly bound. It processes the *oldest un-consolidated* `done` entries, capped so
# one run's model prompt stays bounded even right after a migration or a long
# backlog. Oldest-first means a late extraction — however far behind, e.g. a row
# that finished long after its own day and its weekly window — is never permanently
# stranded: it drains in order over subsequent nightlies (or in bulk via
# `rebuild_profile`), rather than falling outside a fixed lookback window.
_NIGHTLY_MAX_ENTRIES = 25
# Themes/emotions surfaced per weekly digest.
_TOP_N = 6
# Bounded decode for the tiny yes/no checks and the one-sentence digest narration.
_YESNO_MAX_TOKENS = 8
_DIGEST_MAX_TOKENS = 120
_NARRATE_TEMPERATURE = 0.3

_WORD_RE = re.compile(r"[a-z0-9']+")
# Small stopword set so overlap keys on content words, not glue. Mirrors the spirit
# of profile._STOPWORDS; kept local so this module is self-contained.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by do does for from had has have how i if in is it
    its me my of on or our should so that the their them then they this to was we
    what when which who why will with you your not never again still keep kept
    """.split()
)


# ── reports (observability + test assertions) ─────────────────────────────────
@dataclass
class NightlyReport:
    """Counters for one :func:`run_nightly` pass."""

    entries: int = 0
    loops_opened: int = 0
    loops_resolved: int = 0
    l3_added: int = 0
    l3_strengthened: int = 0
    l3_weakened: int = 0
    claims_revalidated: int = 0
    claims_cleared: int = 0
    claims_decayed: int = 0


@dataclass
class ContradictionCandidate:
    """A deterministically-mined behavior-vs-goal tension, *with evidence counts*.

    ``goal_text`` was stated in ``goal_evidence`` entries; ``behavior_evidence`` are
    the entries whose behaviors are topically about the same goal (the candidate
    contradiction). ``count`` is ``len(behavior_evidence)`` — the number the model
    is *not* allowed to invent; it comes from the data.
    """

    goal_text: str
    goal_evidence: list[str]
    behavior_texts: list[str] = field(default_factory=list)
    behavior_evidence: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.behavior_evidence)


@dataclass
class WeeklyReport:
    """Counters + mined candidates for one :func:`run_weekly` pass."""

    week_start: str = ""
    week_end: str = ""
    entries: int = 0
    top_themes: list = field(default_factory=list)
    top_emotions: list = field(default_factory=list)      # [{emotion, count}]
    emotion_pairs: list = field(default_factory=list)      # [{pair:[a,b], count}]
    recurring_loops: list = field(default_factory=list)    # [{topic, count}]
    contradictions: list = field(default_factory=list)     # all mined candidates
    confirmed_contradictions: list = field(default_factory=list)  # model-confirmed only
    watch_items_added: int = 0
    week_digest_id: str | None = None
    month_digest_id: str | None = None
    era_digest_id: str | None = None


# ── the on-save hook ──────────────────────────────────────────────────────────
async def on_save(
    entry_id: str,
    text: str,
    entry_date: str,
    *,
    call_model: ModelCaller | None = None,
) -> str:
    """Post-capture hook: run L1 extraction + L2 embed for one just-saved entry.

    L0→L1→L2 is exactly :func:`memory.capture.run_extraction_and_embed`; this thin
    wrapper is the single seam the API routes call so the write loop owns the whole
    downstream path. It stays cheap on purpose — the heavy L3/L4 work is deferred to
    the nightly/weekly cadences, which pick this entry up by date window. Returns the
    extraction status (``done``/``null_stored``) for logging/tests.
    """
    status = await capture.run_extraction_and_embed(
        entry_id, text, entry_date, call_model=call_model
    )
    log.info("on_save: entry %s -> %s", entry_id, status)
    return status


# ── nightly (today only) ──────────────────────────────────────────────────────
async def run_nightly(
    today: str | None = None,
    *,
    call_model: ModelCaller | None = None,
    include_seeded: bool = False,
) -> NightlyReport:
    """Serialised entry point for the nightly cadence (holds :data:`_job_lock`)."""
    async with _job_lock:
        return await _run_nightly_impl(
            today, call_model=call_model, include_seeded=include_seeded
        )


async def _run_nightly_impl(
    today: str | None = None,
    *,
    call_model: ModelCaller | None = None,
    include_seeded: bool = False,
) -> NightlyReport:
    """Consolidate a day's entries into L3 (§7.2/§7.3), never blocking chat.

    Steps (IMPLEMENTATION_PLAN_V2 Phase 14 "Nightly"):
      1. Metrics: ``mood_series`` is already written at capture, so there is nothing
         to model here — the day's points are durable the moment each entry saved.
      2. Reconcile open loops: lift today's L1 open loops into L3 (code-owned), then
         mark any resolved by a later entry (a tiny yes/no model check confirms).
      3. Update L3 from today's entries via the R7 engine
         (:func:`operations.update_profile_from_entries`) — bounded, evidence-gated
         operations, never a rewrite; it also runs decay + refreshes ``typical_mood``.
      4. Revalidate claims flagged ``needs_revalidation`` by an edit (R5/ADR-001):
         re-check each against its now-current cited evidence and clear or weaken it.
    """
    today = today or operations.today_str()
    report = NightlyReport()

    # Process the oldest entries not yet folded into L3 (capped) — so an extraction
    # that finished *after* its own day's run (a slow/late/re-run extraction) is
    # still picked up here, exactly once, however far behind, rather than stranded.
    entries = _load_window(
        _EPOCH, today, include_seeded=include_seeded, only_unconsolidated=True,
    )[:_NIGHTLY_MAX_ENTRIES]
    report.entries = len(entries)
    known_ids = {e["entry_id"] for e in entries}

    # (2) open-loop reconciliation — code lifts & matches; model only confirms.
    prof = profile_mod.get_profile() or Profile()
    prof, opened, resolved = await _reconcile_open_loops(
        entries, prof, today, known_ids, call_model
    )
    report.loops_opened, report.loops_resolved = opened, resolved
    profile_mod.save_profile(prof)  # persist loop changes before the L3 update reads

    # (3) L3 update from those entries (the model narrates claims; code applies).
    if entries:
        _, applied = await operations.update_profile_from_entries(
            _for_ops(entries), call_model=call_model, today=today
        )
        report.l3_added = applied.added
        report.l3_strengthened = applied.strengthened
        report.l3_weakened = applied.weakened
        # Only mark folded-in when generation actually ran: a model/parse failure
        # (generation_ok False) leaves them un-consolidated so the next nightly
        # retries them, rather than silently dropping their L3 contribution.
        if applied.generation_ok:
            _mark_consolidated(entries)

    # (4) revalidate edited claims (ADR-001 self-heal on the normal cadence).
    checked, cleared, decayed = await _revalidate_flagged_claims(today, call_model)
    report.claims_revalidated, report.claims_cleared, report.claims_decayed = (
        checked, cleared, decayed,
    )

    log.info(
        "nightly %s: %d entries, loops +%d/-%d, L3 +%d/~%d, revalidated %d (cleared %d, decayed %d)",
        today, report.entries, report.loops_opened, report.loops_resolved,
        report.l3_added, report.l3_strengthened, report.claims_revalidated,
        report.claims_cleared, report.claims_decayed,
    )
    return report


# ── weekly (the reduce step) ──────────────────────────────────────────────────
async def run_weekly(
    week_end: str | None = None,
    *,
    call_model: ModelCaller | None = None,
    include_seeded: bool = False,
) -> WeeklyReport:
    """Serialised entry point for the weekly cadence (holds :data:`_job_lock`)."""
    async with _job_lock:
        return await _run_weekly_impl(
            week_end, call_model=call_model, include_seeded=include_seeded
        )


async def _run_weekly_impl(
    week_end: str | None = None,
    *,
    call_model: ModelCaller | None = None,
    include_seeded: bool = False,
) -> WeeklyReport:
    """Reduce a week of entries into L3 tensions + rolled-up digests (§3/§5.5).

    Order matters (IMPLEMENTATION_PLAN_V2 Phase 14 "Weekly"):
      1. Deterministic mining FIRST (no model): theme frequency and behavior-vs-goal
         contradictions, each with **evidence counts** from the data.
      2. L3 update over the week so goal/pattern claims exist to hang tensions on
         (the model narrates the claims; the R7 evidence gate rejects any it can't
         ground in a real entry).
      3. Reconcile: for each mined contradiction, link the matching L3 pattern to the
         matching L3 goal via a code-built ``note_contradiction`` op → a ``watch_list``
         item carrying the mined evidence. No new model call — the counting is done.
      4. Roll up the week digest (a bounded one-sentence narration + code-computed
         stats). Month/era digests reduce the level below via :func:`rollup_digests`.
    """
    week_end = week_end or operations.today_str()
    week_start = _days_before(week_end, 6)
    report = WeeklyReport(week_start=week_start, week_end=week_end)

    entries = _load_window(week_start, week_end, include_seeded=include_seeded)
    report.entries = len(entries)
    if not entries:
        log.info("weekly %s..%s: no entries", week_start, week_end)
        return report

    # (1) mine first — pure counting across all four dimensions the scope names,
    # over the WHOLE week (read-only; counts include already-consolidated entries).
    report.top_themes = _mine_top_themes(entries)
    report.top_emotions, report.emotion_pairs = _mine_emotions(entries)
    report.recurring_loops = _mine_open_loop_recurrence(entries)
    report.contradictions = _mine_behavior_vs_goal(entries)

    # (2) update L3 over the WHOLE week so the model can form recurrence-based claims
    # — a pattern like "keeps skipping exercise" only emerges from the week's entries
    # together, so this must not be limited to entries a nightly hasn't already seen
    # (that would leave the reduce pass with nothing and no pattern to hang a
    # contradiction on). Re-seeing nightly's entries is safe: the deterministic
    # text-dedupe guard in apply_operations folds a re-stated claim into the existing
    # one instead of duplicating. Mark any not-yet-consolidated entries folded, but
    # only if generation actually ran.
    known_ids = {e["entry_id"] for e in entries}
    _, applied = await operations.update_profile_from_entries(
        _for_ops(entries), call_model=call_model, today=week_end
    )
    if applied.generation_ok:
        _mark_consolidated([e for e in entries if not e["consolidated"]])

    # (3) confirm + record contradictions (model judges; code links & gates).
    report.watch_items_added, report.confirmed_contradictions = await _record_contradictions(
        report.contradictions, known_ids, week_end, call_model
    )

    # (4) roll up week → month → era (each reduces the level below; §3/§5.5).
    report.week_digest_id = await _build_week_digest(
        week_start, week_end, entries, report, call_model=call_model
    )
    report.month_digest_id, report.era_digest_id = _rollup_month_and_era(week_end)

    log.info(
        "weekly %s..%s: %d entries, %d theme(s), %d emotion(s), %d recurring loop(s), "
        "%d contradiction(s), +%d watch item(s)",
        week_start, week_end, report.entries, len(report.top_themes),
        len(report.top_emotions), len(report.recurring_loops),
        len(report.contradictions), report.watch_items_added,
    )
    return report


# ── open-loop reconciliation ──────────────────────────────────────────────────
async def _reconcile_open_loops(
    entries: list[dict],
    prof: Profile,
    today: str,
    known_ids: set[str],
    call_model: ModelCaller | None,
) -> tuple[Profile, int, int]:
    """Lift new open loops into L3 and resolve any a later entry closes.

    Open loops are **code-owned** (deterministic — code counts), so creation does not
    go through the model operation grammar: today's L1 ``open_loops`` become L3 loop
    records directly, deduped by topic overlap against loops already open. Resolution
    is where the model earns its keep, but only barely: for a loop that a *different,
    later* entry is topically about, one bounded yes/no check ("does this indicate the
    loop is resolved?") gates the existing :func:`operations.apply_operations`
    ``mark_resolved`` op, so the resolution is still evidence-pinned and deterministic
    to apply. Returns ``(profile, opened, resolved)``.
    """
    loops = [dict(l) for l in prof.open_loops]

    def _open() -> list[dict]:
        return [l for l in loops if str(l.get("status", "open")) != "resolved"]

    # (a) lift new loops from today's entries.
    opened = 0
    for e in entries:
        for text in e["open_loops"]:
            toks = _tokens(text)
            if not toks:
                continue
            if any(_overlap(toks, _tokens(l.get("description", ""))) >= _MATCH_RATIO
                   for l in _open()):
                continue  # already tracked under an equivalent loop
            loops.append({
                "id": f"loop-{uuid.uuid4()}",
                "description": text,
                "status": "open",
                "opened": e["date"],
                "last_updated": today,
                "evidence": [e["entry_id"]],
                "notes": [],
                "source": "code",
            })
            opened += 1

    # (b) resolve loops a *later, different* entry closes.
    resolve_ops: list[dict] = []
    for loop in _open():
        loop_toks = _tokens(loop.get("description", ""))
        if not loop_toks:
            continue
        opener_ids = set(loop.get("evidence") or [])
        opened_on = str(loop.get("opened") or "")
        for e in entries:
            if e["entry_id"] in opener_ids:
                continue  # never resolve a loop with the entry that opened it
            if opened_on and e["date"] < opened_on:
                continue  # a loop can only be resolved by something that came after
            if not _shares_topic(loop_toks, _entry_tokens(e)):
                continue
            confirmed = await _yes_no(_resolution_prompt(loop, e), call_model)
            if confirmed:
                resolve_ops.append({
                    "op": "mark_resolved",
                    "loop_id": loop["id"],
                    "evidence": [e["entry_id"]],
                })
                break  # this loop is closed; move on

    prof = replace(prof, open_loops=loops)
    if resolve_ops:
        prof, _rep = operations.apply_operations(
            prof, resolve_ops, known_entry_ids=known_ids, today=today
        )
    return prof, opened, len(resolve_ops)


def _resolution_prompt(loop: dict, entry: dict) -> str:
    """A tiny bounded yes/no: did ``entry`` resolve this open loop?"""
    return (
        "An open loop is something the person left unresolved.\n\n"
        f"Open loop: {loop.get('description', '')}\n"
        f"A later entry: {entry.get('summary') or entry.get('text', '')}\n\n"
        "Does the later entry indicate this open loop is now resolved? "
        "Answer with only 'yes' or 'no'."
    )


# ── revalidation of edited claims (ADR-001 self-heal) ─────────────────────────
async def _revalidate_flagged_claims(
    today: str, call_model: ModelCaller | None
) -> tuple[int, int, int]:
    """Re-audit L3 claims flagged ``needs_revalidation`` after an entry they cite was
    edited (set by :func:`memory.capture._flag_claims_for_revalidation`).

    For each flagged goal/pattern, re-pull its cited entries' *current* summaries and
    ask one bounded yes/no ("still supported by its evidence?"). Supported claims
    clear the flag; unsupported ones lose confidence (§5.4 decay) — dropping below
    :data:`operations.REVIEW_THRESHOLD` marks them ``needs_review`` — and clear the
    flag either way so a persistently-edited entry doesn't re-audit forever. Returns
    ``(checked, cleared, decayed)``. Best-effort: a read/model failure leaves the flag
    set for the next cadence rather than silently dropping a claim.
    """
    prof = profile_mod.get_profile()
    if prof is None:
        return 0, 0, 0

    conn = db.get_or_create_db()
    try:
        checked = cleared = decayed = 0
        goals = [dict(g) for g in prof.goals]
        patterns = [dict(p) for p in prof.patterns]
        for claim in (*goals, *patterns):
            if not claim.get("needs_revalidation"):
                continue
            checked += 1
            cited = list(claim.get("evidence") or []) + list(claim.get("counter_evidence") or [])
            summaries = _summaries_for(conn, cited)
            supported = await _yes_no(_revalidation_prompt(claim, summaries), call_model)
            if supported is None:
                # Couldn't decide (no model / unparseable) — leave the flag for later.
                checked -= 1
                continue
            claim.pop("needs_revalidation", None)
            if supported:
                cleared += 1
            else:
                claim["confidence"] = operations._clamp(
                    operations._conf(claim) - operations.WEAKEN_DELTA
                )
                if claim["confidence"] < operations.REVIEW_THRESHOLD:
                    claim["needs_review"] = True
                decayed += 1
    finally:
        conn.close()

    if checked:
        profile_mod.save_profile(replace(prof, goals=goals, patterns=patterns))
    return checked, cleared, decayed


def _revalidation_prompt(claim: dict, summaries: list[str]) -> str:
    """A tiny bounded yes/no: is ``claim`` still supported by its cited evidence?"""
    evidence = "\n".join(f"- {s}" for s in summaries) or "- (no evidence text available)"
    return (
        f"Claim about the person: {claim.get('text', '')}\n\n"
        f"The entries this claim rests on now say:\n{evidence}\n\n"
        "Is the claim still supported by this evidence? Answer with only 'yes' or 'no'."
    )


# ── deterministic weekly miners (no model) ────────────────────────────────────
def _mine_top_themes(entries: list[dict]) -> list[dict]:
    """Count theme frequency across the window → ``[{theme, count}]`` (most first)."""
    counts: dict[str, int] = {}
    for e in entries:
        for t in e["themes"]:
            key = str(t).strip().lower()
            if key:
                counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
    return [{"theme": t, "count": c} for t, c in ranked]


def _mine_emotions(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Count emotion frequency and co-occurrence across the window (model-free).

    Returns ``(top_emotions, pairs)`` where ``top_emotions`` is ``[{emotion, count}]``
    and ``pairs`` is ``[{pair:[a,b], count}]`` for emotions that show up together in
    the same entry — the raw material for "what tends to travel with what" without any
    model narration.
    """
    freq: dict[str, int] = {}
    pair_counts: dict[tuple[str, str], int] = {}
    for e in entries:
        names = []
        for em in e["emotions"]:
            name = str(em.get("name")).strip().lower() if isinstance(em, dict) else str(em).strip().lower()
            if name:
                names.append(name)
        for name in set(names):
            freq[name] = freq.get(name, 0) + 1
        unique = sorted(set(names))
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                key = (unique[i], unique[j])
                pair_counts[key] = pair_counts.get(key, 0) + 1
    top = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
    pairs = sorted(pair_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
    return (
        [{"emotion": n, "count": c} for n, c in top],
        [{"pair": list(p), "count": c} for p, c in pairs if c > 1],
    )


def _mine_open_loop_recurrence(entries: list[dict]) -> list[dict]:
    """Count how often the same open-loop topic recurs across the window (model-free).

    Loops raised on multiple days cluster by topic overlap into one recurring item
    with a count — surfacing the thing the person keeps leaving unresolved. Returns
    ``[{topic, count}]`` for topics seen more than once, most-recurrent first.
    """
    clusters: list[dict] = []
    for e in entries:
        for text in e["open_loops"]:
            toks = _tokens(text)
            if not toks:
                continue
            match = next((c for c in clusters if _overlap(toks, c["_toks"]) >= _MATCH_RATIO), None)
            if match is None:
                clusters.append({"topic": text, "count": 1, "_toks": toks})
            else:
                match["count"] += 1
    recurring = [{"topic": c["topic"], "count": c["count"]} for c in clusters if c["count"] > 1]
    recurring.sort(key=lambda c: -c["count"])
    return recurring[:_TOP_N]


def _rollup_month_and_era(week_end: str) -> tuple[str | None, str | None]:
    """Reduce the just-written week digest up into its month and an all-time era.

    Keeps the map-reduce hierarchy live in the real path (not just tests): the month
    digest reduces the ``week`` rows in ``week_end``'s calendar month, and the era
    digest reduces every ``month`` row. Each reads only the level below via
    :func:`rollup_digests`. Best-effort — a rollup failure never fails the weekly pass.
    """
    try:
        d = date.fromisoformat(week_end)
        month_start = d.replace(day=1).isoformat()
        # Last day of the month: first day of next month, minus one.
        next_month = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = (next_month - timedelta(days=1)).isoformat()
        month_id = rollup_digests("month", month_start, month_end)
        era_id = rollup_digests("era", "0001-01-01", "9999-12-31")
        return month_id, era_id
    except Exception as exc:  # noqa: BLE001 — rollups are best-effort
        log.warning("month/era rollup failed for %s: %s", week_end, exc)
        return None, None


def _mine_behavior_vs_goal(entries: list[dict]) -> list[ContradictionCandidate]:
    """Find goals whose *same-topic* behaviors recur across the week (with counts).

    Deterministic, evidence-counted, model-free. For each distinct stated goal in the
    window, gather the behaviors that are topically about that goal (token overlap):
    a goal pursued in words but repeatedly acted on the *other* way shows up here as a
    goal with several related behavior entries. The model later narrates *whether*
    (and how) the behavior runs against the goal; code only supplies the candidate and
    the counts, so the number of contradicting entries can never be invented.
    Candidates with fewer than :data:`_MIN_CONTRADICTION_COUNT` behaviors are dropped
    (a single slip is not a pattern). Ranked by evidence count, descending.
    """
    goals: list[tuple[str, str]] = []   # (text, entry_id)
    behaviors: list[tuple[str, str]] = []
    for e in entries:
        for g in e["stated_goals"]:
            if str(g).strip():
                goals.append((str(g).strip(), e["entry_id"]))
        for b in e["behaviors"]:
            if str(b).strip():
                behaviors.append((str(b).strip(), e["entry_id"]))

    # Group goals by topic signature so "get in shape" stated on 3 days is one goal.
    seen: list[ContradictionCandidate] = []
    for gtext, gid in goals:
        gtoks = _tokens(gtext)
        if not gtoks:
            continue
        existing = next((c for c in seen if _overlap(_tokens(c.goal_text), gtoks) >= _MATCH_RATIO), None)
        if existing is None:
            existing = ContradictionCandidate(goal_text=gtext, goal_evidence=[])
            seen.append(existing)
        if gid not in existing.goal_evidence:
            existing.goal_evidence.append(gid)

    for cand in seen:
        gtoks = _tokens(cand.goal_text)
        for btext, bid in behaviors:
            if _overlap(gtoks, _tokens(btext)) >= _MATCH_RATIO:
                cand.behavior_texts.append(btext)
                if bid not in cand.behavior_evidence:
                    cand.behavior_evidence.append(bid)

    candidates = [c for c in seen if c.count >= _MIN_CONTRADICTION_COUNT]
    candidates.sort(key=lambda c: -c.count)
    return candidates


async def _record_contradictions(
    candidates: list[ContradictionCandidate],
    known_ids: set[str],
    today: str,
    call_model: ModelCaller | None,
) -> tuple[int, list[ContradictionCandidate]]:
    """Confirm each mined *candidate* is a real tension, then record it on L3.

    Code supplies the candidate and the evidence counts (same-topic behaviors that
    recur against a stated goal), but same-topic is not the same as *opposing* — a
    person who says "exercise more" and logs "went running" is aligned, not
    contradicting. So before writing anything, one bounded yes/no model check judges
    whether the behaviors actually run **against** the goal; only a confirmed tension
    becomes a ``watch_list`` item. This is the "model narrates/judges, code counts"
    split: the number is the data's, the *interpretation* is the model's, and a
    candidate the model rejects is dropped rather than mislabelled.

    The write itself reloads the profile (the weekly L3 update just created the
    goal/pattern claims), matches the candidate to a real goal + pattern claim, and
    applies a code-built ``note_contradiction`` op — the existing evidence gate +
    claim-existence check reject anything ungrounded. Returns ``(items_added,
    confirmed_candidates)`` so callers (and the digest) reflect only real tensions,
    never the raw same-topic candidates.
    """
    prof = profile_mod.get_profile()
    if prof is None or not candidates:
        return 0, []

    ops: list[dict] = []
    confirmed: list[ContradictionCandidate] = []
    for cand in candidates:
        goal = _best_claim(cand.goal_text, prof.goals)
        pattern = _best_claim_multi(cand.behavior_texts, prof.patterns)
        if goal is None or pattern is None:
            log.info(
                "weekly: mined tension on %r has no matching L3 claim yet; skipping",
                cand.goal_text,
            )
            continue
        evidence = [eid for eid in cand.behavior_evidence if eid in known_ids]
        if not evidence:
            continue
        opposes = await _yes_no(_contradiction_prompt(cand), call_model)
        if opposes is not True:
            # Undecided or aligned → not a contradiction; never mislabel it.
            log.info(
                "weekly: candidate on %r not confirmed as a contradiction (%r)",
                cand.goal_text, opposes,
            )
            continue
        confirmed.append(cand)
        ops.append({
            "op": "note_contradiction",
            "claim_id_a": pattern["id"],
            "claim_id_b": goal["id"],
            "description": (
                f"Wants to {cand.goal_text}, but acted against it {cand.count} time(s) "
                f"this week."
            ),
            "evidence": evidence,
        })

    if not ops:
        return 0, []
    updated, applied = operations.apply_operations(
        prof, ops, known_entry_ids=known_ids, today=today
    )
    profile_mod.save_profile(updated)
    return applied.contradictions, confirmed


def _contradiction_prompt(cand: ContradictionCandidate) -> str:
    """A tiny bounded yes/no: do these behaviors run against the stated goal?"""
    behaviors = "\n".join(f"- {b}" for b in dict.fromkeys(cand.behavior_texts))
    return (
        f"The person's stated goal: {cand.goal_text}\n\n"
        f"This week they did:\n{behaviors}\n\n"
        "Do these behaviors work AGAINST that goal (not toward it)? "
        "Answer with only 'yes' or 'no'."
    )


# ── rollups: week → month → era ───────────────────────────────────────────────
async def _build_week_digest(
    week_start: str,
    week_end: str,
    entries: list[dict],
    report: WeeklyReport,
    *,
    call_model: ModelCaller | None,
) -> str:
    """Compute the week's stats (code) + one bounded narration (model), persist it."""
    moods = [e["mood"] for e in entries
             if isinstance(e["mood"], int) and not isinstance(e["mood"], bool)]
    stats = {
        "entry_count": len(entries),
        "avg_mood": round(sum(moods) / len(moods), 1) if moods else None,
        "top_themes": report.top_themes,
        "top_emotions": report.top_emotions,
        "emotion_pairs": report.emotion_pairs,
        "recurring_loops": report.recurring_loops,
        # Only model-confirmed tensions — a same-topic candidate the model judged
        # aligned must not be recorded as a contradiction in the digest either.
        "contradictions": [
            {"goal": c.goal_text, "count": c.count} for c in report.confirmed_contradictions
        ],
    }
    summary = await _narrate_digest("week", week_start, week_end, stats, call_model)
    conn = db.get_or_create_db()
    try:
        return db.insert_digest(
            conn, level="week", period_start=week_start, period_end=week_end,
            summary=summary, stats=stats, created_at=operations.today_str(),
        )
    finally:
        conn.close()


def rollup_digests(level: str, period_start: str, period_end: str) -> str | None:
    """Reduce the level below into one ``month`` or ``era`` digest (map-reduce).

    A ``month`` digest reduces the ``week`` digests that *end* within the month; an
    ``era`` digest reduces the ``month`` digests that end within its span. Children are
    selected by ``period_end`` containment (``by_period_end``), so a week straddling a
    month boundary belongs to exactly one month — otherwise it would be summed into
    both neighbouring months and then double-counted again in the era. Each reads only
    the level below (never the raw entries), so no input is ever larger than one
    bounded window (§3/§5.5). Stats are summed/averaged from the children; narration is
    left to a caller that wants it (kept model-free here so a rollup is pure and
    cheap). Returns the new digest id, or ``None`` when there is nothing to reduce.
    """
    child_level = {"month": "week", "era": "month"}.get(level)
    if child_level is None:
        raise ValueError(f"rollup_digests: unsupported level {level!r}")

    conn = db.get_or_create_db()
    try:
        children = db.digests_for_level(
            conn, child_level, date_from=period_start, date_to=period_end,
            by_period_end=True,
        )
        if not children:
            return None
        entry_count = 0
        mood_weighted = 0.0
        mood_n = 0
        theme_counts: dict[str, int] = {}
        for row in children:
            cstats = _loads(row["stats"])
            n = int(cstats.get("entry_count") or 0)
            entry_count += n
            avg = cstats.get("avg_mood")
            if isinstance(avg, (int, float)) and n:
                mood_weighted += float(avg) * n
                mood_n += n
            for t in cstats.get("top_themes") or []:
                key = str(t.get("theme", "")).strip().lower()
                if key:
                    theme_counts[key] = theme_counts.get(key, 0) + int(t.get("count") or 0)
        top = sorted(theme_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
        stats = {
            "entry_count": entry_count,
            "avg_mood": round(mood_weighted / mood_n, 1) if mood_n else None,
            "top_themes": [{"theme": t, "count": c} for t, c in top],
            "child_digests": len(children),
        }
        return db.insert_digest(
            conn, level=level, period_start=period_start, period_end=period_end,
            summary=None, stats=stats, created_at=operations.today_str(),
        )
    finally:
        conn.close()


async def _narrate_digest(
    level: str, start: str, end: str, stats: dict, call_model: ModelCaller | None
) -> str | None:
    """One bounded sentence describing the period, strictly from ``stats`` (no facts
    the model can invent). Returns ``None`` if the model is unavailable/empty — the
    digest still persists its code-computed stats."""
    themes = ", ".join(t["theme"] for t in stats.get("top_themes") or []) or "nothing in particular"
    prompt = (
        f"Write one short, plain sentence summarizing this {level} for a personal "
        f"journal. Do not add facts beyond these:\n"
        f"- entries: {stats.get('entry_count')}\n"
        f"- average mood (-5..+5): {stats.get('avg_mood')}\n"
        f"- recurring themes: {themes}\n"
        f"Be descriptive, not a verdict."
    )
    call = call_model or operations._llama_server_call
    try:
        raw = await call(prompt, temperature=_NARRATE_TEMPERATURE, max_tokens=_DIGEST_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 — narration is optional
        log.warning("digest narration failed (%s..%s): %s", start, end, exc)
        return None
    text = raw.strip()
    return text or None


# ── small shared helpers ──────────────────────────────────────────────────────
_EPOCH = "0001-01-01"


def _load_window(
    date_from: str,
    date_to: str,
    *,
    include_seeded: bool,
    only_unconsolidated: bool = False,
) -> list[dict]:
    """Load an inclusive date window of done extractions as parsed entry dicts."""
    conn = db.get_or_create_db()
    try:
        rows = db.entries_for_consolidation(
            conn, date_from=date_from, date_to=date_to,
            include_seeded=include_seeded, only_unconsolidated=only_unconsolidated,
        )
    finally:
        conn.close()
    return [_row_to_entry(r) for r in rows]


def _mark_consolidated(entries: list[dict]) -> None:
    """Flag ``entries`` as folded into L3 so a later run never double-counts them."""
    ids = [e["entry_id"] for e in entries]
    if not ids:
        return
    conn = db.get_or_create_db()
    try:
        db.mark_entries_consolidated(conn, ids)
    finally:
        conn.close()


def _row_to_entry(row) -> dict:
    """Turn a DB row into a plain dict with L1 fields parsed to the shapes we mine.

    Critically, ``stated_goals`` and ``open_loops`` are stored by
    :mod:`memory.extract` as **objects** (``{"text", "is_new"}`` /
    ``{"description", "status"}``), not strings — so they are flattened to their text
    here. Every downstream miner then sees plain strings, and an open loop lifted into
    L3 gets a real description rather than a stringified dict. ``behaviors`` /
    ``self_judgments`` are already string lists but are normalised defensively.
    """
    return {
        "entry_id": row["entry_id"],
        "date": row["date"],
        "text": row["text"] or "",
        "summary": row["summary"] or "",
        "mood": row["mood"],
        "consolidated": bool(row["consolidated"]),
        "emotions": _loads_list(row["emotions"]),           # [{name, intensity}]
        "themes": _normalize_texts(row["themes"]),
        "stated_goals": _normalize_texts(row["stated_goals"], "text"),
        "behaviors": _normalize_texts(row["behaviors"], "text"),
        "open_loops": _normalize_texts(row["open_loops"], "description"),
        "self_judgments": _normalize_texts(row["self_judgments"], "text"),
    }


def _normalize_texts(value, *keys: str) -> list[str]:
    """Flatten an L1 list field to plain strings, tolerating both shapes.

    Items may be strings (``themes``, ``behaviors``) or objects (``stated_goals`` →
    ``text``, ``open_loops`` → ``description``). ``keys`` names the string field(s) to
    pull from a dict item, in order. Anything empty is dropped.
    """
    out: list[str] = []
    for item in _loads_list(value):
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = ""
            for k in keys:
                candidate = item.get(k)
                if isinstance(candidate, str) and candidate.strip():
                    text = candidate.strip()
                    break
        else:
            text = str(item).strip()
        if text:
            out.append(text)
    return out


def _for_ops(entries: list[dict]) -> list[dict]:
    """Project entries to the shape :func:`operations.render_prompt` reads."""
    return [
        {
            "entry_id": e["entry_id"],
            "date": e["date"],
            "summary": e["summary"],
            "themes": e["themes"],
        }
        for e in entries
    ]


def _summaries_for(conn, entry_ids: list[str]) -> list[str]:
    """Current summaries for cited entries (for the revalidation prompt)."""
    out: list[str] = []
    for eid in entry_ids:
        row = db.get_extraction(conn, eid)
        if row is not None and row["summary"]:
            out.append(str(row["summary"]))
    return out


async def _yes_no(prompt: str, call_model: ModelCaller | None) -> bool | None:
    """Run a bounded yes/no model check. Returns ``True``/``False``/``None`` (undecided).

    ``None`` (no model reachable, or an unparseable answer) is a first-class result:
    callers treat it as "can't tell — leave state as-is", so a missing model never
    forces a resolution or drops a claim.
    """
    call = call_model or operations._llama_server_call
    try:
        raw = await call(prompt, temperature=0.0, max_tokens=_YESNO_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 — a failed check is "undecided", never fatal
        log.warning("yes/no check model call failed: %s", exc)
        return None
    head = raw.strip().lower()[:16]
    if "yes" in head:
        return True
    if "no" in head:
        return False
    return None


def _tokens(text: str) -> set[str]:
    """Content-word token set for overlap scoring (lowercased, stopwords dropped)."""
    return {w for w in _WORD_RE.findall(str(text).lower()) if w not in _STOPWORDS and len(w) > 1}


def _entry_tokens(entry: dict) -> set[str]:
    """Tokens representing what an entry is *about* (summary + its own open loops)."""
    parts = [entry.get("summary", ""), " ".join(entry.get("open_loops", []))]
    return _tokens(" ".join(parts))


def _overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient of two token sets (|a∩b| / min(|a|,|b|)); 0 if either empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _shares_topic(a: set[str], b: set[str]) -> bool:
    """True if two token sets share any content word — the lenient loop pre-filter.

    Open-loop resolution only needs a *candidate* here; the bounded yes/no model
    check is the real gate, so this stays generous (word forms like call/called
    won't overlap, but a shared noun like "dentist" is enough to ask the question).
    """
    return bool(a & b)


def _best_claim(text: str, claims: list[dict]) -> dict | None:
    """Best topic-overlap claim above threshold, or ``None``."""
    toks = _tokens(text)
    best, best_score = None, _MATCH_RATIO
    for claim in claims:
        score = _overlap(toks, _tokens(claim.get("text", "")))
        if score >= best_score:
            best, best_score = claim, score
    return best


def _best_claim_multi(texts: list[str], claims: list[dict]) -> dict | None:
    """Best claim across several candidate texts (for behavior→pattern matching)."""
    best, best_score = None, _MATCH_RATIO
    for text in texts:
        toks = _tokens(text)
        for claim in claims:
            score = _overlap(toks, _tokens(claim.get("text", "")))
            if score >= best_score:
                best, best_score = claim, score
    return best


def _days_before(day: str, n: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=n)).isoformat()


def _loads(value):
    try:
        return json.loads(value) if value else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _loads_list(value) -> list:
    try:
        parsed = json.loads(value) if value else []
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []
