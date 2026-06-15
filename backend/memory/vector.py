"""L2 semantic index — ChromaDB ``journals`` collection for entry summaries.

On every successful extraction, the 4–5 sentence summary is embedded here with
``bge-small-en-v1.5`` (local, via fastembed) so later phases can do associative
recall over past entries. Metadata carries ``{entry_id, date, mood, themes,
is_seeded}`` exactly as §7.1's ChromaDB note specifies; recall queries filter
``is_seeded=False`` so demo seed data never surfaces as a real memory.

Offline by construction: the embedding model is downloaded once, out-of-band
(see scripts/download_embed_model.py), and at runtime we force HuggingFace into
offline mode so the privacy net-guard is never tripped by an update check.
"""

from __future__ import annotations

import logging
import os

# Force the embedding stack offline BEFORE fastembed/huggingface_hub import, so a
# cached model is used and no outbound call is attempted at runtime. The one-time
# download happens in a separate process that does NOT set these.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from . import vault_dir

log = logging.getLogger("eva.memory.vector")

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
JOURNALS_COLLECTION = "journals"

# Process-wide singletons: the embedder loads an ONNX model and the Chroma client
# opens an on-disk store — both expensive, so build them lazily once and reuse.
_client = None
_journals = None
_embedder = None


def chroma_dir():
    """Return the on-disk directory for ChromaDB's persistent store."""
    return vault_dir() / "chroma"


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed text with fastembed's bge-small-en-v1.5 (local, deterministic).

    We compute embeddings ourselves and hand Chroma explicit vectors rather than
    attaching a Chroma embedding function. That pins the exact model the plan
    requires (bge-small-en-v1.5) and keeps us off Chroma's per-version embedding-
    function API, which has shifted between releases.
    """
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(model_name=EMBED_MODEL)
    return [list(map(float, v)) for v in _embedder.embed(list(texts))]


def _get_collection():
    """Lazily create/open the persistent ``journals`` collection (memoised).

    The collection stores precomputed vectors, so no Chroma-side embedding
    function is configured — nothing here ever reaches for the network.
    """
    global _client, _journals
    if _journals is not None:
        return _journals

    import chromadb

    path = chroma_dir()
    path.mkdir(parents=True, exist_ok=True)
    _client = chromadb.PersistentClient(path=str(path))
    _journals = _client.get_or_create_collection(
        name=JOURNALS_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    log.info("opened ChromaDB journals collection at %s", path)
    return _journals


def embed_summary(
    *,
    entry_id: str,
    date: str,
    summary: str,
    mood: int | None,
    themes: list[str],
    is_seeded: bool = False,
) -> None:
    """Embed one entry summary into the ``journals`` collection.

    Uses ``entry_id`` as the Chroma document id (upsert-safe: re-embedding the
    same entry replaces it). ChromaDB metadata values must be scalars, so the
    ``themes`` list is stored as a comma-joined string and ``mood`` only when it
    is not NULL — both are documented seams the recall code in Phase 11 reads back.
    """
    metadata: dict = {
        "entry_id": entry_id,
        "date": date,
        "themes": ", ".join(themes),
        "is_seeded": is_seeded,
    }
    if mood is not None:
        metadata["mood"] = mood

    _get_collection().upsert(
        ids=[entry_id],
        embeddings=_embed([summary]),
        documents=[summary],
        metadatas=[metadata],
    )
    log.info("embedded summary for entry %s into journals", entry_id)


def recall(query_text: str, n_results: int = 5, *, include_seeded: bool = False) -> dict:
    """Return the nearest journal summaries to ``query_text``.

    Filters out seeded demo data by default (``is_seeded=False``), matching §7.1's
    recall rule. Phase 11 builds the real recall UX on top of this; it exists here
    so the embedding path is verifiable end-to-end from day one.
    """
    where = None if include_seeded else {"is_seeded": False}
    return _get_collection().query(
        query_embeddings=_embed([query_text]), n_results=n_results, where=where
    )


def count() -> int:
    """Return the number of vectors in the journals collection (diagnostics/tests)."""
    return _get_collection().count()
