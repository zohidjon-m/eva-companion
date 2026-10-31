#!/usr/bin/env python3
"""Run fictional journals through Eva's real local memory pipeline, in stages.

# DEV-FIXTURE: raw text is synthetic; extraction and profile claims are not seeded.

Use a separate vault and stop Eva's scheduler while building the fixture. This
tool never resets a vault or downloads assets. Stage snapshots are local audit
artifacts, not expected model outputs. A failed run can resume with the same
fixture and base date without rewriting its journal entries.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("baseline", "adjustment", "current")
MARKER = "showcase-run.json"


def write_json(path: Path, value: dict) -> None:
    """Atomically save progress so interrupted demo setup can resume safely."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare_vault(vault: Path, fixture: Path, base_date: str) -> tuple[dict, dict]:
    """Claim an empty demo vault, refusing to alter an existing personal vault."""
    raw = fixture.read_bytes()
    data = json.loads(raw)
    if data.get("fictional") is not True:
        raise ValueError("Showcase input must be explicitly labeled fictional.")
    signature = hashlib.sha256(raw).hexdigest()
    marker = vault / MARKER
    if marker.exists():
        state = json.loads(marker.read_text(encoding="utf-8"))
        if state.get("fixture_sha256") != signature or state.get("base_date") != base_date:
            raise ValueError("This vault belongs to a different fixture or base date. Use a new vault.")
    else:
        protected = ("journal", "eva.db", "chroma", "corpus", "profile.json", "profile.md", "settings.json")
        if any((vault / name).exists() for name in protected):
            raise ValueError("Existing Eva data found. Choose an empty showcase vault; nothing was changed.")
        vault.mkdir(parents=True, exist_ok=True)
        state = {"fictional": True, "persona": data["persona"], "fixture_sha256": signature,
                 "base_date": base_date, "entries": {}, "completed_stages": []}
        write_json(marker, state)
    return data, state


async def build_stage(args: argparse.Namespace) -> None:
    """Capture and consolidate one chronological stage with the actual local model."""
    vault_path = args.vault.expanduser().resolve()
    data, state = prepare_vault(vault_path, args.fixture, args.base_date)
    if args.stage in state["completed_stages"]:
        print(f"ALREADY_COMPLETE {args.stage}; existing entries and snapshot preserved.")
        return
    prior = STAGES[:STAGES.index(args.stage)]
    if any(stage not in state["completed_stages"] for stage in prior):
        raise ValueError("Run the preceding stages first: baseline, adjustment, current.")
    os.environ["EVA_VAULT_DIR"] = str(vault_path)
    sys.path.insert(0, str(ROOT / "backend"))
    import settings
    from memory import capture, consolidate, db, graph, profile, vault
    from net_guard import install_net_guard

    if settings.get("ai_provider_id") != "local_llamacpp":
        raise ValueError("The showcase requires the local llama.cpp provider.")
    install_net_guard()
    base = date.fromisoformat(args.base_date)
    marker = vault_path / MARKER
    reports = []
    for entry in (e for e in data["entries"] if e["stage"] == args.stage):
        key = entry["key"]
        when = datetime.combine(base - timedelta(days=entry["days_ago"]), time(20, 30))
        if key not in state["entries"]:
            rec = vault.save_entry(f"### {entry['title']}\n\n{entry['text']}", "journal", when=when)
            state["entries"][key] = asdict(rec)
            write_json(marker, state)
        rec = vault.EntryRecord(**state["entries"][key])
        conn = db.get_or_create_db()
        try:
            if db.get_entry(conn, rec.id) is None:
                db.insert_entry(conn, **asdict(rec))
                db.create_pending_extraction(conn, rec.id, source_hash=vault.source_hash(rec.text))
        finally:
            conn.close()
        print(f"EXTRACT {key} ({rec.date}, {rec.id})", flush=True)
        status = await consolidate.on_save(rec.id, rec.text, rec.date)
        if status != "done":
            raise RuntimeError(f"Extraction failed for {key}: {status}. Fix the model before continuing.")
        report = await consolidate.run_nightly(today=rec.date)
        conn = db.get_or_create_db()
        try:
            row = conn.execute("SELECT consolidated FROM extractions WHERE entry_id = ?", (rec.id,)).fetchone()
            if not row or not row[0]:
                raise RuntimeError(f"Profile operation generation failed for {key}; the entry remains saved.")
        finally:
            conn.close()
        reports.append({"key": key, "entry_id": rec.id, "date": rec.date, "nightly": asdict(report)})
        print(f"CONSOLIDATED {key}: {json.dumps(asdict(report))}", flush=True)
    snapshots = vault_path / "showcase-artifacts"
    snapshots.mkdir(exist_ok=True)
    conn = db.get_or_create_db()
    try:
        graph_data = graph.read_graph(conn)
        counts = dict(conn.execute("SELECT extraction_status, COUNT(*) FROM extractions GROUP BY extraction_status").fetchall())
    finally:
        conn.close()
    snapshot = {"fictional": True, "stage": args.stage, "base_date": args.base_date,
                "profile": profile.profile_payload(), "graph": graph_data,
                "extractions": counts, "reports": reports}
    write_json(snapshots / f"{args.stage}.json", snapshot)
    current_profile = profile.get_profile()
    if current_profile:
        write_json(snapshots / f"{args.stage}-profile.json", current_profile.to_dict())
    if args.stage not in state["completed_stages"]:
        state["completed_stages"].append(args.stage)
    write_json(marker, state)
    print(f"READY {args.stage}: {counts}, {len(graph_data['nodes'])} graph nodes", flush=True)


def main() -> int:
    """Require an explicit demo vault and stage to make fixture writes deliberate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--base-date", default=date.today().isoformat())
    parser.add_argument("--fixture", type=Path, default=ROOT / "scripts/fixtures/showcase_alex.json")
    parser.add_argument("--prepare-only", action="store_true", help="Claim an empty vault before starting the backend.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.prepare_only:
        prepare_vault(args.vault.expanduser().resolve(), args.fixture, args.base_date)
        print("PREPARED fictional showcase vault; no entries or model outputs written.")
        return 0
    asyncio.run(build_stage(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
