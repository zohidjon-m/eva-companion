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
# Phase 6: book chunks live in their own collection, strictly separate from
# journal summaries (EVA_SYSTEM_DESIGN §6). Same embedding model, different
# retrieval path — recall never touches corpus; advice never touches journals.
CORPUS_COLLECTION = "corpus"

# The collection-metadata key under which we record the embedding model name. If
# the model ever changes, stored vectors are incompatible; we detect the mismatch
# on open and refuse to silently mix vectors from two models (§6 versioning guard).
MODEL_META_KEY = "embedding_model"


class EmbeddingModelMismatch(RuntimeError):
    """Raised when a collection was built with a different embedding model.

    Mixing vectors from two models yields meaningless distances, so we fail hard
    and point the operator at ``scripts/reindex.py`` to re-embed from L0/L1.
    """


# Process-wide singletons: the embedder loads an ONNX model and the Chroma client
# opens an on-disk store — both expensive, so build them lazily once and reuse.
_client = None
_journals = None
_corpus = None
_embedder = None


def chroma_dir():
    """Return the on-disk directory for ChromaDB's persistent store."""
    return vault_dir() / "chroma"


def fastembed_cache_dir():
    """Return the persistent directory the bge-small embedding model lives in.

    Kept inside the vault (``<vault>/models/fastembed``), next to the whisper
    weights, so all of Eva's local models live in one durable, user-owned place.

    Why this is not left to fastembed's default: fastembed caches to the *system
    temp dir* (``$TMPDIR/fastembed_cache``), which macOS purges periodically and
    on reboot. When that happens the model vanishes, and because the runtime is
    offline (``HF_HUB_OFFLINE=1`` above + the net-guard), fastembed cannot
    re-download it — every embed then fails, surfacing to the user as a corpus
    upload that "went wrong". Pinning the cache into the vault makes the model
    survive temp cleanups, so a one-time download (scripts/download_embed_model.py)
    actually stays put.
    """
    return vault_dir() / "models" / "fastembed"


# bge-small-en-v1.5 is an *asymmetric* retriever: the query is meant to carry a
# short instruction prefix while the stored passages are embedded plain. Applying
# the prefix on the query side only pulls a genuinely relevant passage markedly
# closer than an off-topic one, which is what lets a distance threshold actually
# separate "in the library" from "not in the library". The string below is the
# model's documented query instruction — do NOT change it without re-embedding,
# and NEVER apply it to stored chunks/summaries (that would break the asymmetry).
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed text with fastembed's bge-small-en-v1.5 (local, deterministic).

    This is the PASSAGE side of the asymmetric pair: stored chunks (corpus) and
    stored summaries (journals) are embedded plain, with no prefix. Search queries
    go through :func:`_embed_query` instead.

    We compute embeddings ourselves and hand Chroma explicit vectors rather than
    attaching a Chroma embedding function. That pins the exact model the plan
    requires (bge-small-en-v1.5) and keeps us off Chroma's per-version embedding-
    function API, which has shifted between releases.
    """
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        # Pin the cache into the vault (not fastembed's volatile temp default) so
        # the model is durable across reboots/temp cleanups — see fastembed_cache_dir().
        cache = fastembed_cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        _embedder = TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(cache))
    return [list(map(float, v)) for v in _embedder.embed(list(texts))]


def _embed_query(query_text: str) -> list[list[float]]:
    """Embed a SEARCH QUERY with the bge-small instruction prefix (asymmetric side).

    The one place the query prefix is applied, so every retrieval path shares one
    definition: corpus retrieval (:func:`query_corpus`) uses it now, and Phase 11's
    journal recall (:func:`recall`) uses it too — both query against indexes that
    were built with the plain (prefix-free) passage embeddings, so the prefix must
    live here, on the query, and nowhere on the stored side.
    """
    return _embed([f"{QUERY_PREFIX}{query_text}"])


def _get_client():
    """Open the persistent ChromaDB client once and reuse it (memoised).

    Both collections (``journals`` and ``corpus``) share one on-disk store under
    the vault, so they share one client.
    """
    global _client
    if _client is None:
        import chromadb

        path = chroma_dir()
        path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(path))
        log.info("opened ChromaDB persistent client at %s", path)
    return _client


def _check_model_version(collection) -> None:
    """Guard against a changed embedding model (§6 versioning guard).

    If the collection records a different model than ``EMBED_MODEL``, the stored
    vectors are incompatible — raise rather than mix them. Collections created
    before this guard existed have no recorded model; treat that as compatible
    (they were built with the same single model the app has always used).
    """
    stored = (collection.metadata or {}).get(MODEL_META_KEY)
    if stored is not None and stored != EMBED_MODEL:
        raise EmbeddingModelMismatch(
            f"Collection '{collection.name}' was built with embedding model "
            f"'{stored}', but the app now uses '{EMBED_MODEL}'. Re-embed from "
            f"scratch with scripts/reindex.py before continuing."
        )


def _get_collection():
    """Lazily create/open the persistent ``journals`` collection (memoised).

    The collection stores precomputed vectors, so no Chroma-side embedding
    function is configured — nothing here ever reaches for the network.
    """
    global _journals
    if _journals is not None:
        return _journals

    _journals = _get_client().get_or_create_collection(
        name=JOURNALS_COLLECTION,
        metadata={"hnsw:space": "cosine", MODEL_META_KEY: EMBED_MODEL},
    )
    _check_model_version(_journals)
    return _journals


def _get_corpus_collection():
    """Lazily create/open the persistent ``corpus`` collection (memoised).

    Strictly separate from ``journals`` (§6): book chunks only. The embedding
    model name is stored in the collection metadata at creation so the versioning
    guard can detect an incompatible model on a later run.
    """
    global _corpus
    if _corpus is not None:
        return _corpus

    _corpus = _get_client().get_or_create_collection(
        name=CORPUS_COLLECTION,
        metadata={"hnsw:space": "cosine", MODEL_META_KEY: EMBED_MODEL},
    )
    _check_model_version(_corpus)
    return _corpus


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
        query_embeddings=_embed_query(query_text), n_results=n_results, where=where
    )


def count() -> int:
    """Return the number of vectors in the journals collection (diagnostics/tests)."""
    return _get_collection().count()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — corpus collection (book chunks). Kept here, in the same file as
# journals, but on a separate collection with a separate retrieval path.
# ─────────────────────────────────────────────────────────────────────────────


def index_corpus_chunks(
    *,
    doc_id: str,
    source_file: str,
    chunks,
) -> int:
    """Embed and index one document's chunks into the ``corpus`` collection.

    ``chunks`` is a list of :class:`ingest.chunker.Chunk`. Each chunk is stored
    under a stable id ``"{doc_id}:{chunk.index}"`` (re-indexing the same document
    replaces its chunks) with metadata ``{source_file, page, section, doc_id,
    chunk_index}`` — exactly the citation fields §6 specifies, plus ``doc_id`` so
    a whole document can be removed in one call. ChromaDB metadata must be
    scalars, so ``page``/``section`` are only set when present. Returns the number
    of chunks indexed.

    Embeds in one batch (fastembed is far faster batched than per-chunk). Does
    nothing and returns 0 for an empty chunk list.
    """
    if not chunks:
        return 0

    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict] = []
    for chunk in chunks:
        meta: dict = {
            "source_file": source_file,
            "doc_id": doc_id,
            "chunk_index": chunk.index,
        }
        if chunk.page is not None:
            meta["page"] = chunk.page
        if chunk.section is not None:
            meta["section"] = chunk.section
        ids.append(f"{doc_id}:{chunk.index}")
        texts.append(chunk.text)
        metadatas.append(meta)

    _get_corpus_collection().upsert(
        ids=ids,
        embeddings=_embed(texts),
        documents=texts,
        metadatas=metadatas,
    )
    log.info("indexed %d corpus chunks for document %s", len(ids), doc_id)
    return len(ids)


def query_corpus(query_text: str, n_results: int = 5) -> dict:
    """Return the nearest corpus chunks to ``query_text`` (book retrieval).

    This is the corpus-only retrieval path (it never touches ``journals``). Phase
    7 builds grounded, cited answers on top of it; it exists here so the ingest
    pipeline is verifiable end-to-end — a phrase from an uploaded book retrieves
    the chunk it came from.
    """
    return _get_corpus_collection().query(
        query_embeddings=_embed_query(query_text), n_results=n_results
    )


def delete_corpus_document(doc_id: str) -> None:
    """Remove every chunk belonging to ``doc_id`` from the ``corpus`` collection."""
    _get_corpus_collection().delete(where={"doc_id": doc_id})
    log.info("deleted corpus chunks for document %s", doc_id)


def corpus_count() -> int:
    """Return the total number of chunks in the corpus collection (diagnostics)."""
    return _get_corpus_collection().count()
