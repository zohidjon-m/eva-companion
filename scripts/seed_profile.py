#!/usr/bin/env python3
"""Seed the hand-written demo profile (Phase 13) into the vault.

``local_vault/`` is gitignored (it is the user's private data), so this script is
the committed, reproducible *source* of the hand-authored demo profile — the same
role ``scripts/seed_demo.py`` plays for the mood chart. Running it writes:

  * ``<vault>/profile.json`` — the structured L3 truth, conforming EXACTLY to
    EVA_MEMORY_ARCHITECTURE §7.2 (same fields, same types, same structure the real
    L3 engine will write).
  * ``<vault>/profile.md``   — the human-readable rendering, generated from the
    JSON by ``memory.profile`` so the two never drift.

The content is shaped to match the seeded mood month (``seed_demo.py``): a man
working on discipline and faith (fajr), a fitness habit, a friendship with Daniel
that had a rough patch, recurring work anxiety. That alignment is what makes the
demo land — e.g. "should I skip the gym today?" meets a stated fitness goal and a
watch-list tension about skipping it when tired.

The ``evidence`` arrays hold placeholder entry ids: this is hand-authored demo
data, not the output of the real extraction engine, so the pointers are
illustrative. The real L3 engine fills them with genuine entry ids. # DEMO-STUB

Usage (from anywhere):
    backend/.venv/bin/python scripts/seed_profile.py
    backend/.venv/bin/python scripts/seed_profile.py --force   # overwrite an existing profile
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Run from anywhere: make the backend package importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from memory import profile  # noqa: E402

log = logging.getLogger("eva.seed_profile")


# ─────────────────────────────────────────────────────────────────────────────
# The hand-authored demo profile. Conforms exactly to §7.2. UUIDs are fixed so a
# re-seed is deterministic; evidence ids are illustrative placeholders (see the
# module docstring). last_seen dates sit inside the seeded demo month.
# ─────────────────────────────────────────────────────────────────────────────
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
            "status": "active",
            "confidence": 0.82,
            "last_seen": "2026-06-15",
            "evidence": ["seed-entry-0006", "seed-entry-0010", "seed-entry-0016"],
            "source": "model",
        },
        {
            "id": "g-2c4e6a80-9d12-4f33-8b77-5e6f7a8b9c0d",
            "text": "Train at the gym four times a week",
            "status": "active",
            "confidence": 0.71,
            "last_seen": "2026-06-13",
            "evidence": ["seed-entry-0008", "seed-entry-0013", "seed-entry-0018"],
            "source": "model",
        },
        {
            "id": "g-9b3d5f70-1a23-4c44-9e88-6f7a8b9c0d1e",
            "text": "Be steadier under work pressure instead of spiralling",
            "status": "active",
            "confidence": 0.64,
            "last_seen": "2026-06-12",
            "evidence": ["seed-entry-0004", "seed-entry-0017"],
            "source": "model",
        },
    ],
    "patterns": [
        {
            "id": "p-4f6a8c20-5b34-4d55-8a99-7b8c9d0e1f2a",
            "text": "Avoids difficult conversations when tired",
            "type": "behavior",
            "confidence": 0.74,
            "last_seen": "2026-06-09",
            "evidence": ["seed-entry-0002", "seed-entry-0009"],
            "source": "model",
        },
        {
            "id": "p-8a0c2e40-7d56-4e66-9b00-8c9d0e1f2a3b",
            "text": "Skips workouts and routines first when work stress spikes",
            "type": "behavior",
            "confidence": 0.68,
            "last_seen": "2026-06-12",
            "evidence": ["seed-entry-0004", "seed-entry-0017"],
            "source": "model",
        },
    ],
    "relationships": [
        {
            "name": "Daniel",
            "type": "friend",
            "summary": "Close, but tension flares around communication when one of them is stressed",
            "evidence": ["seed-entry-0002", "seed-entry-0014"],
            "last_seen": "2026-06-10",
        },
        {
            "name": "his mother",
            "type": "family",
            "summary": "Loving but recurring friction about visiting; old scripts resurface",
            "evidence": ["seed-entry-0009"],
            "last_seen": "2026-06-07",
        },
    ],
    "emotional_baseline": {
        "typical_mood": 1,
        "known_triggers": ["fatigue", "conflict", "work deadlines"],
        "what_helps": ["prayer", "exercise", "an early night"],
        "evidence": ["seed-entry-0003", "seed-entry-0008", "seed-entry-0016"],
    },
    "open_loops": [
        {
            "id": "o-1d3f5a70-9b78-4f77-8c11-9d0e1f2a3b4c",
            "description": "Wants to rebuild a steady morning routine after the rough work patch",
            "status": "updated",
            "opened": "2026-06-04",
            "last_updated": "2026-06-13",
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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Seed the hand-written demo profile.")
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing profile (default: refuse if one is present).",
    )
    args = parser.parse_args()

    existing = profile.get_profile()
    if existing is not None and not args.force:
        log.warning(
            "a profile already exists; refusing to overwrite. Re-run with --force to replace it."
        )
        return 1

    saved = profile.save_profile(profile.Profile.from_dict(DEMO_PROFILE))
    log.info(
        "seeded demo profile: %d goal(s), %d pattern(s), %d relationship(s), %d open loop(s)",
        len(saved.goals), len(saved.patterns), len(saved.relationships), len(saved.open_loops),
    )
    print(
        "\nDone. Wrote profile.json + profile.md into the vault. The Profile screen "
        "renders profile.md; Eva pulls relevant slices into chat every turn. Delete "
        "profile.json to see the graceful 'no profile' degrade."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
