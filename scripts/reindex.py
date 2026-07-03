#!/usr/bin/env python3
"""Rebuild the L2 ChromaDB vectors from L1 SQLite and stored corpus files."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from memory import reindex  # noqa: E402


def _print_report(report: reindex.ReindexReport) -> None:
    print(f"entries scanned  : {report.entries_scanned}")
    print(f"journals embedded: {report.journals_embedded}")
    print(f"episode units    : {report.episode_units_embedded}")
    print(f"corpus docs      : {report.corpus_docs}")
    print(f"corpus chunks    : {report.corpus_chunks}")
    print(f"corpus failed    : {report.corpus_failed}")


async def _amain() -> int:
    try:
        report = await reindex.reindex_all()
    except Exception as exc:  # noqa: BLE001 - CLI should report storage/embed failures
        print(f"reindex failed: {exc}", file=sys.stderr)
        return 1

    _print_report(report)
    return 0


def main() -> int:
    """Run the R4 L2 rebuild and print a concise report."""
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
