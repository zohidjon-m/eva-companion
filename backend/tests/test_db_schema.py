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
        "id", "entry_id", "extraction_status", "source_hash", "mood",
        "emotions", "entities", "themes", "events", "stated_goals",
        "behaviors", "decisions", "open_loops", "self_judgments", "summary",
        "extracted_at", "consolidated",
    ],
    "mood_series": ["id", "entry_id", "date", "mood", "emotions", "is_seeded"],
    "graph_nodes": ["id", "label", "type", "entry_count", "entries", "is_seeded"],
    "graph_edges": [
        "id", "source", "target", "type", "weight", "is_hypothesis",
        "label", "entries", "is_seeded",
    ],
    "digests": [
        "id", "level", "period_start", "period_end", "summary", "stats",
        "created_at",
    ],
    "conversations": ["id", "started_at", "last_at", "title"],
    "chat_turns": ["id", "conversation_id", "role", "text", "created_at"],
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
    assert version == db_mod.SCHEMA_USER_VERSION == 6


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


def test_v1_to_v2_migration_adds_graph_is_seeded(db):
    """An existing v1 db (graph tables without is_seeded) migrates forward cleanly.

    Simulates the pre-Phase-14 schema by dropping the column and resetting the
    version, then re-runs init_db and asserts the column is back and the version
    advanced — the ``CREATE TABLE IF NOT EXISTS`` path alone would never add it.
    """
    db_mod, conn = db
    conn.executescript(
        """
        DROP TABLE graph_edges;
        DROP TABLE graph_nodes;
        CREATE TABLE graph_nodes (
            id TEXT PRIMARY KEY, label TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('theme','person','place','goal','problem','emotion')),
            entry_count INTEGER NOT NULL DEFAULT 0, entries TEXT
        );
        CREATE TABLE graph_edges (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL REFERENCES graph_nodes(id),
            target TEXT NOT NULL REFERENCES graph_nodes(id),
            type TEXT NOT NULL CHECK(type IN ('co_occurrence','temporal','similarity','hypothesis')),
            weight REAL NOT NULL DEFAULT 0.0, is_hypothesis INTEGER NOT NULL DEFAULT 0,
            label TEXT, entries TEXT
        );
        PRAGMA user_version = 1;
        """
    )
    conn.commit()
    assert "is_seeded" not in {r["name"] for r in conn.execute("PRAGMA table_info(graph_nodes)")}

    db_mod.init_db(conn)  # runs the v1 → v2 migration

    for table in ("graph_nodes", "graph_edges"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert "is_seeded" in cols, f"{table}.is_seeded not added by migration"
    # init_db migrates all the way forward to the current version.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_USER_VERSION


def test_v3_to_v4_migration_adds_source_hash_and_unique_entry_index(db):
    """A v3 db gets extraction source hashes and one row per stable entry UID."""
    db_mod, conn = db
    conn.executescript(
        """
        DROP INDEX IF EXISTS idx_extractions_entry_id;
        ALTER TABLE extractions RENAME TO extractions_old;
        CREATE TABLE extractions (
            id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            extraction_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(extraction_status IN ('pending','done','failed','null_stored')),
            mood INTEGER,
            emotions TEXT,
            entities TEXT,
            themes TEXT,
            events TEXT,
            stated_goals TEXT,
            behaviors TEXT,
            decisions TEXT,
            open_loops TEXT,
            self_judgments TEXT,
            summary TEXT,
            extracted_at TEXT
        );
        INSERT INTO extractions (
            id, entry_id, extraction_status, mood, emotions, entities, themes,
            events, stated_goals, behaviors, decisions, open_loops,
            self_judgments, summary, extracted_at
        )
        SELECT
            id, entry_id, extraction_status, mood, emotions, entities, themes,
            events, stated_goals, behaviors, decisions, open_loops,
            self_judgments, summary, extracted_at
        FROM extractions_old;
        DROP TABLE extractions_old;
        PRAGMA user_version = 3;
        """
    )
    conn.commit()
    assert "source_hash" not in {r["name"] for r in conn.execute("PRAGMA table_info(extractions)")}

    db_mod.init_db(conn)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(extractions)")}
    assert "source_hash" in cols
    indexes = {
        r["name"]
        for r in conn.execute("PRAGMA index_list(extractions)")
        if r["unique"]
    }
    assert "idx_extractions_entry_id" in indexes
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_USER_VERSION


def test_v4_to_v5_migration_adds_consolidated_flag(db):
    """A v4 db (extractions without `consolidated`) migrates forward cleanly.

    Rebuilds the table at the v4 shape (rather than DROP COLUMN, which SQLite
    re-parses from the commented DDL) and resets the version, then re-runs init_db.
    """
    db_mod, conn = db
    conn.executescript(
        """
        ALTER TABLE extractions RENAME TO extractions_old;
        CREATE TABLE extractions (
            id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            extraction_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(extraction_status IN ('pending','done','failed','null_stored')),
            source_hash TEXT,
            mood INTEGER, emotions TEXT, entities TEXT, themes TEXT, events TEXT,
            stated_goals TEXT, behaviors TEXT, decisions TEXT, open_loops TEXT,
            self_judgments TEXT, summary TEXT, extracted_at TEXT
        );
        DROP TABLE extractions_old;
        PRAGMA user_version = 4;
        """
    )
    conn.commit()
    assert "consolidated" not in {r["name"] for r in conn.execute("PRAGMA table_info(extractions)")}

    db_mod.init_db(conn)  # runs the v4 → v5 migration

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(extractions)")}
    assert "consolidated" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_USER_VERSION


def test_v5_to_v6_migration_dedupes_mood_series_and_adds_unique_index(db):
    """A v5 db with duplicate mood rows keeps the newest row before indexing."""
    db_mod, conn = db
    conn.execute("DROP INDEX IF EXISTS idx_mood_series_entry_id")
    db_mod.insert_entry(
        conn,
        id="e1",
        date="2026-07-01",
        type="journal",
        text="x",
        word_count=1,
        created_at="2026-07-01T08:00:00",
    )
    conn.execute(
        "INSERT INTO mood_series (id, entry_id, date, mood, emotions, is_seeded) "
        "VALUES ('m1', 'e1', '2026-07-01', -2, '[]', 0)"
    )
    conn.execute(
        "INSERT INTO mood_series (id, entry_id, date, mood, emotions, is_seeded) "
        "VALUES ('m2', 'e1', '2026-07-01', 3, '[]', 0)"
    )
    conn.execute("PRAGMA user_version = 5")
    conn.commit()

    db_mod.init_db(conn)

    rows = conn.execute("SELECT id, mood FROM mood_series WHERE entry_id = 'e1'").fetchall()
    assert [(r["id"], r["mood"]) for r in rows] == [("m2", 3)]
    indexes = {r["name"]: r for r in conn.execute("PRAGMA index_list(mood_series)")}
    assert indexes["idx_mood_series_entry_id"]["unique"] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_USER_VERSION


def test_extractions_entry_id_unique(db):
    """Each stable entry UID has at most one extraction row."""
    _, conn = db
    conn.execute(
        "INSERT INTO entries (id, date, type, text, word_count, created_at) "
        "VALUES ('e1', '2026-06-16', 'chat', 'hi', 1, '2026-06-16T09:00:00')"
    )
    conn.execute(
        "INSERT INTO extractions (id, entry_id, extraction_status) "
        "VALUES ('x1', 'e1', 'pending')"
    )
    conn.commit()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO extractions (id, entry_id, extraction_status) "
            "VALUES ('x2', 'e1', 'pending')"
        )


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


# ── R6: recent_episodes (chronological "lately" read) ────────────────────────


def _add_done_entry(db_mod, conn, *, id, date, created_at, summary, is_seeded=False):
    """Insert one entry with a finalized ('done') extraction carrying ``summary``."""
    db_mod.insert_entry(
        conn, id=id, date=date, type="journal", text="t",
        word_count=1, created_at=created_at, is_seeded=is_seeded,
    )
    db_mod.create_pending_extraction(conn, id)
    db_mod.finalize_extraction(
        conn, id, mood=1, emotions=[], entities=[], themes=["home"], events=[],
        stated_goals=[], behaviors=[], decisions=[], open_loops=[],
        self_judgments=[], summary=summary, extracted_at=created_at,
    )


def test_recent_episodes_newest_first_real_and_done_only(db):
    db_mod, conn = db
    _add_done_entry(db_mod, conn, id="e1", date="2026-06-01",
                    created_at="2026-06-01T00:00:00", summary="first")
    _add_done_entry(db_mod, conn, id="e2", date="2026-06-02",
                    created_at="2026-06-02T00:00:00", summary="second")
    # A seeded row (demo data) is excluded, like recall excludes it.
    _add_done_entry(db_mod, conn, id="seed", date="2026-06-03",
                    created_at="2026-06-03T00:00:00", summary="seeded", is_seeded=True)
    # A pending row has no summary yet and must be excluded.
    db_mod.insert_entry(conn, id="p1", date="2026-06-04", type="journal",
                        text="t", word_count=1, created_at="2026-06-04T00:00:00")
    db_mod.create_pending_extraction(conn, "p1")

    rows = db_mod.recent_episodes(conn, 10)
    assert [r["entry_id"] for r in rows] == ["e2", "e1"]  # newest first
    assert rows[0]["summary"] == "second"

    # The limit is honored.
    assert [r["entry_id"] for r in db_mod.recent_episodes(conn, 1)] == ["e2"]
