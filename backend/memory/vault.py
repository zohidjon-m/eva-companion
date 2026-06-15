"""L0 vault — the user's journal as append-only Markdown. The source of truth.

Everything the user writes lands here first, as plain Markdown that is readable
in any text editor without Eva running. One file per day
(``local_vault/journal/YYYY-MM-DD.md``) with a small YAML frontmatter header;
each turn (a chat message or a journal entry) is appended as its own timestamped
section. Nothing in this module reads or depends on SQLite — the database is
derived from these files, never the other way round (CLAUDE.md rule 5).

Append-only is a hard rule: we only ever add to a day file, never rewrite or
reorder it, so a crash or a half-finished write can lose at most the single turn
in flight, never history.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from . import vault_dir

log = logging.getLogger("eva.memory.vault")

# The two kinds of turn Eva captures. Mirrors the CHECK constraint on
# entries.type in schema.sql — kept in sync by hand (one enum, two places).
ENTRY_TYPES = ("chat", "journal")

# Markdown writes are short and synchronous, but a chat turn and a journal save
# can land on the same day file from different threads. Serialise all writes so
# frontmatter is created exactly once and turn sections never interleave.
_write_lock = threading.Lock()


@dataclass(frozen=True)
class EntryRecord:
    """Metadata for one saved turn — the bridge from L0 to the L1 index.

    ``save_entry`` returns this after writing the Markdown. Its fields line up
    one-to-one with the ``entries`` table columns so Step B can hand it straight
    to ``db.insert_entry`` without reshaping. The full ``text`` is carried so the
    index can store it verbatim.
    """

    id: str
    date: str          # YYYY-MM-DD (local day the turn was written)
    type: str          # "chat" | "journal"
    text: str          # the full turn text, exactly as written
    word_count: int
    created_at: str     # ISO-8601 timestamp


def journal_dir() -> Path:
    """Return the directory holding the per-day Markdown files."""
    return vault_dir() / "journal"


def day_file(date: str) -> Path:
    """Return the Markdown file path for a given ``YYYY-MM-DD`` date string."""
    return journal_dir() / f"{date}.md"


def _frontmatter(date: str, created_at: str) -> str:
    """Build the YAML frontmatter block that opens a new day file.

    Describes the *file* (a day's log), not any single turn — turns carry their
    own type and timestamp in their section headers, because one day file can mix
    chat and journal turns.
    """
    header = yaml.safe_dump(
        {"date": date, "kind": "eva-journal-day", "created_at": created_at},
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{header}\n---\n\n# {date}\n"


def _turn_block(rec: EntryRecord, time_str: str) -> str:
    """Render one turn as a Markdown section.

    The entry id is embedded as an HTML comment so it is invisible when the file
    is read as prose but still recoverable by tooling (e.g. a future rebuild of
    the L1 index from L0). The body is the user's text verbatim.
    """
    return (
        f"\n## {time_str} · {rec.type}\n"
        f"<!-- id: {rec.id} -->\n\n"
        f"{rec.text.strip()}\n"
    )


def save_entry(text: str, entry_type: str, *, when: datetime | None = None) -> EntryRecord:
    """Append one turn to today's Markdown day file and return its metadata.

    Args:
        text: the full turn/entry text, stored verbatim.
        entry_type: ``"chat"`` or ``"journal"``.
        when: timestamp to record; defaults to now (local time). Injectable so
            tests are deterministic.

    Returns:
        An :class:`EntryRecord` the caller (Step B) persists to the L1 index.

    Raises:
        ValueError: if ``entry_type`` is not a known type or ``text`` is empty.

    The first write of a day creates the file with frontmatter; subsequent writes
    append only. The whole operation is guarded by a process-wide lock so the
    create-or-append decision is atomic.
    """
    if entry_type not in ENTRY_TYPES:
        raise ValueError(f"entry_type must be one of {ENTRY_TYPES}, got {entry_type!r}")
    if not text or not text.strip():
        raise ValueError("refusing to save an empty entry")

    ts = when or datetime.now()
    date = ts.strftime("%Y-%m-%d")
    created_at = ts.isoformat(timespec="seconds")
    rec = EntryRecord(
        id=str(uuid.uuid4()),
        date=date,
        type=entry_type,
        text=text,
        word_count=len(text.split()),
        created_at=created_at,
    )

    path = day_file(date)
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists()
        with path.open("a", encoding="utf-8") as f:
            if new_file:
                f.write(_frontmatter(date, created_at))
            f.write(_turn_block(rec, ts.strftime("%H:%M:%S")))

    log.info("vault: saved %s turn %s to %s", entry_type, rec.id, path.name)
    return rec
