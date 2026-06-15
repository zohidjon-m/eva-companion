"""Phase 2 Step A — verify db.py applies the §7.1 schema exactly.

These tests assert the database created by ``init_db`` has every table, column,
and constraint from EVA_MEMORY_ARCHITECTURE §7.1 — the schema is locked before
any reasoning is built on top of it, so a drift here is a real regression.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A fresh, schema-applied database rooted in a temp vault dir."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    # Re-import so vault_dir() picks up the patched env at module import time.
    import memory
    import memory.db as db_mod

    importlib.reload(memory)
    importlib.reload(db_mod)
    conn = db_mod.get_or_create_db()
    yield db_mod, conn
    conn.close()


# The full table/column contract from §7.1. If the schema changes, this changes
# with it — deliberately, after a human decides the migration is correct.
EXPECTED_COLUMNS = {
    "entries": ["id", "date", "type", "text", "word_count", "is_seeded", "created_at"],
    "extractions": [
        "id", "entry_id", "extraction_status", "mood", "emotions", "entities",
        "themes", "events", "stated_goals", "behaviors", "decisions",
        "open_loops", "self_judgments", "summary", "extracted_at",
    ],
    "mood_series": ["id", "entry_id", "date", "mood", "emotions", "is_seeded"],
    "graph_nodes": ["id", "label", "type", "entry_count", "entries"],
    "graph_edges": [
        "id", "source", "target", "type", "weight", "is_hypothesis",
        "label", "entries",
    ],
    "digests": [
        "id", "level", "period_start", "period_end", "summary", "stats",
        "created_at",
    ],
}


def test_all_tables_present(db):
    _, conn = db
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for table in EXPECTED_COLUMNS:
        assert table in names, f"missing table {table}"
    # The FTS5 virtual table over entries must exist too.
    assert "entries_fts" in names


@pytest.mark.parametrize("table,cols", EXPECTED_COLUMNS.items())
def test_columns_exact(db, table, cols):
    _, conn = db
    actual = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    assert actual == cols, f"{table} columns drifted from §7.1"


def test_user_version_stamped(db):
    db_mod, conn = db
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == db_mod.SCHEMA_USER_VERSION == 1


def test_entries_type_check_constraint(db):
    """entries.type only accepts 'chat' or 'journal'."""
    db_mod, conn = db
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO entries (id, date, type, text, created_at) "
            "VALUES ('x', '2026-06-16', 'bogus', 't', 'now')"
        )


def test_foreign_key_cascade_on(db):
    """extractions.entry_id cascades when its entry is deleted (FKs are ON)."""
    db_mod, conn = db
    conn.execute(
        "INSERT INTO entries (id, date, type, text, word_count, created_at) "
        "VALUES ('e1', '2026-06-16', 'chat', 'hi', 1, '2026-06-16T09:00:00')"
    )
    conn.execute(
        "INSERT INTO extractions (id, entry_id, extraction_status) "
        "VALUES ('x1', 'e1', 'pending')"
    )
    conn.commit()
    conn.execute("DELETE FROM entries WHERE id='e1'")
    conn.commit()
    remaining = conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0]
    assert remaining == 0, "ON DELETE CASCADE did not fire (foreign_keys off?)"


def test_init_db_idempotent(db):
    """Re-applying the schema on an existing db is a harmless no-op."""
    db_mod, conn = db
    db_mod.init_db(conn)  # second application
    db_mod.init_db(conn)  # third
    # Still exactly the expected tables, nothing duplicated or dropped.
    count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert count >= len(EXPECTED_COLUMNS)
