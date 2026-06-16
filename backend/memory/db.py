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
SCHEMA_USER_VERSION = 1


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
        # `PRAGMA user_version` does not accept bound parameters; the value is our
        # own constant, never user input, so the f-string is safe here.
        conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION};")
        log.info("initialised eva.db schema at user_version=%d", SCHEMA_USER_VERSION)
    conn.commit()


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


def create_pending_extraction(conn: sqlite3.Connection, entry_id: str) -> str:
    """Insert a ``pending`` extractions row for an entry and return its id.

    Called at capture time, in the same breath as ``insert_entry`` — so even if
    extraction never runs (crash, model down), the entry is on record as awaiting
    extraction and a nightly sweep can re-queue it.
    """
    ext_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO extractions (id, entry_id, extraction_status) VALUES (?, ?, 'pending')",
        (ext_id, entry_id),
    )
    conn.commit()
    return ext_id


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
) -> None:
    """Write a successful extraction onto the entry's row (status → ``done``).

    All structured fields are JSON-encoded into their TEXT columns exactly as the
    §7.1 schema documents. ``mood`` stays a real integer (or NULL). Idempotent per
    entry: updates the single row keyed by ``entry_id``.
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
    }
    conn.execute(
        """
        UPDATE extractions SET
            extraction_status='done',
            mood=:mood, emotions=:emotions, entities=:entities, themes=:themes,
            events=:events, stated_goals=:stated_goals, behaviors=:behaviors,
            decisions=:decisions, open_loops=:open_loops,
            self_judgments=:self_judgments, summary=:summary,
            extracted_at=:extracted_at
        WHERE entry_id=:entry_id
        """,
        {**values, "entry_id": entry_id},
    )
    conn.commit()


def mark_null_stored(conn: sqlite3.Connection, entry_id: str) -> None:
    """Mark an extraction as failed-but-stored: status ``null_stored``, fields NULL.

    The save is never lost; only the structure is missing. A nightly sweep
    re-queues these rows (§7.1). The mood chart treats NULL mood as a gap, never 0.
    """
    conn.execute(
        "UPDATE extractions SET extraction_status='null_stored' WHERE entry_id=?",
        (entry_id,),
    )
    conn.commit()


def get_extraction(conn: sqlite3.Connection, entry_id: str) -> sqlite3.Row | None:
    """Return the extractions row for an entry, or ``None``."""
    return conn.execute(
        "SELECT * FROM extractions WHERE entry_id=?", (entry_id,)
    ).fetchone()


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
