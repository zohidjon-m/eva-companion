"""R7 rebuild path: replay L3 (the profile) from L1 plus user anchors.

Like :mod:`memory.reextract` rebuilds L1 from L0, this rebuilds L3 from L1. The
user model is reconstructed by replaying the §7.3 operation grammar over every
real entry, so a corrupted or stale ``profile.json`` can always be regenerated
from the durable layers below it.

Two invariants make the rebuild safe:

  * **User anchors survive byte-for-byte.** Any claim the user corrected
    (``source == "user"`` / id in ``anchors``), plus the relationships the operation
    grammar does not author and any user-anchored identity/baseline *field* (R7.5,
    tracked by its ``section.field`` anchor path), are carried forward untouched. A
    rebuild can never weaken or drop a human correction.
  * **Model claims are re-derived, not trusted.** Non-anchored goals, patterns,
    open loops, the whole watch-list, and the non-anchored identity/emotional-baseline
    fields are cleared and rebuilt from L1 evidence, so a rebuild reflects the entries
    as they stand now. ``typical_mood`` is recomputed deterministically from L1 mood
    history (:func:`operations.apply_typical_mood`, shared with the incremental update
    seam); the model never authors it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import db, operations
from . import profile as profile_mod
from .operations import ModelCaller
from .profile import Profile

# The singleton identity/baseline fields a rebuild re-derives (unless anchored).
_IDENTITY_FIELDS = ("stated_self", "principles")
_BASELINE_FIELDS = ("typical_mood", "known_triggers", "what_helps")

log = logging.getLogger("eva.memory.rebuild_profile")


@dataclass(frozen=True)
class RebuildReport:
    """Counters for one L3 rebuild run."""

    entries: int
    anchors_preserved: int
    goals: int
    patterns: int
    open_loops: int
    watch_list: int
    identity_fields: int
    baseline_fields: int
    rejected: int


def _is_anchor(claim: dict, anchors: list[str]) -> bool:
    return claim.get("source") == "user" or claim.get("id") in anchors


def _preserved_singleton(base: Profile, section: str, source: dict, fields: tuple[str, ...]) -> dict:
    """Keep only user-anchored fields of a singleton section; clear the rest (R7.5).

    Non-anchored identity/baseline fields are dropped so the model re-derives them
    from L1 evidence (closing the R7.5 seam). A field the user corrected — anchored
    via its ``section.field`` path — is carried forward with its provenance intact.
    """
    out: dict = {"provenance": {}}
    src_prov = source.get("provenance") if isinstance(source.get("provenance"), dict) else {}
    for field in fields:
        if profile_mod.is_field_anchored(base, f"{section}.{field}"):
            if field in source:
                out[field] = source[field]
            if field in src_prov:
                out["provenance"][field] = src_prov[field]
    return out


def _preserved_base(base: Profile) -> Profile:
    """Strip model-authored claims, keep everything a rebuild must never lose.

    Relationships are not authored by the §7.3 grammar, so they are preserved
    verbatim. Identity and emotional baseline keep only their *user-anchored* fields
    (R7.5) — the rest is cleared for re-derivation from L1. Anchored
    goals/patterns/loops are kept; non-anchored ones (and the whole watch-list) are
    cleared for re-derivation.
    """
    anchors = list(base.anchors)
    return Profile(
        schema_version=base.schema_version,
        identity=_preserved_singleton(base, "identity", base.identity, _IDENTITY_FIELDS),
        goals=[g for g in base.goals if _is_anchor(g, anchors)],
        patterns=[p for p in base.patterns if _is_anchor(p, anchors)],
        relationships=base.relationships,
        emotional_baseline=_preserved_singleton(base, "baseline", base.emotional_baseline, _BASELINE_FIELDS),
        open_loops=[l for l in base.open_loops if _is_anchor(l, anchors)],
        watch_list=[],
        anchors=anchors,
    )


def _load_entries() -> list[dict]:
    """Read every real (non-seeded) done extraction as an operation input record."""
    conn = db.get_or_create_db()
    try:
        rows = db.real_extractions(conn)
    finally:
        conn.close()
    entries = []
    for row in rows:
        try:
            themes = json.loads(row["themes"]) if row["themes"] else []
        except (json.JSONDecodeError, TypeError):
            themes = []
        entries.append({
            "entry_id": row["entry_id"],
            "date": row["date"],
            "mood": row["mood"],
            "summary": row["summary"] or "",
            "themes": themes if isinstance(themes, list) else [],
        })
    return entries


async def rebuild_profile(
    call_model: ModelCaller | None = None,
    *,
    today: str | None = None,
) -> RebuildReport:
    """Regenerate ``profile.json`` from L1 evidence, preserving user anchors.

    Reads the current profile (for its anchors and non-authored sections), clears
    model-authored claims, replays operations over every real entry in **bounded
    chronological batches** (never the whole history at once — §5 "bounded input,
    bounded output"), decays, then persists through :func:`profile.save_profile`.
    The profile is carried forward across batches so later entries strengthen or
    contradict claims the earlier ones built. Returns a :class:`RebuildReport`.
    """
    today = today or operations.today_str()
    base = profile_mod.get_profile() or Profile()
    preserved = _preserved_base(base)
    anchors_preserved = len(preserved.goals) + len(preserved.patterns) + len(preserved.open_loops)

    entries = _load_entries()  # oldest-first (db.real_extractions orders by date)
    acc = preserved
    rejected = 0
    for start in range(0, len(entries), operations.BATCH_SIZE):
        batch = entries[start:start + operations.BATCH_SIZE]
        ops = await operations.generate_operations(batch, acc, call_model=call_model)
        known = {e["entry_id"] for e in batch}
        acc, report = operations.apply_operations(acc, ops, known_entry_ids=known, today=today)
        rejected += report.rejected

    # typical_mood is code-derived from the full L1 mood history — the model never
    # authors it. Shared with the incremental update seam so the two never diverge.
    acc = operations.apply_typical_mood(acc, entries)
    decayed = operations.apply_decay(acc, today=today)
    profile_mod.save_profile(decayed)

    identity_fields = _count_fields(decayed.identity, _IDENTITY_FIELDS)
    baseline_fields = _count_fields(decayed.emotional_baseline, _BASELINE_FIELDS)
    log.info(
        "profile rebuild: %d entries in %d batch(es), %d anchors preserved, "
        "%d goals, %d patterns, %d identity + %d baseline field(s), %d rejected",
        len(entries), -(-len(entries) // operations.BATCH_SIZE) if entries else 0,
        anchors_preserved, len(decayed.goals), len(decayed.patterns),
        identity_fields, baseline_fields, rejected,
    )
    return RebuildReport(
        entries=len(entries),
        anchors_preserved=anchors_preserved,
        goals=len(decayed.goals),
        patterns=len(decayed.patterns),
        open_loops=len(decayed.open_loops),
        watch_list=len(decayed.watch_list),
        identity_fields=identity_fields,
        baseline_fields=baseline_fields,
        rejected=rejected,
    )


def _count_fields(section: dict, fields: tuple[str, ...]) -> int:
    """Count populated singleton fields (``typical_mood`` of 0 still counts)."""
    return sum(1 for f in fields if section.get(f) not in (None, "", []))
