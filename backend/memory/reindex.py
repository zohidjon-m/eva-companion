"""R4 rebuild path: derive the L2 ChromaDB vectors from L1 + stored corpus files.

L1 (SQLite) and the raw corpus files remain the source of truth. This module
re-embeds the derived vector store — the ``journals`` summary index, the
``episodes`` open-loop/event index, and the ``corpus`` book-chunk index — so that
``rm -rf <vault>/chroma`` followed by a reindex restores equivalent recall.

Idempotent: journal summaries upsert by entry id, episode units delete-then-
upsert per entry, and corpus chunks upsert by ``doc:chunk`` — so a reindex is
safe to run with or without a prior ``chroma/`` delete.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import db, vector

log = logging.getLogger("eva.memory.reindex")


@dataclass(frozen=True)
class ReindexReport:
    """Counters for one R4 L2 rebuild run."""

    entries_scanned: int
    journals_embedded: int
    episode_units_embedded: int
    corpus_docs: int
    corpus_chunks: int
    corpus_failed: int


def _json_list(value: str | None) -> list:
    """Parse a JSON array column into a list, tolerating NULL/garbage (→ ``[]``)."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


async def reindex_all(*, include_corpus: bool = True) -> ReindexReport:
    """Rebuild the L2 vector store from L1 SQLite and stored corpus files.

    Reads every done extraction (seeded and real), re-embeds the summary into
    ``journals`` and the open loops + events into ``episodes``, threading the
    ``is_seeded`` flag through so demo rows stay out of live recall. When
    ``include_corpus`` is set, replays the corpus ingest for every ready document.

    Async only to match ``reextract_all``'s signature and call convention; no
    model generation happens here — embedding is local and synchronous.
    """
    entries_scanned = 0
    journals_embedded = 0
    episode_units_embedded = 0

    conn = db.get_or_create_db()
    try:
        for row in db.all_done_extractions(conn):
            entries_scanned += 1
            is_seeded = bool(row["is_seeded"])
            themes = _json_list(row["themes"])

            summary = row["summary"]
            if summary:
                vector.embed_summary(
                    entry_id=row["entry_id"],
                    date=row["date"],
                    summary=summary,
                    mood=row["mood"],
                    themes=themes,
                    is_seeded=is_seeded,
                )
                journals_embedded += 1

            episode_units_embedded += vector.embed_episodes(
                entry_id=row["entry_id"],
                date=row["date"],
                mood=row["mood"],
                themes=themes,
                open_loops=_json_list(row["open_loops"]),
                events=_json_list(row["events"]),
                is_seeded=is_seeded,
            )
    finally:
        conn.close()

    corpus_docs = 0
    corpus_chunks = 0
    corpus_failed = 0
    if include_corpus:
        from ingest import corpus

        corpus_docs, corpus_chunks, corpus_failed = corpus.reindex_all_documents()

    log.info(
        "reindex: entries=%d journals=%d episode_units=%d corpus_docs=%d "
        "corpus_chunks=%d corpus_failed=%d",
        entries_scanned,
        journals_embedded,
        episode_units_embedded,
        corpus_docs,
        corpus_chunks,
        corpus_failed,
    )
    return ReindexReport(
        entries_scanned=entries_scanned,
        journals_embedded=journals_embedded,
        episode_units_embedded=episode_units_embedded,
        corpus_docs=corpus_docs,
        corpus_chunks=corpus_chunks,
        corpus_failed=corpus_failed,
    )
