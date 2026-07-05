"""R7 rebuild path: replay L3 (the profile) from L1 plus user anchors.

Like :mod:`memory.reextract` rebuilds L1 from L0, this rebuilds L3 from L1. The
user model is reconstructed by replaying the §7.3 operation grammar over every
real entry, so a corrupted or stale ``profile.json`` can always be regenerated
from the durable layers below it.

Two invariants make the rebuild safe:

  * **User anchors survive byte-for-byte.** Any claim the user corrected
    (``source == "user"`` / id in ``anchors``), plus the identity, emotional
    baseline, and relationships that the operation grammar does not author, are
    carried forward from the existing profile untouched. A rebuild can never weaken
    or drop a human correction.
  * **Model claims are re-derived, not trusted.** Non-anchored goals, patterns,
    open loops, and the whole watch-list are cleared and rebuilt from L1 evidence,
    so a rebuild reflects the entries as they stand now.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import db, operations
from . import profile as profile_mod
from .operations import ModelCaller
from .profile import Profile

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
    rejected: int


def _is_anchor(claim: dict, anchors: list[str]) -> bool:
    return claim.get("source") == "user" or claim.get("id") in anchors


def _preserved_base(base: Profile) -> Profile:
    """Strip model-authored claims, keep everything a rebuild must never lose.

    Identity, emotional baseline, and relationships are not authored by the §7.3
    grammar, so they are preserved verbatim. Anchored goals/patterns/loops are kept;
    non-anchored ones (and the whole watch-list) are cleared for re-derivation.
    """
    anchors = list(base.anchors)
    return Profile(
        schema_version=base.schema_version,
        identity=base.identity,
        goals=[g for g in base.goals if _is_anchor(g, anchors)],
        patterns=[p for p in base.patterns if _is_anchor(p, anchors)],
        relationships=base.relationships,
        emotional_baseline=base.emotional_baseline,
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

    decayed = operations.apply_decay(acc, today=today)
    profile_mod.save_profile(decayed)

    log.info(
        "profile rebuild: %d entries in %d batch(es), %d anchors preserved, "
        "%d goals, %d patterns, %d rejected",
        len(entries), -(-len(entries) // operations.BATCH_SIZE) if entries else 0,
        anchors_preserved, len(decayed.goals), len(decayed.patterns), rejected,
    )
    return RebuildReport(
        entries=len(entries),
        anchors_preserved=anchors_preserved,
        goals=len(decayed.goals),
        patterns=len(decayed.patterns),
        open_loops=len(decayed.open_loops),
        watch_list=len(decayed.watch_list),
        rejected=rejected,
    )
