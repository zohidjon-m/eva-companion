"""Chat conversation transcripts — the full back-and-forth for the Chat history UI.

Separate from the journal vault on purpose. The journal (``entries`` +
``extractions``) stores the user's own words so the L1 extractor and memory
recall work on them; Eva's replies never belong there. These two tables
(``conversations`` + ``chat_turns``, see ``schema.sql``) instead keep the raw
conversation — both sides — so the Chat screen can list past conversations and
reopen them. Like the rest of ``eva.db`` this is a *derived*, rebuildable store:
deleting it loses chat history but nothing the journal depends on.

Each function owns its own short-lived connection (mirroring the journal browse
endpoints in ``app.py``), so callers don't thread a connection through. The
writes are tiny and local, well inside the chat turn's latency budget.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from . import db

# A conversation's title is just a teaser of the first user message, so the
# history sidebar reads like a list of openings rather than UUIDs.
_TITLE_MAX = 80


def _now() -> str:
    """Local ISO-8601 timestamp (seconds), matching the rest of eva.db."""
    return datetime.now().isoformat(timespec="seconds")


def _title_from(text: str) -> str:
    """Collapse whitespace and clip the first user message into a short title."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return "New conversation"
    return collapsed[:_TITLE_MAX]


def start_conversation(first_user_text: str) -> str:
    """Create a conversation row titled from the first user message; return its id.

    Called the first time a turn is captured on a socket that isn't already tied
    to a conversation. ``started_at`` and ``last_at`` begin equal; ``last_at`` is
    bumped by :func:`append_turn` on every later turn so the sidebar can sort by
    most-recent activity.
    """
    conv_id = str(uuid.uuid4())
    now = _now()
    conn = db.get_or_create_db()
    try:
        conn.execute(
            "INSERT INTO conversations (id, started_at, last_at, title) VALUES (?, ?, ?, ?)",
            (conv_id, now, now, _title_from(first_user_text)),
        )
        conn.commit()
    finally:
        conn.close()
    return conv_id


def ensure_conversation(conversation_id: str, first_user_text: str) -> None:
    """Create the conversation row for a client-supplied id if it doesn't exist.

    The frontend generates a conversation id for a fresh thread and sends it from
    the very first turn, so the backend never has to echo an id back. This is an
    ``INSERT OR IGNORE``: the first turn creates the row (titled from the message),
    later turns find it already present and only bump ``last_at`` via
    :func:`append_turn`.
    """
    now = _now()
    conn = db.get_or_create_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, started_at, last_at, title) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, now, now, _title_from(first_user_text)),
        )
        conn.commit()
    finally:
        conn.close()


def append_turn(conversation_id: str, role: str, text: str) -> None:
    """Append one turn (``role`` is ``'user'`` or ``'eva'``) and bump ``last_at``.

    The CHECK constraint on ``chat_turns.role`` enforces the two allowed roles.
    Bumping the parent conversation's ``last_at`` in the same transaction keeps the
    sidebar ordering correct without a separate write.
    """
    now = _now()
    conn = db.get_or_create_db()
    try:
        conn.execute(
            "INSERT INTO chat_turns (id, conversation_id, role, text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), conversation_id, role, text, now),
        )
        conn.execute(
            "UPDATE conversations SET last_at = ? WHERE id = ?", (now, conversation_id)
        )
        conn.commit()
    finally:
        conn.close()


def list_conversations() -> list[dict]:
    """Return every conversation, most recently active first, for the sidebar.

    Each item carries ``id``, ``title``, ``started_at``, ``last_at`` and the
    ``turn_count`` (so the UI can show "12 messages"). No turn text is loaded here
    — the rail only needs titles; the transcript is fetched on click.
    """
    conn = db.get_or_create_db()
    try:
        rows = conn.execute(
            """
            SELECT
                c.id          AS id,
                c.title       AS title,
                c.started_at  AS started_at,
                c.last_at     AS last_at,
                (SELECT COUNT(*) FROM chat_turns t WHERE t.conversation_id = c.id)
                              AS turn_count
            FROM conversations c
            ORDER BY c.last_at DESC
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: str) -> dict | None:
    """Return one conversation with its ordered turns, or ``None`` if it's gone.

    Shape: ``{id, title, started_at, last_at, turns: [{role, text, created_at}]}``
    — exactly what the Chat screen needs to rehydrate a thread on click/reload.
    """
    conn = db.get_or_create_db()
    try:
        head = conn.execute(
            "SELECT id, title, started_at, last_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if head is None:
            return None
        turns = conn.execute(
            "SELECT role, text, created_at FROM chat_turns "
            "WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC",
            (conversation_id,),
        ).fetchall()
    finally:
        conn.close()
    result = dict(head)
    result["turns"] = [dict(t) for t in turns]
    return result


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation and its turns (FK cascade). Return whether it existed."""
    conn = db.get_or_create_db()
    try:
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
