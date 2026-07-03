"""Corpus orchestrator — save → load → chunk → embed → index, plus the manifest.

This is the seam ``POST /corpus/upload`` and ``GET /corpus`` sit on. It does two
jobs:

1. **Ingest a file.** Save the raw upload into the vault (``corpus/``) so the
   user owns a readable copy, then run it through ``loaders`` → ``chunker`` →
   ``memory.vector`` (the ``corpus`` collection). A file that can't be read is
   recorded as a *failed* document rather than raising past the API — the Library
   shows the failure state, nothing crashes.

2. **Track documents.** A small JSON manifest (``corpus/library.json``) records
   each document's id, filename, status, chunk count, and any error, so the
   Library can list and remove documents. The manifest is metadata *about* the
   corpus; the chunks themselves live in ChromaDB and the bytes on disk — both
   rebuildable, so the manifest is allowed to be a convenience index.

The corpus is entirely local; nothing here makes a network call.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

from memory import vault_dir, vector

from .chunker import chunk_sections
from .loaders import LoaderError, load_document

log = logging.getLogger("eva.ingest.corpus")

# Serialise manifest reads/writes. Uploads can overlap (the UI may queue several),
# and the manifest is a single small file — a process-wide lock keeps it coherent.
_manifest_lock = threading.Lock()


def corpus_dir() -> Path:
    """Return the vault's ``corpus/`` directory (raw uploaded books live here)."""
    return vault_dir() / "corpus"


def _manifest_path() -> Path:
    """Return the path to the document manifest JSON."""
    return corpus_dir() / "library.json"


def _read_manifest() -> list[dict]:
    """Load the manifest's document list, or ``[]`` if it doesn't exist yet.

    Tolerant of a missing or unreadable file: the manifest is a rebuildable
    convenience index, so a corrupt one degrades to "no documents listed" rather
    than crashing the Library.
    """
    path = _manifest_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        docs = data.get("documents", [])
        return docs if isinstance(docs, list) else []
    except (json.JSONDecodeError, OSError) as e:
        log.warning("could not read corpus manifest (%s); treating as empty", e)
        return []


def _write_manifest(documents: list[dict]) -> None:
    """Persist the document list to the manifest (atomic replace)."""
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"documents": documents}, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_documents() -> list[dict]:
    """Return all known corpus documents, newest first (the Library list).

    The manifest is appended in chronological order, so reversing it gives
    newest-first reliably — more robust than sorting on the second-resolution
    ``added_at`` timestamp, which ties for uploads within the same second.
    """
    return list(reversed(_read_manifest()))


def _safe_name(filename: str) -> str:
    """Reduce an uploaded filename to a safe basename (no path traversal).

    Strips any directory components and characters that don't belong in a file
    name, so a malicious ``../../etc/x`` upload can only ever land inside
    ``corpus/``.
    """
    base = Path(filename).name
    cleaned = "".join(c for c in base if c.isalnum() or c in " ._-()").strip()
    return cleaned or "document"


def _friendly_ingest_error(exc: Exception) -> str:
    """Turn an unexpected ingest exception into an actionable, user-facing reason.

    The most common *real* cause of a non-LoaderError failure is the embedding
    model not being available: it loads (fastembed → bge-small) only when the
    weights are cached, and the runtime is offline, so a cold/cleared cache raises
    a HuggingFace ``LocalEntryNotFoundError`` deep in the embed step. Mapping that
    to a clear "run the download script" message (instead of the old opaque
    "Something went wrong") is what lets a user actually fix it — the full
    traceback is still logged for diagnosis.
    """
    needle = f"{type(exc).__name__}: {exc}".lower()
    # fastembed raises ValueError("Could not load model ... from any source.") when
    # the bge-small weights aren't cached and the runtime can't fetch them (offline
    # + net-guard). Match that, plus the HuggingFace offline-miss for good measure.
    if (
        "could not load model" in needle
        or "from any source" in needle
        or "localentrynotfound" in needle
        or "bge-small" in needle
    ):
        return (
            "Eva's embedding model isn't set up, so this file can't be indexed. "
            "Run:  backend/.venv/bin/python scripts/download_embed_model.py  "
            "(one-time, needs internet), then try again."
        )
    return "Something went wrong while processing this file."


def ingest_file(filename: str, data: bytes) -> dict:
    """Ingest one uploaded file end-to-end and record it in the manifest.

    Pipeline: save the raw bytes into ``corpus/`` → load → chunk → embed+index
    into the ``corpus`` collection. On success the returned document has
    ``status='ready'`` and a ``chunk_count``; on a load/processing failure it has
    ``status='failed'`` and a user-facing ``error`` (and the saved bytes are
    removed). Either way a document record is appended to the manifest and
    returned, so the Library always reflects the attempt.
    """
    doc_id = uuid.uuid4().hex[:12]
    ext = Path(filename).suffix.lower()
    stored_name = f"{doc_id}__{_safe_name(filename)}"
    stored_path = corpus_dir() / stored_name

    doc: dict = {
        "id": doc_id,
        "filename": Path(filename).name,
        "ext": ext,
        "stored_filename": stored_name,
        "added_at": datetime.now().isoformat(timespec="seconds"),
        "status": "ready",
        "chunk_count": 0,
        "error": None,
    }

    corpus_dir().mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(data)

    try:
        sections = load_document(filename, data)
        chunks = chunk_sections(sections)
        count = vector.index_corpus_chunks(
            doc_id=doc_id, source_file=doc["filename"], chunks=chunks
        )
        doc["chunk_count"] = count
        doc["status"] = "ready"
        log.info("ingested '%s' (%s) → %d chunks", doc["filename"], doc_id, count)
    except LoaderError as e:
        doc["status"] = "failed"
        doc["error"] = str(e)
        _safe_unlink(stored_path)
        log.warning("ingest failed for '%s': %s", doc["filename"], e)
    except Exception as e:  # noqa: BLE001 — never let an upload crash the server
        doc["status"] = "failed"
        doc["error"] = _friendly_ingest_error(e)
        _safe_unlink(stored_path)
        log.exception("unexpected ingest error for '%s': %s", doc["filename"], e)

    with _manifest_lock:
        docs = _read_manifest()
        docs.append(doc)
        _write_manifest(docs)
    return doc


def reindex_all_documents() -> tuple[int, int, int]:
    """Re-embed every ready corpus document from its stored bytes (R4 rebuild).

    The manifest and the raw files in ``corpus/`` are the source of truth for the
    library; the ChromaDB ``corpus`` collection is derived. So after a
    ``chroma/`` delete, this replays load → chunk → index for each ready document
    (idempotent per ``doc:chunk``). A document whose stored file is missing or no
    longer loadable is counted as failed and skipped — never raised past here, so
    one bad file can't abort the whole rebuild.

    Returns ``(docs_reindexed, chunks_indexed, docs_failed)``.
    """
    docs = 0
    chunks = 0
    failed = 0
    for doc in _read_manifest():
        if doc.get("status") != "ready":
            continue
        stored = doc.get("stored_filename")
        path = corpus_dir() / stored if stored else None
        if path is None or not path.exists():
            failed += 1
            log.warning("reindex: stored file missing for %s (%r)", doc.get("id"), stored)
            continue
        try:
            sections = load_document(doc["filename"], path.read_bytes())
            doc_chunks = chunk_sections(sections)
            count = vector.index_corpus_chunks(
                doc_id=doc["id"], source_file=doc["filename"], chunks=doc_chunks
            )
            docs += 1
            chunks += count
            log.info("reindex: '%s' (%s) → %d chunks", doc["filename"], doc["id"], count)
        except Exception as e:  # noqa: BLE001 — one bad file must not abort the rebuild
            failed += 1
            log.warning("reindex: failed to re-index %s: %s", doc.get("id"), e)
    return docs, chunks, failed


def remove_document(doc_id: str) -> bool:
    """Remove a document: its chunks, its stored bytes, and its manifest entry.

    Returns ``True`` if the document existed. Vector and file deletion are
    best-effort (a missing file or already-empty collection is not an error); the
    manifest entry is always removed so the Library reflects the removal.
    """
    with _manifest_lock:
        docs = _read_manifest()
        match = next((d for d in docs if d.get("id") == doc_id), None)
        if match is None:
            return False
        remaining = [d for d in docs if d.get("id") != doc_id]
        _write_manifest(remaining)

    try:
        vector.delete_corpus_document(doc_id)
    except Exception as e:  # noqa: BLE001 — manifest already updated; log and move on
        log.warning("could not delete vectors for %s: %s", doc_id, e)

    stored = match.get("stored_filename")
    if stored:
        _safe_unlink(corpus_dir() / stored)
    log.info("removed corpus document %s ('%s')", doc_id, match.get("filename"))
    return True


def _safe_unlink(path: Path) -> None:
    """Delete a file if it exists, swallowing OS errors (best-effort cleanup)."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:  # pragma: no cover — e.g. permissions
        log.warning("could not delete %s: %s", path, e)
