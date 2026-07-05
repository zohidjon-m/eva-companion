#!/usr/bin/env python3
"""Rebuild the L3 profile (profile.json) from L1 plus user anchors."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from memory import rebuild_profile  # noqa: E402


def _print_report(report: rebuild_profile.RebuildReport) -> None:
    print(f"entries            : {report.entries}")
    print(f"anchors preserved  : {report.anchors_preserved}")
    print(f"goals              : {report.goals}")
    print(f"patterns           : {report.patterns}")
    print(f"open loops         : {report.open_loops}")
    print(f"watch list         : {report.watch_list}")
    print(f"identity fields    : {report.identity_fields}")
    print(f"baseline fields    : {report.baseline_fields}")
    print(f"operations rejected: {report.rejected}")


async def _amain() -> int:
    try:
        report = await rebuild_profile.rebuild_profile()
    except Exception as exc:  # noqa: BLE001 - CLI should report storage/model failures
        print(f"profile rebuild failed: {exc}", file=sys.stderr)
        return 1

    _print_report(report)
    return 0


def main() -> int:
    """Run the R7 L3 rebuild and print a concise report."""
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
