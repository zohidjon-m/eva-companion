"""L3 update engine — the real user model, built by evidence-backed operations.

This is the writer half of the L3 seam (:mod:`memory.profile` is the reader). The
model never touches ``profile.json`` directly; instead, each nightly/weekly update
emits a small list of **operations** (EVA_MEMORY_ARCHITECTURE §7.3) — ``add_goal``,
``strengthen``, ``weaken``, ``note_contradiction``, … — each carrying an evidence
pointer to the justifying entry. This module validates and applies them
deterministically, then persists through :func:`profile.save_profile`.

Three §7.2/§7.3 disciplines are enforced here and unit-tested without a model:

  * **Evidence gate (anti-hallucination).** Any assertion-shaped operation whose
    ``evidence[]`` cites no *known* entry uid is silently rejected. The model
    cannot invent a claim it can't ground in a real entry.
  * **Deterministic apply + decay.** Confidence rises with corroboration
    (``strengthen``), falls without it (``weaken``, nightly ``decay``), and every
    change is a fixed arithmetic step — the model writes prose, never numbers.
  * **Anchor protection.** A claim the user corrected (``source == "user"`` / id in
    ``anchors``) cannot be strengthened, weakened, or restatused by the model. Only
    the user changes anchors, via ``PUT /profile`` (:func:`profile.parse_markdown`).

R7 scope: the deterministic apply engine plus a *minimal* model-backed generator.
The consolidation scheduler and weekly miners that decide *when* to call
:func:`update_profile_from_entries` are R8.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable

from . import profile as profile_mod
from .profile import Profile

log = logging.getLogger("eva.memory.operations")

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "profile_operation.md"
_ENTRIES_PLACEHOLDER = "{{ENTRIES}}"
_PROFILE_PLACEHOLDER = "{{PROFILE}}"

# Generation decode settings mirror extraction: cold temperature for parseable,
# consistent JSON; both attempts run at 0.3 (the pre-step's clean temp).
GENERATE_TEMPERATURE = 0.3
RETRY_TEMPERATURE = 0.3
MAX_TOKENS = 900

# Max entries fed to the model in one operation-generation call. Keeps each call's
# input bounded (EVA_MEMORY_ARCHITECTURE §5: "bounded input, bounded output") so a
# rebuild over a large vault never sends the whole history in a single prompt.
BATCH_SIZE = 15

# ── §7.3 confidence arithmetic ────────────────────────────────────────────────
NEW_CLAIM_CONFIDENCE = 0.5   # add_goal / add_pattern start here
STRENGTHEN_DELTA = 0.1       # strengthen: += 0.1, capped at 1.0
WEAKEN_DELTA = 0.15          # weaken: -= 0.15, floored at 0.0
CONFIDENCE_CAP = 1.0
CONFIDENCE_FLOOR = 0.0
REVIEW_THRESHOLD = 0.2       # weaken below this flags the claim for review
DECAY_PER_DAY = 0.01         # nightly: -= 0.01 × days_since_last_seen
STALE_CONFIDENCE = 0.5       # a claim under this, unseen ≥ STALE_DAYS, is stale
STALE_DAYS = 60

GOAL_STATUSES = {"active", "paused", "achieved", "abandoned"}
PATTERN_TYPES = {"behavior", "cognitive", "emotional"}  # matches profile_operation.md
# The two emotional_baseline list fields the model may append to (R7.5). It may
# NOT touch ``typical_mood`` — that is code-derived (:func:`derive_typical_mood`).
BASELINE_ITEM_FIELDS = {"known_triggers", "what_helps"}

# Required fields per operation (§7.3). ``set_anchor`` is user-only and never
# emitted by the engine, so it is not appliable here.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "add_goal": ("text", "evidence"),
    "add_pattern": ("text", "type", "evidence"),
    "update_goal_status": ("goal_id", "status", "evidence"),
    "strengthen": ("claim_id", "evidence"),
    "weaken": ("claim_id", "reason", "evidence"),
    "note_contradiction": ("claim_id_a", "claim_id_b", "evidence"),
    "mark_resolved": ("loop_id", "evidence"),
    "update_loop": ("loop_id", "note", "evidence"),
    "add_relationship_note": ("name", "note", "evidence"),
    # R7.5 — evidence-backed identity & emotional baseline (singleton §7.2 dicts).
    "set_identity": ("text", "evidence"),
    "add_principle": ("text", "evidence"),
    "add_baseline_item": ("field", "text", "evidence"),
}

# Every model operation must cite at least one real entry (the R7 realignment rule:
# "reject any model operation without valid evidence"). This includes ``weaken`` —
# a model may only erode a claim when a specific entry contradicts it; uncorroborated
# fading is handled deterministically by :func:`apply_decay`, never by the model.
_EVIDENCE_REQUIRED = frozenset(_REQUIRED_FIELDS)

# A ModelCaller takes the rendered prompt + decode params and returns raw model
# text. The default talks to llama-server; tests inject a fake. (Same seam as
# :data:`memory.extract.ModelCaller`.)
ModelCaller = Callable[..., Awaitable[str]]


@dataclass
class AppliedReport:
    """Counters + reasons for one :func:`apply_operations` run (auditable trail)."""

    added: int = 0
    strengthened: int = 0
    weakened: int = 0
    status_changed: int = 0
    resolved: int = 0
    updated_loops: int = 0
    contradictions: int = 0
    relationship_notes: int = 0
    identity_set: int = 0
    principles_added: int = 0
    baseline_items_added: int = 0
    rejected: int = 0
    # False when the model's operation generation failed on both attempts (as opposed
    # to succeeding with no changes). Consolidation uses this to avoid marking entries
    # processed when L3 generation never actually ran — see :func:`generate_operations`.
    generation_ok: bool = True
    reasons: list = field(default_factory=list)

    def _reject(self, why: str) -> None:
        self.rejected += 1
        self.reasons.append(why)


def today_str() -> str:
    """Today's date as an ISO ``YYYY-MM-DD`` string (the ``last_seen`` format)."""
    return date.today().isoformat()


# ── validation: the evidence gate (pure, model-independent) ───────────────────
def validate_operations(
    ops: list, known_entry_ids: set[str]
) -> tuple[list[dict], list[str]]:
    """Split ``ops`` into (structurally valid, rejection reasons).

    Enforces the two model-independent gates: every operation must name a known
    verb with all its §7.3 required fields, and every *assertion* operation must
    cite at least one ``entry_id`` present in ``known_entry_ids``. Claim-existence
    and anchor checks depend on the profile and are done in :func:`apply_operations`.
    """
    valid: list[dict] = []
    reasons: list[str] = []
    known = known_entry_ids or set()

    for op in ops:
        if not isinstance(op, dict):
            reasons.append(f"not an object: {op!r}")
            continue
        verb = op.get("op")
        required = _REQUIRED_FIELDS.get(verb)
        if required is None:
            reasons.append(f"unknown operation: {verb!r}")
            continue
        missing = [f for f in required if op.get(f) in (None, "", [])]
        if missing:
            reasons.append(f"{verb}: missing required field(s) {missing}")
            continue
        if verb in _EVIDENCE_REQUIRED:
            evidence = op.get("evidence")
            cited = [e for e in evidence if e in known] if isinstance(evidence, list) else []
            if not cited:
                reasons.append(f"{verb}: no cited evidence is a known entry uid")
                continue
        valid.append(op)

    return valid, reasons


# ── apply: the deterministic §7.3 grammar ─────────────────────────────────────
def apply_operations(
    profile: Profile,
    ops: list,
    *,
    known_entry_ids: set[str],
    today: str | None = None,
) -> tuple[Profile, AppliedReport]:
    """Apply a batch of operations to ``profile``, deterministically (§7.3).

    Runs :func:`validate_operations` first (evidence gate), then applies each
    surviving op against the §7.2 shapes. Claim-existence and anchor protection are
    checked here — an op targeting a missing or user-anchored claim is rejected, not
    applied. Pure: returns a new :class:`Profile` and an :class:`AppliedReport`;
    persistence is the caller's job (:func:`update_profile_from_entries`).
    """
    today = today or today_str()
    report = AppliedReport()

    valid, reasons = validate_operations(ops if isinstance(ops, list) else [], known_entry_ids)
    for why in reasons:
        report._reject(why)

    # Mutable working copies (Profile is frozen; rebuild at the end).
    identity = dict(profile.identity)
    goals = [dict(g) for g in profile.goals]
    patterns = [dict(p) for p in profile.patterns]
    relationships = [dict(r) for r in profile.relationships]
    baseline = dict(profile.emotional_baseline)
    open_loops = [dict(l) for l in profile.open_loops]
    watch_list = [dict(w) for w in profile.watch_list]
    anchors = list(profile.anchors)

    # Deep-copy the field-keyed provenance dicts so stamping identity/baseline here
    # can't mutate the base profile in place (§7.2 v2; apply_operations stays pure).
    identity["provenance"] = _copy_provenance(identity.get("provenance"))
    baseline["provenance"] = _copy_provenance(baseline.get("provenance"))

    def _cited(op: dict) -> list[str]:
        ev = op.get("evidence")
        return [e for e in ev if e in known_entry_ids] if isinstance(ev, list) else []

    def _is_anchored(claim: dict) -> bool:
        return claim.get("source") == "user" or claim.get("id") in anchors

    def _find(claim_id: str, *lists: list[dict]) -> dict | None:
        for lst in lists:
            for claim in lst:
                if claim.get("id") == claim_id:
                    return claim
        return None

    def _find_by_text(text: str, claims: list[dict]) -> dict | None:
        """An existing claim with the same normalised text (the dedupe guard).

        The prompt tells the model never to restate an existing claim, but a slip —
        within one batch, or across the overlapping nightly/weekly passes — would
        otherwise append a literal duplicate. Matching on normalised text lets an
        ``add_*`` fold into the existing claim (as a strengthen) instead.
        """
        norm = str(text).strip().lower()
        return next(
            (c for c in claims if str(c.get("text", "")).strip().lower() == norm), None
        )

    for op in valid:
        verb = op["op"]

        if verb == "add_goal":
            dup = _find_by_text(op["text"], goals)
            if dup is not None and _is_anchored(dup):
                report._reject(f"add_goal: {op['text']!r} matches a user-anchored goal")
            elif dup is not None:
                # Deterministic merge: fold the restated goal into the existing claim.
                dup["confidence"] = _clamp(_conf(dup) + STRENGTHEN_DELTA)
                dup["evidence"] = _merge_evidence(dup, _cited(op))
                dup["last_seen"] = today
                report.strengthened += 1
            else:
                goals.append({
                    "id": f"g-{uuid.uuid4()}",
                    "text": str(op["text"]).strip(),
                    "status": "active",
                    "confidence": NEW_CLAIM_CONFIDENCE,
                    "last_seen": today,
                    "evidence": _cited(op),
                    "source": "model",
                })
                report.added += 1

        elif verb == "add_pattern":
            ptype = str(op["type"]).strip().lower()
            if ptype not in PATTERN_TYPES:
                report._reject(f"add_pattern: invalid type {op['type']!r}")
                continue
            dup = _find_by_text(op["text"], patterns)
            if dup is not None and _is_anchored(dup):
                report._reject(f"add_pattern: {op['text']!r} matches a user-anchored pattern")
            elif dup is not None:
                dup["confidence"] = _clamp(_conf(dup) + STRENGTHEN_DELTA)
                dup["evidence"] = _merge_evidence(dup, _cited(op))
                dup["last_seen"] = today
                report.strengthened += 1
            else:
                patterns.append({
                    "id": f"p-{uuid.uuid4()}",
                    "text": str(op["text"]).strip(),
                    "type": ptype,
                    "confidence": NEW_CLAIM_CONFIDENCE,
                    "last_seen": today,
                    "evidence": _cited(op),
                    "source": "model",
                })
                report.added += 1

        elif verb == "strengthen":
            claim = _find(op["claim_id"], goals, patterns)
            if claim is None:
                report._reject(f"strengthen: unknown claim {op['claim_id']!r}")
            elif _is_anchored(claim):
                report._reject(f"strengthen: claim {op['claim_id']!r} is a user anchor")
            else:
                claim["confidence"] = _clamp(_conf(claim) + STRENGTHEN_DELTA)
                claim["evidence"] = _merge_evidence(claim, _cited(op))
                claim["last_seen"] = today
                report.strengthened += 1

        elif verb == "weaken":
            claim = _find(op["claim_id"], goals, patterns)
            if claim is None:
                report._reject(f"weaken: unknown claim {op['claim_id']!r}")
            elif _is_anchored(claim):
                report._reject(f"weaken: claim {op['claim_id']!r} is a user anchor")
            else:
                claim["confidence"] = _clamp(_conf(claim) - WEAKEN_DELTA)
                # Record the contradicting entry as *counter*-evidence (kept separate
                # from supporting `evidence` so a rebuild never mistakes it for
                # support) plus the reason. This keeps the weaken durably grounded, so
                # the edit self-heal path (capture._flag_claims_for_revalidation) can
                # re-audit this claim if that entry is later edited.
                claim["counter_evidence"] = _merge_evidence(claim, _cited(op), key="counter_evidence")
                claim["weaken_reason"] = str(op["reason"]).strip()
                if claim["confidence"] < REVIEW_THRESHOLD:
                    claim["needs_review"] = True
                report.weakened += 1

        elif verb == "update_goal_status":
            status = str(op["status"]).strip()
            if status not in GOAL_STATUSES:
                report._reject(f"update_goal_status: invalid status {status!r}")
                continue
            goal = _find(op["goal_id"], goals)
            if goal is None:
                report._reject(f"update_goal_status: unknown goal {op['goal_id']!r}")
            elif _is_anchored(goal):
                report._reject(f"update_goal_status: goal {op['goal_id']!r} is a user anchor")
            else:
                goal["status"] = status
                goal["evidence"] = _merge_evidence(goal, _cited(op))
                goal["last_seen"] = today
                report.status_changed += 1

        elif verb == "note_contradiction":
            # Both ids must resolve to real claims — a fabricated or stale id must
            # never become a visible watch-list item. §7.2: pattern-vs-goal tension.
            pattern = _find(op["claim_id_a"], patterns)
            goal = _find(op["claim_id_b"], goals)
            if pattern is None:
                report._reject(f"note_contradiction: unknown pattern {op['claim_id_a']!r}")
            elif goal is None:
                report._reject(f"note_contradiction: unknown goal {op['claim_id_b']!r}")
            else:
                watch_list.append({
                    "pattern_id": pattern["id"],
                    "conflicting_goal_id": goal["id"],
                    "description": str(op.get("description") or "").strip(),
                    "evidence": _cited(op),
                })
                report.contradictions += 1

        elif verb == "mark_resolved":
            loop = _find(op["loop_id"], open_loops)
            if loop is None:
                report._reject(f"mark_resolved: unknown loop {op['loop_id']!r}")
            else:
                loop["status"] = "resolved"
                loop["last_updated"] = today
                loop["evidence"] = _merge_evidence(loop, _cited(op))
                report.resolved += 1

        elif verb == "update_loop":
            loop = _find(op["loop_id"], open_loops)
            if loop is None:
                report._reject(f"update_loop: unknown loop {op['loop_id']!r}")
            else:
                notes = list(loop.get("notes") or [])
                notes.append(str(op["note"]).strip())
                loop["notes"] = notes
                loop["status"] = "updated"
                loop["last_updated"] = today
                loop["evidence"] = _merge_evidence(loop, _cited(op))
                report.updated_loops += 1

        elif verb == "add_relationship_note":
            name = str(op["name"]).strip()
            rel = next((r for r in relationships
                        if str(r.get("name") or "").strip().lower() == name.lower()), None)
            if rel is None:
                report._reject(f"add_relationship_note: unknown person {name!r}")
            else:
                note = str(op["note"]).strip()
                summary = str(rel.get("summary") or "").strip()
                rel["summary"] = f"{summary} • {note}" if summary else note
                rel["evidence"] = _merge_evidence(rel, _cited(op))
                rel["last_seen"] = today
                report.relationship_notes += 1

        elif verb == "set_identity":
            # Scalar field: a new stated_self replaces the old (each carries its own
            # evidence). Anchored (user-corrected) identity is never overwritten.
            if profile_mod.is_field_anchored(profile, "identity.stated_self"):
                report._reject("set_identity: stated_self is a user anchor")
            else:
                identity["stated_self"] = str(op["text"]).strip()
                _stamp_provenance(identity, "stated_self", _cited(op), today)
                report.identity_set += 1

        elif verb == "add_principle":
            if profile_mod.is_field_anchored(profile, "identity.principles"):
                report._reject("add_principle: principles is a user anchor")
            else:
                text = str(op["text"]).strip()
                principles = list(identity.get("principles") or [])
                if any(text.lower() == str(p).strip().lower() for p in principles):
                    report._reject(f"add_principle: {text!r} already recorded")
                else:
                    principles.append(text)
                    identity["principles"] = principles
                    _stamp_provenance(identity, "principles", _cited(op), today, merge=True)
                    report.principles_added += 1

        elif verb == "add_baseline_item":
            fieldname = str(op["field"]).strip()
            if fieldname not in BASELINE_ITEM_FIELDS:
                # typical_mood is code-owned; only the two list fields are appliable.
                report._reject(f"add_baseline_item: invalid field {op['field']!r}")
                continue
            if profile_mod.is_field_anchored(profile, f"baseline.{fieldname}"):
                report._reject(f"add_baseline_item: {fieldname} is a user anchor")
            else:
                text = str(op["text"]).strip()
                items = list(baseline.get(fieldname) or [])
                if any(text.lower() == str(i).strip().lower() for i in items):
                    report._reject(f"add_baseline_item: {text!r} already in {fieldname}")
                else:
                    items.append(text)
                    baseline[fieldname] = items
                    _stamp_provenance(baseline, fieldname, _cited(op), today, merge=True)
                    report.baseline_items_added += 1

    updated = Profile(
        schema_version=profile.schema_version,
        identity=identity,
        goals=goals,
        patterns=patterns,
        relationships=relationships,
        emotional_baseline=baseline,
        open_loops=open_loops,
        watch_list=watch_list,
        anchors=anchors,
    )
    return updated, report


# ── decay: nightly confidence fade (§7.3) ─────────────────────────────────────
def apply_decay(profile: Profile, *, today: str | None = None) -> Profile:
    """Fade the confidence of non-anchor goals & patterns by their staleness.

    Nightly rule (§7.3): ``confidence -= 0.01 × days_since_last_seen``; a claim that
    falls below 0.5 and has gone ≥ 60 days without corroboration is flagged
    ``stale``. Anchors (user corrections) never decay. Pure — returns a new
    :class:`Profile`; the caller persists.

    **Idempotent within a day.** Each claim records ``decayed_through`` (the last
    date decay was applied). Decay only charges for days *after* the later of
    ``last_seen`` and ``decayed_through``, so re-running rebuild/update on the same
    day never double-decays a claim, and running across days never compounds.
    """
    today = today or today_str()
    today_date = _parse_date(today)

    def _decayed(claims: list[dict]) -> list[dict]:
        out = []
        for claim in claims:
            c = dict(claim)
            if c.get("source") == "user" or c.get("id") in profile.anchors:
                out.append(c)
                continue
            # Charge decay only for days not already applied (idempotency).
            marks = [d for d in (_parse_date(c.get("last_seen")),
                                 _parse_date(c.get("decayed_through"))) if d is not None]
            since = max(marks) if marks else None
            days = (today_date - since).days if (since and today_date) else 0
            if days > 0:
                c["confidence"] = _clamp(_conf(c) - DECAY_PER_DAY * days)
                c["decayed_through"] = today
                if c["confidence"] < STALE_CONFIDENCE and _days_between(c.get("last_seen"), today_date) >= STALE_DAYS:
                    c["stale"] = True
            out.append(c)
        return out

    return Profile(
        schema_version=profile.schema_version,
        identity=profile.identity,
        goals=_decayed(profile.goals),
        patterns=_decayed(profile.patterns),
        relationships=profile.relationships,
        emotional_baseline=profile.emotional_baseline,
        open_loops=profile.open_loops,
        watch_list=profile.watch_list,
        anchors=profile.anchors,
    )


# ── model-backed generation (thin; R8 expands the miners that feed it) ─────────
def render_prompt(entries: list[dict], profile: Profile) -> str:
    """Fill the operation prompt with recent entries and the current profile."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    entries_block = "\n".join(
        f'- entry_id: {e.get("entry_id")}\n'
        f'  date: {e.get("date", "")}\n'
        f'  summary: {e.get("summary", "")}\n'
        f'  themes: {", ".join(e.get("themes") or [])}'
        for e in entries
    ) or "(no entries)"
    profile_block = json.dumps(_compact_profile(profile), indent=2, ensure_ascii=False)
    return template.replace(_ENTRIES_PLACEHOLDER, entries_block).replace(
        _PROFILE_PLACEHOLDER, profile_block
    )


def extract_json_array(text: str) -> list:
    """Return the first balanced ``[...]`` array in ``text`` as a list.

    Tolerant of stray prose or code fences around the JSON (a small model sometimes
    adds them) by scanning for the first bracket-balanced span. Raises ``ValueError``
    if no parseable array is found. Mirrors :func:`memory.extract.extract_json_object`.
    """
    start = text.find("[")
    if start == -1:
        raise ValueError("no JSON array found in model output")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                parsed = json.loads(text[start : i + 1])
                if not isinstance(parsed, list):
                    raise ValueError("parsed JSON is not an array")
                return parsed
    raise ValueError("unbalanced JSON brackets in model output")


async def _llama_server_call(prompt: str, *, temperature: float, max_tokens: int) -> str:
    """Default ModelCaller: reach the model through the shared :mod:`llm.client`.

    Profile updates are a *background* job, so they go through ``complete_chat``
    with ``priority=False`` — a real-time chat turn always takes the model lock
    ahead of them. Same routing and decode discipline as extraction.
    """
    from llm import client

    return await client.complete_chat(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temp=temperature,
        top_p=None,
        top_k=None,
        priority=False,
        stop=["<end_of_turn>", "<eos>"],
    )


async def generate_operations(
    entries: list[dict],
    profile: Profile,
    *,
    call_model: ModelCaller | None = None,
) -> list[dict] | None:
    """Ask the model for a batch of §7.3 operations over ``entries``.

    Runs the model once, retrying once at 0.3 if the output does not parse as a JSON
    array. Returns the raw (unvalidated) operation dicts — the evidence gate lives in
    :func:`validate_operations`/:func:`apply_operations`. Never raises. The empty case
    is disambiguated so a caller can tell "nothing to change" from "couldn't run":

    * ``[]`` — the model ran and legitimately proposed no change (or ``entries`` was
      empty). These entries are considered processed.
    * ``None`` — the model call or parse failed on *both* attempts. No update this
      cycle; the caller should NOT treat the entries as processed (so a consolidation
      run can retry them next time rather than dropping their L3 contribution).
    """
    if not entries:
        return []
    call = call_model or _llama_server_call
    prompt = render_prompt(entries, profile)

    for attempt, temp in enumerate((GENERATE_TEMPERATURE, RETRY_TEMPERATURE), start=1):
        try:
            raw = await call(prompt, temperature=temp, max_tokens=MAX_TOKENS)
        except Exception as e:  # noqa: BLE001 — a failed attempt, never fatal
            log.warning("operation generation attempt %d model call failed: %s", attempt, e)
            continue
        try:
            ops = extract_json_array(raw)
        except ValueError as e:
            log.warning("operation generation attempt %d parse failed: %s", attempt, e)
            continue
        return [op for op in ops if isinstance(op, dict)]

    log.error("operation generation failed twice; no operations this cycle")
    return None


async def update_profile_from_entries(
    entries: list[dict],
    *,
    call_model: ModelCaller | None = None,
    today: str | None = None,
) -> tuple[Profile, AppliedReport]:
    """Generate → validate → apply → decay → refresh mood → persist. The seam R8 calls.

    Reads the current profile (bootstrapping an empty one if none exists yet, so a
    young profile grows from zero), asks the model for operations over ``entries``,
    applies the surviving ones, runs nightly decay, refreshes the code-derived
    ``typical_mood`` from the *full* L1 mood history (so it doesn't go stale between
    full rebuilds), and saves through :func:`profile.save_profile`. Returns the saved
    profile and the apply report.
    """
    today = today or today_str()
    base = profile_mod.get_profile() or Profile()
    ops = await generate_operations(entries, base, call_model=call_model)
    generation_ok = ops is not None       # None == both model/parse attempts failed
    known = {str(e.get("entry_id")) for e in entries if e.get("entry_id")}
    applied, report = apply_operations(base, ops, known_entry_ids=known, today=today)
    report.generation_ok = generation_ok
    decayed = apply_decay(applied, today=today)
    decayed = apply_typical_mood(decayed, _load_mood_history())
    profile_mod.save_profile(decayed)
    log.info(
        "profile update: +%d added, %d strengthened, %d weakened, %d rejected",
        report.added, report.strengthened, report.weakened, report.rejected,
    )
    return decayed, report


# ── typical_mood: code-derived, never model-authored (R7.5) ───────────────────
def derive_typical_mood(
    mood_points: list[dict], *, evidence_cap: int = 20
) -> tuple[int | None, list[str]]:
    """The code-owned ``emotional_baseline.typical_mood``: rounded all-time mean.

    The model may never compute this (EVA_MEMORY_ARCHITECTURE §7.2/§7.3, R7.5); code
    counts. ``mood_points`` are entry records carrying ``mood`` (int or ``None`` when
    extraction failed) and ``entry_id`` — sourced from ``extractions.mood``, the
    canonical mood column that the ``mood_series`` chart table is itself a
    denormalised copy of, so the L3 baseline and the mood chart never drift.
    Averages every non-null mood, rounds to the −5…+5 scale, and returns that plus
    the contributing entry uids (the most-recent ``evidence_cap`` kept, so the
    provenance array stays bounded). Returns ``(None, [])`` when no entry carries a
    mood — the baseline then simply has no typical_mood, mirroring the chart skipping
    null points rather than substituting 0.
    """
    valued = [
        (str(p.get("entry_id")), p.get("mood"))
        for p in mood_points
        if isinstance(p.get("mood"), int) and not isinstance(p.get("mood"), bool)
        and p.get("entry_id")
    ]
    if not valued:
        return None, []
    mean = sum(m for _, m in valued) / len(valued)
    rounded = int(max(-5, min(5, round(mean))))
    return rounded, [eid for eid, _ in valued][-evidence_cap:]


def apply_typical_mood(profile: Profile, mood_points: list[dict]) -> Profile:
    """Write the code-derived ``typical_mood`` into a profile's baseline (R7.5).

    The single place both write paths refresh typical_mood, so it never goes stale
    between rebuilds: the full rebuild (:mod:`memory.rebuild_profile`) and the
    incremental update seam (:func:`update_profile_from_entries`) both call this.
    ``mood_points`` must be the *full* mood history (typical_mood is an all-time
    mean, §7.2). A user-anchored typical_mood is left exactly as the user set it.
    Pure — returns a new :class:`Profile`.
    """
    if profile_mod.is_field_anchored(profile, "baseline.typical_mood"):
        return profile
    mood_val, mood_ev = derive_typical_mood(mood_points)
    baseline = dict(profile.emotional_baseline)
    prov = dict(baseline.get("provenance") or {})
    if mood_val is None:
        baseline.pop("typical_mood", None)
        prov.pop("typical_mood", None)
    else:
        baseline["typical_mood"] = mood_val
        prov["typical_mood"] = {"source": "code", "evidence": mood_ev}
    baseline["provenance"] = prov
    return replace(profile, emotional_baseline=baseline)


def _load_mood_history() -> list[dict]:
    """The full real mood history from L1 (``extractions.mood``) — the mood source.

    Lazily imported (like :func:`_llama_server_call`) so :mod:`memory.operations`
    keeps a thin import surface. Returns ``[]`` if the store can't be read, so a
    profile update never fails just because mood history is unavailable.
    """
    try:
        from . import db

        conn = db.get_or_create_db()
        try:
            return db.mood_history(conn)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a missing/unreadable store must not block updates
        log.warning("could not load mood history for typical_mood: %s", e)
        return []


# ── small helpers ─────────────────────────────────────────────────────────────
def _copy_provenance(prov) -> dict:
    """A deep-enough copy of a field-keyed provenance dict (§7.2 v2)."""
    if not isinstance(prov, dict):
        return {}
    return {k: dict(v) if isinstance(v, dict) else v for k, v in prov.items()}


def _stamp_provenance(
    section: dict, field: str, cited: list[str], today: str, *, merge: bool = False
) -> None:
    """Write/refresh a singleton field's provenance entry (§7.2 v2, ``source=model``).

    ``merge`` unions the cited evidence with any existing (for list fields that
    append — principles, triggers, what_helps); otherwise the cited evidence
    replaces it (for the scalar ``stated_self``). ``last_seen`` is stamped to today.
    """
    prov = section.setdefault("provenance", {})
    entry = dict(prov.get(field)) if isinstance(prov.get(field), dict) else {}
    if merge:
        existing = entry.get("evidence")
        base = list(existing) if isinstance(existing, list) else []
        for eid in cited:
            if eid not in base:
                base.append(eid)
        entry["evidence"] = base
    else:
        entry["evidence"] = list(cited)
    entry["source"] = "model"
    entry["last_seen"] = today
    prov[field] = entry


def _conf(claim: dict) -> float:
    try:
        return float(claim.get("confidence", NEW_CLAIM_CONFIDENCE))
    except (TypeError, ValueError):
        return NEW_CLAIM_CONFIDENCE


def _clamp(value: float) -> float:
    """Clamp a confidence into [0, 1] and round to kill float drift."""
    return round(max(CONFIDENCE_FLOOR, min(CONFIDENCE_CAP, value)), 4)


def _merge_evidence(claim: dict, new_ids: list[str], *, key: str = "evidence") -> list[str]:
    """Union existing + new uids under ``key``, order-preserving, de-duplicated."""
    existing = claim.get(key)
    out = list(existing) if isinstance(existing, list) else []
    for eid in new_ids:
        if eid not in out:
            out.append(eid)
    return out


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _days_between(last_seen, today_date: date | None) -> int:
    seen = _parse_date(last_seen)
    if seen is None or today_date is None:
        return 0
    return (today_date - seen).days


def _compact_profile(profile: Profile) -> dict:
    """A compact view of the profile for the prompt (ids + text + confidence only).

    Keeps the model's context bounded: it needs each claim's id and current wording
    to emit ``strengthen``/``weaken``/``update_*`` ops, not the full evidence arrays.
    """
    def _claims(items, text_key):
        return [
            {"id": c.get("id"), text_key: c.get(text_key), "confidence": c.get("confidence")}
            for c in items if c.get("id")
        ]

    baseline = profile.emotional_baseline
    return {
        # Surface identity + baseline list fields so the model can see what's already
        # recorded and avoid restating it (R7.5). typical_mood is code-owned and
        # deliberately omitted — the model must not reason about or emit it.
        "identity": {
            "stated_self": profile.identity.get("stated_self", ""),
            "principles": list(profile.identity.get("principles") or []),
        },
        "emotional_baseline": {
            "known_triggers": list(baseline.get("known_triggers") or []),
            "what_helps": list(baseline.get("what_helps") or []),
        },
        "goals": _claims(profile.goals, "text"),
        "patterns": _claims(profile.patterns, "text"),
        "open_loops": [
            {"id": l.get("id"), "description": l.get("description"), "status": l.get("status")}
            for l in profile.open_loops if l.get("id")
        ],
        "relationships": [r.get("name") for r in profile.relationships if r.get("name")],
    }
