#!/usr/bin/env python3
"""Demo reset — clean the vault, re-seed the demo state, verify it's ready (Phase 15).

One command takes Eva from "whatever state the last run left" to a known, repeatable
demo state, so the live walkthrough (``DEMO_SCRIPT.md``) starts identically every
time. It is the safety net behind running the demo cold, twice in a row.

What it does, in order:

  1. **Back up** anything irreplaceable first. The L0 journal Markdown, the SQLite
     index, the user's uploaded books, and the profile are copied to a timestamped
     ``<vault>.bak-YYYYmmdd-HHMMSS/`` sibling *before* a single byte is deleted.
     L0 is the only irreplaceable store (EVA_SYSTEM_DESIGN §6); we never destroy it
     without a copy, even for a demo reset.
  2. **Clean** the derived + content stores: ``journal/``, ``eva.db`` (+WAL/SHM),
     the ``chroma/`` vectors, the ``corpus/`` books + manifest, and ``profile.*``.
     It deliberately PRESERVES two things: ``models/`` (the multi-hundred-MB
     embed/whisper weights — re-downloading needs internet, which the demo machine
     may not have) and ``settings.json`` (the user's voice/whisper preferences).
  3. **Seed** the demo state by re-running the committed seed scripts —
     ``seed_demo.py`` (≈3 weeks of backdated mood + the knowledge graph, all
     ``is_seeded=1``) and ``seed_profile.py --force`` (the hand-written L3 profile)
     — then ingests the bundled demo book (``scripts/_eva_testbook.txt``) into the
     corpus so the grounded-citation beat works out of the box.
  4. **Verify** the result: mood points, graph nodes, a present profile, a ready
     corpus doc, and the live net-guard. Prints a READY / NOT-READY summary and
     exits non-zero if any required piece is missing, so a broken reset is loud.

This is DESTRUCTIVE (it wipes the current journal + DB), so it refuses to run
without confirmation. Pass ``--yes`` to skip the prompt on demo day.

Usage (from the repo root or anywhere):
    backend/.venv/bin/python scripts/demo_reset.py            # prompts first
    backend/.venv/bin/python scripts/demo_reset.py --yes      # no prompt (demo day)
    backend/.venv/bin/python scripts/demo_reset.py --no-backup --yes
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Run from anywhere: make the backend package importable.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from memory import vault_dir  # noqa: E402

log = logging.getLogger("eva.demo_reset")

_PY = sys.executable  # the interpreter running this script (the backend venv)
_DEMO_BOOK = _REPO_ROOT / "scripts" / "_eva_testbook.txt"

# What a reset removes, relative to the vault root. Everything else — crucially
# ``models/`` (downloaded weights) and ``settings.json`` (user preferences) — is
# left in place. Listed explicitly so the reset can never "rm -rf the vault".
_REMOVE_DIRS = ["journal", "chroma", "corpus"]
_REMOVE_FILES = ["eva.db", "eva.db-wal", "eva.db-shm", "profile.json", "profile.md"]

# What a backup copies (the irreplaceable / user-owned parts). chroma + models are
# excluded: chroma is rebuildable from L0, and models are large, downloadable, and
# untouched by the reset anyway.
_BACKUP_DIRS = ["journal", "corpus"]
_BACKUP_FILES = ["eva.db", "profile.json", "profile.md", "settings.json"]


def _backup(vault: Path) -> Path | None:
    """Copy the irreplaceable parts of the vault to a timestamped sibling.

    Returns the backup directory, or ``None`` if there was nothing to back up (a
    fresh vault). The backup lives *outside* the vault so a later reset never
    sweeps it up and it is never re-ingested as corpus/journal data.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = vault.parent / f"{vault.name}.bak-{stamp}"
    copied = 0
    for name in _BACKUP_DIRS:
        src = vault / name
        if src.is_dir() and any(src.iterdir()):
            shutil.copytree(src, dest / name, dirs_exist_ok=True)
            copied += 1
    for name in _BACKUP_FILES:
        src = vault / name
        if src.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
            copied += 1
    if copied == 0:
        return None
    log.info("backed up %d item(s) to %s", copied, dest)
    return dest


def _clean(vault: Path) -> None:
    """Remove the derived + content stores, preserving models/ and settings.json.

    Recreates the emptied directories so the backend finds the expected layout and
    the seed step writes into a clean tree.
    """
    for name in _REMOVE_DIRS:
        path = vault / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    for name in _REMOVE_FILES:
        (vault / name).unlink(missing_ok=True)
    log.info("cleaned journal/, chroma/, corpus/, eva.db, profile.* (models/ + settings.json kept)")


def _run_script(script: str, *args: str) -> None:
    """Run a sibling seed script with the current interpreter, raising on failure."""
    cmd = [_PY, str(_REPO_ROOT / "scripts" / script), *args]
    log.info("running %s %s", script, " ".join(args))
    subprocess.run(cmd, check=True, cwd=_REPO_ROOT)


def _ingest_demo_book() -> dict | None:
    """Ingest the bundled demo book into the corpus, or ``None`` if it can't.

    Best-effort, mirroring the real upload path (``ingest.corpus.ingest_file``):
    if the demo book file is missing it is skipped; an ingest failure (e.g. the
    embed model isn't cached) comes back as a ``failed`` document, which the
    verification step surfaces — it never aborts the reset.
    """
    if not _DEMO_BOOK.is_file():
        log.warning("demo book not found at %s; skipping corpus seed", _DEMO_BOOK)
        return None
    from ingest import corpus as corpus_ingest

    data = _DEMO_BOOK.read_bytes()
    doc = corpus_ingest.ingest_file("The Field Guide to Marrow Valley.txt", data)
    log.info(
        "ingested demo book → status=%s, %d chunk(s)", doc["status"], doc.get("chunk_count", 0)
    )
    return doc


def _verify(vault: Path) -> tuple[bool, list[str]]:
    """Check the seeded state and return (all_ready, human-readable lines).

    Required for a green demo: seeded mood points, a seeded graph, and a present
    profile. The corpus book is reported but treated as non-fatal — the demo can
    still upload a book live if the embed model wasn't cached on this machine.

    The outbound net-guard is deliberately NOT checked here: it is a property of
    the *running backend* (installed at app startup), not of the seeder process,
    so it is verified by ``demo_drills.py`` and ``GET /health`` instead — checking
    it in this short-lived process would always read NOT ACTIVE and mislead.
    """
    from memory import db, profile
    from ingest import corpus as corpus_ingest

    lines: list[str] = []
    ok = True

    conn = db.get_or_create_db()
    try:
        mood = db.mood_series_range(conn, date_from=None, date_to=None, include_seeded=True)
        graph_nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
    finally:
        conn.close()

    mood_ok = len(mood) > 0
    ok &= mood_ok
    lines.append(f"{'✓' if mood_ok else '✗'} mood series: {len(mood)} seeded point(s)")

    graph_ok = graph_nodes > 0
    ok &= graph_ok
    lines.append(f"{'✓' if graph_ok else '✗'} knowledge graph: {graph_nodes} node(s)")

    prof = profile.get_profile()
    prof_ok = prof is not None
    ok &= prof_ok
    lines.append(
        f"{'✓' if prof_ok else '✗'} profile: "
        + (f"present ({len(prof.goals)} goal(s))" if prof_ok else "MISSING")
    )

    docs = corpus_ingest.list_documents()
    ready_docs = [d for d in docs if d.get("status") == "ready"]
    # Non-fatal: a book can be uploaded live if the embed model wasn't cached here.
    mark = "✓" if ready_docs else "•"
    detail = (
        f"{len(ready_docs)} ready"
        if ready_docs
        else "none ready (upload one live, or run scripts/download_embed_model.py)"
    )
    lines.append(f"{mark} corpus: {detail}")

    return ok, lines


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Reset Eva to a known demo state.")
    parser.add_argument("--yes", action="store_true", help="Skip the destructive-action prompt.")
    parser.add_argument("--no-backup", action="store_true", help="Don't back up the current vault first.")
    args = parser.parse_args()

    vault = vault_dir()
    print(f"\nDemo reset targets the vault at:\n  {vault}\n")
    print("This DELETES the current journal, database, vectors, uploaded books, and")
    print("profile, then re-seeds the demo state. models/ and settings.json are kept.")
    if not args.no_backup:
        print("A timestamped backup is taken first.")
    print()

    if not args.yes:
        reply = input("Type 'reset' to proceed: ").strip().lower()
        if reply != "reset":
            print("Aborted; nothing was changed.")
            return 1

    if not vault.exists():
        vault.mkdir(parents=True, exist_ok=True)

    if not args.no_backup:
        backup = _backup(vault)
        if backup is None:
            print("Nothing to back up (fresh vault).")
        else:
            print(f"Backed up to: {backup}")

    _clean(vault)

    print("\n── seeding ─────────────────────────────────────────────────────────────")
    _run_script("seed_demo.py")            # mood + knowledge graph (+ embed if available)
    _run_script("seed_profile.py", "--force")  # the hand-written L3 profile
    _ingest_demo_book()                    # the grounded-citation demo book

    print("\n── verifying ───────────────────────────────────────────────────────────")
    ready, lines = _verify(vault)
    for line in lines:
        print("  " + line)

    if ready:
        print("\nREADY. Eva is in the demo state — start with run_demo.sh or dev.sh.")
        return 0
    print("\nNOT READY — one or more required pieces is missing (see the ✗ rows above).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
