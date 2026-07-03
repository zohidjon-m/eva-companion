"""L1 SQLite index — connection, schema application, and the L0 entry index.

The database is a *derived* store: every row here can be rebuilt from the L0
Markdown vault, so the file at ``local_vault/eva.db`` is safe to delete and
regenerate. This module owns three things and nothing more in Phase 2 Step A:

1. Locating and opening that database (loopback-only app; a plain local file).
2. Applying ``schema.sql`` exactly as specified in EVA_MEMORY_ARCHITECTURE §7.1
   — every table, column, and constraint, with no simplification.
3. The thin read/write helpers for the ``entries`` table (the L0 index), which
   Step B's capture wiring will call once per saved turn.

The extraction pipeline, ChromaDB embedding, and the `/chat` wiring are Step B
and deliberately absent here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path

from . import vault_dir

log = logging.getLogger("eva.memory.db")

DB_FILENAME = "eva.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Bump this and add a migration block whenever schema.sql changes. The schema
# file keeps the matching `PRAGMA user_version` as a comment; db.py is the
# component that actually owns and writes the version (per §7.1's migration note).
#
# v2 (Phase 14): added `is_seeded` to graph_nodes/graph_edges so a seeded demo
# graph can be pruned later, mirroring the flag already on entries/mood_series.
# v3 (chat history): added `conversations` + `chat_turns` so the Chat screen can
# persist and reopen full conversations (both sides), separate from the journal.
# v4 (R2 / Phase 3.5): added `extractions.source_hash` and a unique extraction
# row per stable entry UID.
SCHEMA_USER_VERSION = 4


def db_path() -> Path:
    """Return the absolute path to the SQLite file inside the vault."""
    return vault_dir() / DB_FILENAME


def connect() -> sqlite3.Connection:
    """Open (creating the parent vault dir if needed) and return a connection.

    Enables ``foreign_keys`` on every connection — SQLite defaults it OFF, and
    the schema relies on ``ON DELETE CASCADE`` from ``extractions``/``mood_series``
    back to ``entries``. Rows come back as ``sqlite3.Row`` so callers can use
    column names. The caller owns the connection's lifetime.
    """
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply ``schema.sql`` and stamp the schema version. Idempotent.

    Every statement in the schema uses ``IF NOT EXISTS``, so running this on an
    already-initialised database is a no-op. The ``user_version`` is only written
    on a fresh database (version 0) — once migrations exist they, not this
    function, advance it. This is the single place the §7.1 schema is created;
    it is never improvised or modified elsewhere.
    """
    schema_sql = SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)

    current = conn.execute("PRAGMA user_version;").fetchone()[0]
    if current == 0:
        # Fresh database: ``schema.sql`` already created every table at the current
        # shape, so just stamp the version. `PRAGMA user_version` does not accept
        # bound parameters; the value is our own constant, never user input, so the
        # f-string is safe here.
        conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION};")
        log.info("initialised eva.db schema at user_version=%d", SCHEMA_USER_VERSION)
    elif current < SCHEMA_USER_VERSION:
        _migrate(conn, current)
    conn.commit()


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    """Bring an existing database forward to ``SCHEMA_USER_VERSION``.

    ``CREATE TABLE IF NOT EXISTS`` in ``schema.sql`` is a no-op once a table
    exists, so a *column* added to an existing table never appears without an
    explicit migration. Each step is idempotent (it checks before it alters) and
    advances ``user_version`` so a re-run resumes where it left off.
    """
    if from_version < 2:
        # v1 → v2: the is_seeded flag on the L4 graph tables (Phase 14).
        _add_column_if_missing(conn, "graph_nodes", "is_seeded", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "graph_edges", "is_seeded", "INTEGER NOT NULL DEFAULT 0")
        log.info("migrated eva.db schema v1 → v2 (graph is_seeded)")
    if from_version < 3:
        # v2 → v3: chat conversation transcripts. The new tables are created by the
        # `CREATE TABLE IF NOT EXISTS` in schema.sql that init_db() runs *before*
        # this migration, so there is nothing to ALTER here — just record the bump.
        log.info("migrated eva.db schema v2 → v3 (conversations + chat_turns)")
    if from_version < 4:
        _add_column_if_missing(conn, "extractions", "source_hash", "TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_extractions_entry_id "
            "ON extractions(entry_id)"
        )
        log.info("migrated eva.db schema v3 → v4 (source_hash + extraction uid index)")
    conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION};")


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, decl: str
) -> None:
    """``ALTER TABLE … ADD COLUMN`` only if ``column`` isn't already present.

    Table/column/decl are our own constants (never user input), so interpolating
    them into the DDL is safe; SQLite does not accept bound parameters for DDL.
    """
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def get_or_create_db() -> sqlite3.Connection:
    """Convenience: open a connection and ensure the schema is applied."""
    conn = connect()
    init_db(conn)
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# entries — the L0 index. These mirror, never replace, the Markdown vault.
# Step B calls insert_entry() once per saved turn (alongside the vault write);
# extraction rows and embeddings are layered on in Step B.
# ─────────────────────────────────────────────────────────────────────────────
def insert_entry(
    conn: sqlite3.Connection,
    *,
    id: str,
    date: str,
    type: str,
    text: str,
    word_count: int,
    created_at: str,
    is_seeded: bool = False,
) -> None:
    """Insert one row into the ``entries`` index.

    Field names and types match ``schema.sql`` exactly. ``type`` must be ``chat``
    or ``journal`` (enforced by the table's CHECK constraint). This indexes an
    entry that has *already* been written to the L0 vault — the Markdown remains
    the source of truth.
    """
    conn.execute(
        """
        INSERT INTO entries (id, date, type, text, word_count, is_seeded, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (id, date, type, text, word_count, 1 if is_seeded else 0, created_at),
    )
    rowid = conn.execute("SELECT rowid FROM entries WHERE id = ?", (id,)).fetchone()[0]
    conn.execute("INSERT INTO entries_fts(rowid, text) VALUES (?, ?)", (rowid, text))
    conn.commit()


def upsert_entry_from_l0(
    conn: sqlite3.Connection,
    *,
    id: str,
    date: str,
    type: str,
    text: str,
    word_count: int,
    created_at: str,
    is_seeded: bool = False,
) -> str:
    """Insert or refresh one ``entries`` row from an L0 Markdown turn.

    R3 rebuilds SQLite from Markdown without minting new identities. This helper
    keeps ``entries.id`` equal to the UID parsed from L0 and reports whether the
    row was inserted, updated, or already unchanged so the rebuild script can
    produce an honest summary.
    """
    seeded = 1 if is_seeded else 0
    existing = get_entry(conn, id)
    if existing is None:
        conn.execute(
            """
            INSERT INTO entries (id, date, type, text, word_count, is_seeded, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (id, date, type, text, word_count, seeded, created_at),
        )
        conn.commit()
        return "inserted"

    changed = (
        existing["date"] != date
        or existing["type"] != type
        or existing["text"] != text
        or existing["word_count"] != word_count
        or existing["is_seeded"] != seeded
        or existing["created_at"] != created_at
    )
    if not changed:
        return "unchanged"

    conn.execute(
        """
        UPDATE entries
        SET date = ?, type = ?, text = ?, word_count = ?, is_seeded = ?, created_at = ?
        WHERE id = ?
        """,
        (date, type, text, word_count, seeded, created_at, id),
    )
    conn.commit()
    return "updated"


def prune_entries_not_in(conn: sqlite3.Connection, entry_ids: set[str]) -> int:
    """Delete derived ``entries`` rows that no longer exist in L0 Markdown.

    The Markdown vault is the source of truth. During R3, stale DB-only rows must
    be removed so deleting and rebuilding ``eva.db`` yields the same entry count
    as the vault. Foreign keys cascade the dependent extraction/mood rows.
    """
    if not entry_ids:
        count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        conn.execute("DELETE FROM entries")
        conn.commit()
        return count

    placeholders = ",".join("?" for _ in entry_ids)
    count = conn.execute(
        f"SELECT COUNT(*) FROM entries WHERE id NOT IN ({placeholders})",
        tuple(entry_ids),
    ).fetchone()[0]
    conn.execute(
        f"DELETE FROM entries WHERE id NOT IN ({placeholders})",
        tuple(entry_ids),
    )
    conn.commit()
    return count


def rebuild_entries_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the external-content FTS table from the current ``entries`` rows.

    ``entries_fts`` has no triggers in the current schema, so R3 must explicitly
    refresh it after upserting/pruning entries.
    """
    conn.execute("INSERT INTO entries_fts(entries_fts) VALUES ('rebuild')")
    conn.commit()


def refresh_entry_fts(
    conn: sqlite3.Connection, entry_id: str, *, previous_text: str | None = None
) -> bool:
    """Refresh the FTS row for one entry after an L0 edit.

    R5 recomputes exactly one UID, so a full ``entries_fts`` rebuild would hide
    mistakes and waste work. ``previous_text`` is passed when the row already had
    indexed terms; FTS5 needs that old body to remove stale tokens from the
    external-content index before inserting the current text.
    """
    row = conn.execute(
        "SELECT rowid, text FROM entries WHERE id = ?", (entry_id,)
    ).fetchone()
    if row is None:
        return False

    if previous_text is not None:
        try:
            conn.execute("SAVEPOINT fts_refresh")
            conn.execute(
                "INSERT INTO entries_fts(entries_fts, rowid, text) VALUES ('delete', ?, ?)",
                (row["rowid"], previous_text),
            )
            conn.execute("RELEASE fts_refresh")
        except sqlite3.DatabaseError:
            # Some older rows were never inserted into FTS. FTS5 reports that as
            # "database disk image is malformed" for the delete command; rolling
            # back the savepoint leaves the index intact, then the insert below
            # starts indexing the row from now on.
            conn.execute("ROLLBACK TO fts_refresh")
            conn.execute("RELEASE fts_refresh")
    conn.execute(
        "INSERT INTO entries_fts(rowid, text) VALUES (?, ?)",
        (row["rowid"], row["text"]),
    )
    conn.commit()
    return True


def update_entry_text(
    conn: sqlite3.Connection, entry_id: str, *, text: str, word_count: int
) -> None:
    """Update an entry's indexed text + word_count after the L0 Markdown was edited.

    Keeps the L1 index in step with the source of truth when a past entry is
    rewritten. A no-op (zero rows) if the id isn't indexed (e.g. a hand-placed
    entry), which is harmless — the Markdown is still the truth.
    """
    previous = get_entry(conn, entry_id)
    conn.execute(
        "UPDATE entries SET text = ?, word_count = ? WHERE id = ?",
        (text, word_count, entry_id),
    )
    if previous is not None:
        refresh_entry_fts(conn, entry_id, previous_text=previous["text"])
    conn.commit()


def get_entry(conn: sqlite3.Connection, entry_id: str) -> sqlite3.Row | None:
    """Return the ``entries`` row for ``entry_id``, or ``None`` if absent."""
    cur = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    return cur.fetchone()


def count_entries(conn: sqlite3.Connection) -> int:
    """Return the number of rows in ``entries`` (used by tests and diagnostics)."""
    return conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]


def list_journal_days(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return one row per day that has journal entries, newest day first.

    Powers Phase 5's browseable past-entries list. Each row carries the ``date``,
    the ``count`` of journal turns that day, and a ``preview`` (the text of the
    most recent journal turn that day, for a one-line teaser). Only ``journal``
    entries are counted — chat turns live on the same day files but are not part
    of the journal browse.
    """
    return conn.execute(
        """
        SELECT
            e1.date AS date,
            COUNT(*) AS count,
            (
                SELECT e2.text FROM entries e2
                WHERE e2.date = e1.date AND e2.type = 'journal'
                ORDER BY e2.created_at DESC, e2.rowid DESC
                LIMIT 1
            ) AS preview
        FROM entries e1
        WHERE e1.type = 'journal'
        GROUP BY e1.date
        ORDER BY e1.date DESC
        """
    ).fetchall()


def list_journal_entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return one row per *individual* journal entry, newest first.

    Unlike :func:`list_journal_days`, this does not group by day — each saved
    journal entry is its own post, so the journal surface can show a flat,
    scrollable history (grid or list) and open any single post on its own. Each
    row carries the entry's ``id``, ``date``, ``created_at``, full ``text`` (the
    caller trims it to a preview) and ``word_count``. Only ``journal`` entries are
    included; chat turns share the same day files but are not part of the journal.
    """
    return conn.execute(
        """
        SELECT id, date, created_at, text, word_count
        FROM entries
        WHERE type = 'journal'
        ORDER BY created_at DESC, rowid DESC
        """
    ).fetchall()


# ─────────────────────────────────────────────────────────────────────────────
# extractions — one L1 row per entry. Created 'pending' at capture time, then
# finalised to 'done' (with all fields) or 'null_stored' (NULL fields) by the
# background extraction. The row is NEVER blocked on the model: a failed
# extraction still leaves a durable 'null_stored' row, per §7.1's retry contract.
# ─────────────────────────────────────────────────────────────────────────────
# JSON-encoded columns on the extractions table (stored as TEXT).
_JSON_FIELDS = (
    "emotions", "entities", "themes", "events", "stated_goals",
    "behaviors", "decisions", "open_loops", "self_judgments",
)


def create_pending_extraction(
    conn: sqlite3.Connection, entry_id: str, *, source_hash: str | None = None
) -> str:
    """Insert a ``pending`` extractions row for an entry and return its id.

    Called at capture time, in the same breath as ``insert_entry`` — so even if
    extraction never runs (crash, model down), the entry is on record as awaiting
    extraction and a nightly sweep can re-queue it. ``source_hash`` records the
    exact L0 body this pending row represents, enabling later dirty checks.
    """
    ext_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO extractions (id, entry_id, extraction_status, source_hash)
        VALUES (?, ?, 'pending', ?)
        """,
        (ext_id, entry_id, source_hash),
    )
    conn.commit()
    return ext_id


def ensure_pending_extraction(
    conn: sqlite3.Connection, entry_id: str, *, source_hash: str | None = None
) -> bool:
    """Ensure an entry has one extraction row before R3 processing.

    Capture normally creates the row synchronously, but a database rebuilt from
    Markdown may not have it yet. Returns ``True`` when a new pending row was
    created and ``False`` when an extraction row already existed.
    """
    if get_extraction(conn, entry_id) is not None:
        return False
    create_pending_extraction(conn, entry_id, source_hash=source_hash)
    return True


def mark_extraction_pending(
    conn: sqlite3.Connection, entry_id: str, *, source_hash: str | None = None
) -> None:
    """Set one extraction row to ``pending`` for a synchronous recompute.

    If the row does not exist (for example a hand-placed Markdown entry being
    edited before an R3 rebuild), it is created. Existing structured fields are
    cleared so a crash during recompute cannot leave stale derived facts looking
    current for the new ``source_hash``.
    """
    if get_extraction(conn, entry_id) is None:
        create_pending_extraction(conn, entry_id, source_hash=source_hash)
        return
    conn.execute(
        """
        UPDATE extractions
        SET extraction_status='pending',
            source_hash=?,
            mood=NULL,
            emotions=NULL,
            entities=NULL,
            themes=NULL,
            events=NULL,
            stated_goals=NULL,
            behaviors=NULL,
            decisions=NULL,
            open_loops=NULL,
            self_judgments=NULL,
            summary=NULL,
            extracted_at=NULL
        WHERE entry_id=?
        """,
        (source_hash, entry_id),
    )
    conn.commit()


def finalize_extraction(
    conn: sqlite3.Connection,
    entry_id: str,
    *,
    mood: int | None,
    emotions: list,
    entities: list,
    themes: list,
    events: list,
    stated_goals: list,
    behaviors: list,
    decisions: list,
    open_loops: list,
    self_judgments: list,
    summary: str,
    extracted_at: str,
    source_hash: str | None = None,
) -> None:
    """Write a successful extraction onto the entry's row (status → ``done``).

    All structured fields are JSON-encoded into their TEXT columns exactly as the
    §7.1 schema documents. ``mood`` stays a real integer (or NULL). Idempotent per
    entry: updates the single row keyed by ``entry_id``. ``source_hash`` pins the
    result to the exact canonical L0 body that was extracted.
    """
    values = {
        "mood": mood,
        "emotions": json.dumps(emotions, ensure_ascii=False),
        "entities": json.dumps(entities, ensure_ascii=False),
        "themes": json.dumps(themes, ensure_ascii=False),
        "events": json.dumps(events, ensure_ascii=False),
        "stated_goals": json.dumps(stated_goals, ensure_ascii=False),
        "behaviors": json.dumps(behaviors, ensure_ascii=False),
        "decisions": json.dumps(decisions, ensure_ascii=False),
        "open_loops": json.dumps(open_loops, ensure_ascii=False),
        "self_judgments": json.dumps(self_judgments, ensure_ascii=False),
        "summary": summary,
        "extracted_at": extracted_at,
        "source_hash": source_hash,
    }
    conn.execute(
        """
        UPDATE extractions SET
            extraction_status='done',
            mood=:mood, emotions=:emotions, entities=:entities, themes=:themes,
            events=:events, stated_goals=:stated_goals, behaviors=:behaviors,
            decisions=:decisions, open_loops=:open_loops,
            self_judgments=:self_judgments, summary=:summary,
            extracted_at=:extracted_at,
            source_hash=COALESCE(:source_hash, source_hash)
        WHERE entry_id=:entry_id
        """,
        {**values, "entry_id": entry_id},
    )
    conn.commit()


def mark_null_stored(
    conn: sqlite3.Connection, entry_id: str, *, source_hash: str | None = None
) -> None:
    """Mark an extraction as failed-but-stored: status ``null_stored``, fields NULL.

    The save is never lost; only the structure is missing. A nightly sweep
    re-queues these rows (§7.1). The mood chart treats NULL mood as a gap, never 0.
    """
    conn.execute(
        """
        UPDATE extractions
        SET extraction_status='null_stored',
            mood=NULL,
            emotions=NULL,
            entities=NULL,
            themes=NULL,
            events=NULL,
            stated_goals=NULL,
            behaviors=NULL,
            decisions=NULL,
            open_loops=NULL,
            self_judgments=NULL,
            summary=NULL,
            extracted_at=NULL,
            source_hash=COALESCE(?, source_hash)
        WHERE entry_id=?
        """,
        (source_hash, entry_id),
    )
    conn.commit()


def get_extraction(conn: sqlite3.Connection, entry_id: str) -> sqlite3.Row | None:
    """Return the extractions row for an entry, or ``None``."""
    return conn.execute(
        "SELECT * FROM extractions WHERE entry_id=?", (entry_id,)
    ).fetchone()


def current_extraction_status(
    conn: sqlite3.Connection, entry_id: str, source_hash: str
) -> str | None:
    """Return ``done``/``null_stored`` when an extraction already matches L0.

    R2 uses this to skip model and embedding work for unchanged entries while
    still reprocessing pending rows or rows whose stored hash differs from the
    canonical body about to be extracted.
    """
    row = get_extraction(conn, entry_id)
    if row is None:
        return None
    if row["source_hash"] != source_hash:
        return None
    status = row["extraction_status"]
    return status if status in {"done", "null_stored"} else None


def mood_series_range(
    conn: sqlite3.Connection,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    include_seeded: bool = False,
) -> list[sqlite3.Row]:
    """Return mood points for the chart, oldest first, joined to their summary.

    This is the read side of the Phase-12 mood chart (``GET /insights/mood``):
    pure SQL over the denormalised ``mood_series`` table, no LLM. Each row carries
    the entry's ``date``, its ``mood`` (an integer, or NULL — which the chart
    renders as a *gap*, never as zero, per §7.1), the ``emotions`` JSON copied at
    extraction time, ``is_seeded``, and the ``summary`` from the L1 ``extractions``
    row so a hovered point can show that day's reflection.

    Filtering follows §7.1's recall rule, applied here to the chart: live data is
    ``is_seeded = 0`` only; ``include_seeded=True`` (the demo chart) lifts that
    filter so the backdated seed month is shown alongside any real entries. The
    optional ``date_from`` / ``date_to`` bounds (inclusive, ``YYYY-MM-DD``) back
    the 7-/30-day toggle. Ordering is by day, then by the entry's ``created_at`` so
    multiple turns on one day plot left-to-right in the order they were written.

    The join to ``extractions`` is a LEFT join: a mood point always comes from a
    successful extraction (that is the only path that writes ``mood_series``), but
    keeping it left-outer means a future seeding path that writes a mood row
    without an extraction still charts, just without a hover summary.
    """
    clauses: list[str] = []
    params: list = []
    if not include_seeded:
        clauses.append("ms.is_seeded = 0")
    if date_from is not None:
        clauses.append("ms.date >= ?")
        params.append(date_from)
    if date_to is not None:
        clauses.append("ms.date <= ?")
        params.append(date_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    return conn.execute(
        f"""
        SELECT
            ms.entry_id   AS entry_id,
            ms.date       AS date,
            ms.mood       AS mood,
            ms.emotions   AS emotions,
            ms.is_seeded  AS is_seeded,
            x.summary     AS summary,
            e.created_at  AS created_at
        FROM mood_series ms
        JOIN entries e ON e.id = ms.entry_id
        LEFT JOIN extractions x ON x.entry_id = ms.entry_id
        {where}
        ORDER BY ms.date ASC, e.created_at ASC, ms.rowid ASC
        """,
        params,
    ).fetchall()


def upsert_mood_series(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    date: str,
    mood: int | None,
    emotions: list,
    is_seeded: bool = False,
) -> None:
    """Copy an entry's mood/emotions into the denormalised ``mood_series`` table.

    Populated here, at extraction time, so the Phase-12 mood chart has data from
    day one (plan rule 6 — capture stays real even while later features are stubbed).
    This is the one Step-B write not spelled out in the Phase-2 bullet list; it is
    included deliberately because §7.1 and Phase 12 both specify that extraction —
    not a later phase — populates this table. No LLM is involved.
    """
    conn.execute(
        """
        INSERT INTO mood_series (id, entry_id, date, mood, emotions, is_seeded)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()), entry_id, date, mood,
            json.dumps(emotions, ensure_ascii=False), 1 if is_seeded else 0,
        ),
    )
    conn.commit()


def delete_mood_series(conn: sqlite3.Connection, entry_id: str) -> int:
    """Remove derived mood points for one entry and return the row count.

    R3 calls this when an extraction fails so stale mood data cannot survive a
    changed source entry.
    """
    cur = conn.execute("DELETE FROM mood_series WHERE entry_id = ?", (entry_id,))
    conn.commit()
    return cur.rowcount


def replace_mood_series(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    date: str,
    mood: int | None,
    emotions: list,
    is_seeded: bool = False,
) -> None:
    """Replace the derived mood point for one entry idempotently.

    Re-extraction can revisit the same entry many times. Replacing rather than
    appending keeps ``mood_series`` a rebuildable view over the latest L1 row.
    """
    conn.execute("DELETE FROM mood_series WHERE entry_id = ?", (entry_id,))
    conn.execute(
        """
        INSERT INTO mood_series (id, entry_id, date, mood, emotions, is_seeded)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()), entry_id, date, mood,
            json.dumps(emotions, ensure_ascii=False), 1 if is_seeded else 0,
        ),
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# L4 knowledge graph — read/write for graph_nodes & graph_edges (§7.1 / §7.4).
#
# The Phase-14 demo writes a *seeded* graph (is_seeded=1) via scripts/seed_demo.py
# and reads it back through GET /insights/graph. The real L4 builder will write
# is_seeded=0 rows through the same helpers; the read defaults to live-only
# (is_seeded=0), with include_seeded=True lifting that for the demo — exactly the
# recall rule the mood chart already follows.
# ─────────────────────────────────────────────────────────────────────────────
def insert_graph_node(
    conn: sqlite3.Connection,
    *,
    id: str,
    label: str,
    type: str,
    entry_count: int,
    entries: list[str],
    is_seeded: bool = False,
) -> None:
    """Insert one ``graph_nodes`` row. ``type`` must be one of the §7.4 node enum.

    ``entries`` (the evidence entry-ids behind the node) is JSON-encoded into the
    TEXT column exactly as §7.1 documents. The CHECK constraint enforces the enum.
    """
    conn.execute(
        """
        INSERT INTO graph_nodes (id, label, type, entry_count, entries, is_seeded)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (id, label, type, entry_count, json.dumps(entries, ensure_ascii=False), 1 if is_seeded else 0),
    )


def insert_graph_edge(
    conn: sqlite3.Connection,
    *,
    id: str,
    source: str,
    target: str,
    type: str,
    weight: float,
    is_hypothesis: bool,
    label: str | None,
    entries: list[str],
    is_seeded: bool = False,
) -> None:
    """Insert one ``graph_edges`` row. ``type`` must be one of the §7.4 edge enum.

    A hypothesis edge carries ``is_hypothesis=True`` and a human-readable ``label``
    (e.g. "may lead to"); ordinary edges leave ``label`` ``None``. Both are
    enforced by the §7.4 contract, not the DB, so the graph builder is responsible
    for keeping ``type == 'hypothesis'`` aligned with ``is_hypothesis``.
    """
    conn.execute(
        """
        INSERT INTO graph_edges
            (id, source, target, type, weight, is_hypothesis, label, entries, is_seeded)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            id, source, target, type, weight, 1 if is_hypothesis else 0,
            label, json.dumps(entries, ensure_ascii=False), 1 if is_seeded else 0,
        ),
    )


def clear_seeded_graph(conn: sqlite3.Connection) -> tuple[int, int]:
    """Delete every ``is_seeded=1`` node and edge; return (nodes, edges) removed.

    Edges are deleted first so the ``graph_edges → graph_nodes`` foreign key never
    blocks a node delete. Real graph rows (``is_seeded=0``) are never touched, so a
    re-seed is clean and the future L4 output would survive a demo re-seed.
    """
    n_edges = conn.execute("SELECT COUNT(*) FROM graph_edges WHERE is_seeded=1").fetchone()[0]
    n_nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes WHERE is_seeded=1").fetchone()[0]
    conn.execute("DELETE FROM graph_edges WHERE is_seeded=1")
    conn.execute("DELETE FROM graph_nodes WHERE is_seeded=1")
    conn.commit()
    return n_nodes, n_edges


def graph_nodes_all(
    conn: sqlite3.Connection, *, include_seeded: bool = False
) -> list[sqlite3.Row]:
    """Return graph nodes; live-only by default, all rows when ``include_seeded``."""
    where = "" if include_seeded else "WHERE is_seeded = 0"
    return conn.execute(f"SELECT * FROM graph_nodes {where} ORDER BY entry_count DESC, label ASC").fetchall()


def graph_edges_all(
    conn: sqlite3.Connection, *, include_seeded: bool = False
) -> list[sqlite3.Row]:
    """Return graph edges; live-only by default, all rows when ``include_seeded``."""
    where = "" if include_seeded else "WHERE is_seeded = 0"
    return conn.execute(f"SELECT * FROM graph_edges {where} ORDER BY weight DESC").fetchall()


def seeded_extractions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every seeded entry joined to its done extraction (graph build input).

    The Phase-14 graph builder reads this: each row carries the entry's ``id``,
    ``date``, ``text``, and the extraction's ``themes``/``emotions``/``entities``
    JSON, so the seeded graph is derived from the *same* data the mood chart uses
    — honest co-occurrence, not invented links.
    """
    return conn.execute(
        """
        SELECT
            e.id        AS entry_id,
            e.date      AS date,
            e.text      AS text,
            x.themes    AS themes,
            x.emotions  AS emotions,
            x.entities  AS entities,
            x.summary   AS summary
        FROM entries e
        JOIN extractions x ON x.entry_id = e.id
        WHERE e.is_seeded = 1 AND x.extraction_status = 'done'
        ORDER BY e.date ASC
        """
    ).fetchall()


def real_extractions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Like :func:`seeded_extractions` but for the user's REAL entries (is_seeded=0).

    The input to the live graph build (:func:`memory.graph.store_graph`): each row
    carries the entry id/date/text plus the extraction's themes/emotions/entities,
    so the real graph is derived from the same data the mood chart shows.
    """
    return conn.execute(
        """
        SELECT
            e.id        AS entry_id,
            e.date      AS date,
            e.text      AS text,
            x.themes    AS themes,
            x.emotions  AS emotions,
            x.entities  AS entities,
            x.summary   AS summary
        FROM entries e
        JOIN extractions x ON x.entry_id = e.id
        WHERE e.is_seeded = 0 AND x.extraction_status = 'done'
        ORDER BY e.date ASC
        """
    ).fetchall()


def recent_episodes(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Return the newest ``limit`` real (non-seeded) done extractions, newest first.

    The R6 read loop assembles these as the chronological "recent L1 episodes"
    context — the "what's been on their mind lately" baseline (EVA_SYSTEM_DESIGN
    §7.1), distinct from the relevance-ranked recall in :func:`retrieval.recall_memories`.
    Each row carries what the context block needs: ``entry_id``, ``date``, ``mood``,
    ``themes`` (JSON), and the 4–5 sentence ``summary``. Only ``done`` extractions
    are returned (a ``pending``/``null_stored`` row has no summary to show), and
    seeded demo rows are excluded so recency is built from the user's real entries,
    exactly as recall excludes them. Ordered by capture time so "lately" means
    lately, not by day-file date alone.
    """
    return conn.execute(
        """
        SELECT
            e.id        AS entry_id,
            e.date      AS date,
            x.mood      AS mood,
            x.themes    AS themes,
            x.summary   AS summary
        FROM entries e
        JOIN extractions x ON x.entry_id = e.id
        WHERE e.is_seeded = 0 AND x.extraction_status = 'done'
        ORDER BY e.created_at DESC, e.rowid DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def all_done_extractions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every done extraction (seeded AND real) for the R4 vector rebuild.

    ``scripts/reindex.py`` re-embeds L2 from this: each row carries the fields the
    ``journals`` summary vector and the ``episodes`` unit vectors are built from —
    ``date``, ``mood``, ``themes``, ``summary``, ``open_loops``, ``events`` — plus
    ``is_seeded`` so demo rows are re-embedded with the seed flag set and stay out
    of live recall exactly as they were before the rebuild.
    """
    return conn.execute(
        """
        SELECT
            e.id          AS entry_id,
            e.date        AS date,
            e.is_seeded   AS is_seeded,
            x.mood        AS mood,
            x.themes      AS themes,
            x.summary     AS summary,
            x.open_loops  AS open_loops,
            x.events      AS events
        FROM entries e
        JOIN extractions x ON x.entry_id = e.id
        WHERE x.extraction_status = 'done'
        ORDER BY e.date ASC
        """
    ).fetchall()
