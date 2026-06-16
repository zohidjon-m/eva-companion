"""Phase 12 — scripts/seed_demo.py: backdated demo data, marked and re-runnable.

The seed must (a) write is_seeded=1 rows the demo chart can show, (b) be safe to
re-run on a vault that already holds real entries — only ever touching seed rows —
and (c) include the one NULL-mood entry that proves the chart draws a gap. The
embedding step is skipped here (--no-embed) so the test needs no model.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

from memory import db

_SEED_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"


def _load_seed_module():
    """Import scripts/seed_demo.py as a module (it isn't on the package path)."""
    spec = importlib.util.spec_from_file_location("seed_demo", _SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def seed(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    monkeypatch.setattr(sys, "argv", ["seed_demo.py", "--no-embed"])
    return _load_seed_module()


def _counts(conn):
    return {
        "entries": conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
        "seeded_entries": conn.execute("SELECT COUNT(*) FROM entries WHERE is_seeded=1").fetchone()[0],
        "seeded_moods": conn.execute("SELECT COUNT(*) FROM mood_series WHERE is_seeded=1").fetchone()[0],
        "seeded_extractions": conn.execute(
            "SELECT COUNT(*) FROM extractions x JOIN entries e ON e.id=x.entry_id WHERE e.is_seeded=1"
        ).fetchone()[0],
    }


def test_seed_writes_marked_rows(seed):
    assert seed.main() == 0

    conn = db.connect()
    c = _counts(conn)
    n = len(seed.SEED_DAYS)
    assert c["seeded_entries"] == n
    assert c["seeded_moods"] == n
    assert c["seeded_extractions"] == n
    # The deliberate NULL-mood entry is present (the chart's gap, never a zero).
    null_moods = conn.execute(
        "SELECT COUNT(*) FROM mood_series WHERE is_seeded=1 AND mood IS NULL"
    ).fetchone()[0]
    assert null_moods == 1
    conn.close()


def test_reseed_is_idempotent(seed):
    seed.main()
    seed.main()  # second run must not double the seed

    conn = db.connect()
    assert _counts(conn)["seeded_entries"] == len(seed.SEED_DAYS)
    conn.close()


def test_seed_never_touches_real_entries(seed):
    # A real (is_seeded=0) entry placed before seeding must survive a re-seed.
    conn = db.get_or_create_db()
    real_id = str(uuid.uuid4())
    db.insert_entry(
        conn, id=real_id, date="2026-06-15", type="journal", text="my real day",
        word_count=3, created_at="2026-06-15T10:00:00", is_seeded=False,
    )
    conn.close()

    seed.main()
    seed.main()

    conn = db.connect()
    assert db.get_entry(conn, real_id) is not None
    assert _counts(conn)["entries"] == len(seed.SEED_DAYS) + 1
    conn.close()
