#!/usr/bin/env python3
"""Rebuild SQLite L1 from L0 Markdown journal files."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from memory import reextract  # noqa: E402


def _print_report(report: reextract.ReextractReport) -> None:
    print(f"scanned          : {report.scanned}")
    print(f"inserted         : {report.inserted}")
    print(f"updated          : {report.updated}")
    print(f"unchanged        : {report.unchanged}")
    print(f"pruned           : {report.pruned}")
    print(f"extraction rows  : {report.extraction_created} created")
    print(f"skipped          : {report.skipped}")
    print(f"retried          : {report.retried}")
    print(f"done             : {report.done}")
    print(f"null_stored      : {report.null_stored}")
    print(f"fts rebuilt      : {report.fts_rebuilt}")


async def _amain() -> int:
    try:
        report = await reextract.reextract_all()
    except reextract.ReextractError as exc:
        print(f"reextract failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI should report storage/model failures
        print(f"reextract failed unexpectedly: {exc}", file=sys.stderr)
        return 1

    _print_report(report)
    return 0


def main() -> int:
    """Run the R3 L1 rebuild and print a concise report."""
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
