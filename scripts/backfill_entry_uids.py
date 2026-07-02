#!/usr/bin/env python3
"""Backfill legacy journal Markdown so entry UIDs live in V2 headers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from memory import vault  # noqa: E402


def main() -> int:
    """Run the idempotent L0 UID-header migration and print a short report."""
    report = vault.backfill_entry_uids()
    print(
        "checked {checked} file(s), changed {files} file(s), "
        "backfilled {entries} entr{suffix}".format(
            checked=report.files_checked,
            files=report.files_changed,
            entries=report.entries_changed,
            suffix="y" if report.entries_changed == 1 else "ies",
        )
    )
    if report.errors:
        print("conflicts:")
        for err in report.errors:
            print(f"- {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
