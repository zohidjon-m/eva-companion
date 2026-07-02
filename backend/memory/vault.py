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

import hashlib
import logging
import re
import shutil
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
    to ``db.insert_entry`` without reshaping. ``id`` is Eva's durable entry UID:
    it is generated once, stored in L0, and reused by every derived layer as the
    evidence pointer. The full ``text`` is carried so the index can store it
    verbatim.
    """

    id: str            # stable UID, never derived from the entry text
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

    The entry id is Eva's durable UID, written in the V2 header so a DB rebuild
    can preserve evidence pointers without depending on hidden comments. The
    body is the user's text after the same surrounding-whitespace trim the parser
    applies.
    """
    return (
        f"\n## {time_str} · {rec.type} · {rec.id}\n\n"
        f"{rec.text.strip()}\n"
    )


def canonical_entry_text(text: str) -> str:
    """Return the exact body text Eva treats as an entry's source content.

    Markdown block headers and legacy id comments are metadata, not journal
    content. R2's ``source_hash`` is based only on this canonical body so a
    header migration does not make derived memory stale.
    """
    return text.strip()


def source_hash(text: str) -> str:
    """Return a stable SHA-256 hash for an entry's canonical source body."""
    return hashlib.sha256(canonical_entry_text(text).encode("utf-8")).hexdigest()


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
    body = canonical_entry_text(text)
    rec = EntryRecord(
        id=str(uuid.uuid4()),
        date=date,
        type=entry_type,
        text=body,
        word_count=len(body.split()),
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


def update_entry(entry_id: str, new_text: str) -> EntryRecord | None:
    """Rewrite the body of an existing turn in its day file. Returns the updated
    record, or ``None`` if no turn with that id is found on disk.

    This is the single, deliberate exception to the append-only rule (editable
    past entries). The crash-safety that rule gave us is preserved two ways: the
    whole day file is copied to ``<file>.bak`` before the rewrite, and the new
    content is written to a temp file then atomically swapped in. Only the matched
    turn's body changes — the frontmatter, the day heading, and every other turn
    in the file are kept byte-for-byte.
    """
    if not new_text or not new_text.strip():
        raise ValueError("refusing to save an empty entry")
    body = canonical_entry_text(new_text)

    with _write_lock:
        for date in list_day_dates():
            path = day_file(date)
            lines = path.read_text(encoding="utf-8").split("\n")
            out: list[str] = []
            i, n = 0, len(lines)
            cur_time: str | None = None
            cur_type: str | None = None
            hit_time: str | None = None
            hit_type: str | None = None
            found = False

            while i < n:
                line = lines[i]
                parsed_header = _parse_turn_header(line)
                header = _TURN_HEADER_RE.match(line)
                if parsed_header:
                    cur_time, cur_type, header_uid = parsed_header
                elif header:
                    cur_time, cur_type, header_uid = header.group(1), header.group(2).strip(), None
                out.append(line)
                if parsed_header and parsed_header[2] == entry_id:
                    found = True
                    hit_time, hit_type = parsed_header[0], parsed_header[1]
                    i += 1
                    while i < n and not _TURN_HEADER_RE.match(lines[i]):
                        i += 1
                    out.append("")
                    out.extend(body.split("\n"))
                    out.append("")
                    continue
                id_match = _ID_RE.match(line.strip())
                if id_match and id_match.group(1).strip() == entry_id:
                    found = True
                    hit_time, hit_type = cur_time, cur_type
                    # Skip the original body up to the next turn header (or EOF),
                    # then write the new body framed by single blank lines so the
                    # section spacing matches what `_turn_block` originally wrote.
                    i += 1
                    while i < n and not _TURN_HEADER_RE.match(lines[i]):
                        i += 1
                    out.append("")
                    out.extend(body.split("\n"))
                    out.append("")
                    continue
                i += 1

            if found:
                backup = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, backup)
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text("\n".join(out), encoding="utf-8")
                tmp.replace(path)
                rec = EntryRecord(
                    id=entry_id,
                    date=date,
                    type=hit_type or "journal",
                    text=body,
                    word_count=len(body.split()),
                    created_at=f"{date}T{hit_time or '00:00:00'}",
                )
                log.info("vault: updated %s turn %s in %s (backup %s)",
                         rec.type, entry_id, path.name, backup.name)
                return rec
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Read side — parsing the Markdown back out. The day files are the source of
# truth (CLAUDE.md rule 5), so Phase 5's read-only "time-travel" day view reads
# them directly rather than the derived SQLite index. This is also what lets a
# hand-placed older .md file render without ever having been indexed.
# ─────────────────────────────────────────────────────────────────────────────

# Matches a YYYY-MM-DD day-file stem so we never treat a stray file as a day.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A turn section header, e.g. "## 09:14:03 · journal".
_TURN_HEADER_RE = re.compile(r"^## (\d{2}:\d{2}:\d{2}) · (.+)$")
# The HTML-comment id line that immediately follows a turn header.
_ID_RE = re.compile(r"^<!-- id: (.+) -->$")


def _parse_turn_header(line: str) -> tuple[str, str, str | None] | None:
    match = _TURN_HEADER_RE.match(line)
    if not match:
        return None
    time_str = match.group(1)
    parts = [p.strip() for p in match.group(2).split(" · ")]
    turn_type = parts[0]
    uid = parts[1] if len(parts) > 1 and parts[1] else None
    return time_str, turn_type, uid


@dataclass(frozen=True)
class DayTurn:
    """One turn parsed back out of a day file (read-only view).

    Mirrors what :func:`_turn_block` wrote: the turn's id (or ``None`` for a
    hand-written file that omitted the comment), its time-of-day, its type, and
    the verbatim body text. Used by the journal browse/day view.
    """

    id: str | None
    time: str          # HH:MM:SS
    type: str          # "chat" | "journal" (whatever the header carried)
    text: str          # the body, stripped of surrounding blank lines


@dataclass(frozen=True)
class BackfillReport:
    """Summary of a Markdown UID-header backfill run.

    ``errors`` is non-empty when a file was unsafe to rewrite, usually because a
    V2 header UID and legacy comment id disagree. Files with errors are left
    untouched so a human can inspect the identity conflict.
    """

    files_checked: int
    files_changed: int
    entries_changed: int
    errors: tuple[str, ...]


def _backfill_day_text(
    text: str, *, id_factory=lambda: str(uuid.uuid4())
) -> tuple[str, int]:
    lines = text.split("\n")
    out: list[str] = []
    changed_entries = 0
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        parsed = _parse_turn_header(line)
        if parsed is None:
            out.append(line)
            i += 1
            continue

        time_str, turn_type, header_uid = parsed
        legacy_uid: str | None = None
        if i + 1 < n:
            legacy_match = _ID_RE.match(lines[i + 1].strip())
            if legacy_match:
                legacy_uid = legacy_match.group(1).strip()

        if header_uid and legacy_uid and header_uid != legacy_uid:
            raise ValueError(
                f"conflicting ids at {time_str}: header has {header_uid}, "
                f"legacy comment has {legacy_uid}"
            )

        uid = header_uid or legacy_uid or id_factory()
        new_header = f"## {time_str} · {turn_type} · {uid}"
        out.append(new_header)
        if new_header != line or legacy_uid is not None:
            changed_entries += 1

        i += 1
        if legacy_uid is not None:
            i += 1

    return "\n".join(out), changed_entries


def backfill_entry_uids() -> BackfillReport:
    """Rewrite legacy day files so every turn carries its UID in the header.

    This is the one-time R2 migration for L0 Markdown. It is idempotent: V2
    headers are left as-is, legacy ``<!-- id: ... -->`` comments are promoted to
    the header, and missing ids get UUIDv4 values. A file with conflicting ids is
    reported and not rewritten.
    """
    files_checked = 0
    files_changed = 0
    entries_changed = 0
    errors: list[str] = []

    with _write_lock:
        for date in list_day_dates():
            files_checked += 1
            path = day_file(date)
            original = path.read_text(encoding="utf-8")
            try:
                updated, changed = _backfill_day_text(original)
            except ValueError as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            if changed == 0:
                continue
            backup = path.with_suffix(path.suffix + ".uid-backfill.bak")
            shutil.copy2(path, backup)
            tmp = path.with_suffix(path.suffix + ".uid-backfill.tmp")
            tmp.write_text(updated, encoding="utf-8")
            tmp.replace(path)
            files_changed += 1
            entries_changed += changed
            log.info("backfilled %d uid header(s) in %s", changed, path.name)

    return BackfillReport(
        files_checked=files_checked,
        files_changed=files_changed,
        entries_changed=entries_changed,
        errors=tuple(errors),
    )


def list_day_dates() -> list[str]:
    """Return every day-file date present on disk, newest first.

    Reads the journal directory rather than the index, so files that were never
    indexed (e.g. an older entry placed by hand) are still discoverable. Only
    ``YYYY-MM-DD.md`` files count; anything else in the folder is ignored.
    """
    directory = journal_dir()
    if not directory.exists():
        return []
    dates = [p.stem for p in directory.glob("*.md") if _DATE_RE.match(p.stem)]
    return sorted(dates, reverse=True)


def read_day(date: str) -> list[DayTurn]:
    """Parse one day's Markdown file into its turns, in written order.

    Returns ``[]`` if the file does not exist. The parse mirrors the write
    format: a ``## HH:MM:SS · type`` header opens a turn, an optional
    ``<!-- id: … -->`` comment carries its id, and everything up to the next
    header is the body. Frontmatter and the ``# DATE`` heading are skipped. This
    is deliberately forgiving so a lightly hand-edited file still reads.
    """
    path = day_file(date)
    if not path.exists():
        return []

    turns: list[DayTurn] = []
    cur: dict | None = None
    body: list[str] = []

    def flush() -> None:
        if cur is not None:
            turns.append(
                DayTurn(
                    id=cur["id"],
                    time=cur["time"],
                    type=cur["type"],
                    text="\n".join(body).strip(),
                )
            )

    for line in path.read_text(encoding="utf-8").splitlines():
        header = _parse_turn_header(line)
        if header:
            flush()
            cur = {"time": header[0], "type": header[1], "id": header[2]}
            body = []
            continue
        if cur is None:
            continue  # frontmatter / day heading — nothing to collect yet
        id_match = _ID_RE.match(line.strip())
        if id_match and not body:
            if cur["id"] is None:
                cur["id"] = id_match.group(1).strip()
            continue
        body.append(line)
    flush()
    return turns
