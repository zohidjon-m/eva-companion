"""Corpus retrieval — embed the query, return the relevant cited passages.

This is the read half of the RAG story (the write half — ingest → chunk → embed
— landed in Phase 6). Given a user message the intent gate has already decided is
a ``question`` or ``advice_request``, it pulls the nearest corpus chunks, keeps
only those above a relevance threshold, and packages each with the source
locator (file + page/section) that Phase 6 stored on the chunk.

Two disciplines from the architecture shape this module:

  * **Listen-first lives upstream.** Retrieval is only ever *called* on a
    retrieving intent (EVA_MEMORY_ARCHITECTURE §5.9); this module does not
    re-check intent, it just retrieves when asked.
  * **Grounded citations only (§5.10, §7.3).** Every citation Eva can show comes
    from a passage *actually retrieved here* — never from the model's memory. If
    nothing clears the threshold we return an empty list, and the grounding rule
    in the prompt tells Eva to say she doesn't find it rather than invent a
    source. A relevance threshold is therefore not a nicety: it is what makes
    "ask for something not in the library → no fabricated citation" true.

The threshold is on cosine *distance* (the ``corpus`` collection is built with
``hnsw:space=cosine``), where smaller = more similar. It is set conservatively
and exposed as a constant so it can be tuned against the real corpus during the
demo without touching call sites.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import vector

log = logging.getLogger("eva.memory.retrieval")

# How many candidate chunks to pull before threshold filtering.
DEFAULT_N_RESULTS = 4

# Maximum cosine distance for a chunk to count as relevant. Cosine distance runs
# 0 (identical) … 2 (opposite). This value was chosen empirically (not guessed):
# with the bge-small query prefix active (see vector._embed_query), a genuinely
# in-document question measured ~0.31 to its passage, while the nearest *off-topic*
# question (same subject domain) measured ~0.45 — a ~0.14 gap. 0.38 sits in the
# middle of that gap (~0.07 margin on each side), so on-topic passages are kept and
# off-topic ones are rejected, which is what stops a misleading citation chip from
# appearing on a question the library can't actually answer. Re-measure and re-centre
# this if the embedding model or the query prefix ever changes.
MAX_DISTANCE = 0.38


@dataclass(frozen=True)
class Passage:
    """One retrieved corpus chunk plus the source locator needed to cite it.

    ``page`` / ``section`` are whatever Phase 6 stored for the chunk (a PDF chunk
    carries a page, a Markdown chunk a heading; either may be absent). ``distance``
    is the cosine distance to the query, kept for diagnostics and threshold tests.
    """

    text: str
    source_file: str
    page: int | None
    section: str | None
    distance: float

    def label(self) -> str:
        """A short human label for the citation chip, e.g. ``book.pdf · p. 42``.

        Falls back gracefully when no locator is present (some loaders produce
        neither a page nor a section) so a chip is never blank.
        """
        if self.page is not None:
            locator = f"p. {self.page}"
        elif self.section:
            locator = self.section
        else:
            locator = None
        return f"{self.source_file} · {locator}" if locator else self.source_file

    def as_citation(self) -> dict:
        """Serialise to the citation payload the chat socket sends the UI.

        Carries the full passage ``text`` so clicking a chip can show the exact
        words Eva was grounded in — the user can verify the citation themselves,
        which is the whole point of grounded-only citations.
        """
        return {
            "source_file": self.source_file,
            "page": self.page,
            "section": self.section,
            "label": self.label(),
            "text": self.text,
        }


def _parse_results(raw: dict) -> list[Passage]:
    """Turn ChromaDB's columnar query result into a flat list of Passages.

    Chroma returns parallel lists nested one level per query (we always send one
    query, so we read index 0). A result with no documents yields an empty list.
    """
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]

    passages: list[Passage] = []
    for text, meta, distance in zip(documents, metadatas, distances):
        meta = meta or {}
        passages.append(
            Passage(
                text=text,
                source_file=meta.get("source_file", "unknown source"),
                page=meta.get("page"),
                section=meta.get("section"),
                distance=float(distance),
            )
        )
    return passages


def retrieve_corpus(
    query_text: str,
    *,
    n_results: int = DEFAULT_N_RESULTS,
    max_distance: float = MAX_DISTANCE,
) -> list[Passage]:
    """Return the corpus passages relevant to ``query_text``, nearest first.

    Embeds the query and queries the ``corpus`` collection, then drops anything
    above ``max_distance`` so only genuinely on-topic passages survive. Returns an
    empty list when nothing clears the threshold (or the corpus is empty) — the
    signal the caller uses to let Eva say she doesn't find it rather than cite
    something fabricated.

    Never raises for a retrieval problem: a vector-store error degrades to "no
    passages" and is logged, because a grounding failure must fail toward *fewer*
    citations, never toward a crash or an ungrounded answer.
    """
    if not query_text or not query_text.strip():
        return []

    try:
        raw = vector.query_corpus(query_text, n_results=n_results)
    except Exception:  # noqa: BLE001 — retrieval failure must not break the reply
        log.exception("corpus query failed; continuing with no passages")
        return []

    candidates = _parse_results(raw)
    relevant = [p for p in candidates if p.distance <= max_distance]
    log.info(
        "retrieval: %d candidate(s), %d above relevance threshold (max_distance=%.2f)",
        len(candidates), len(relevant), max_distance,
    )
    return relevant


def format_corpus_context(passages: list[Passage]) -> str:
    """Render retrieved passages into the ``{corpus_context}`` prompt slot text.

    Each passage is numbered and prefixed with its source label so the model can
    name the source it drew from. Returns ``""`` for an empty list, which the
    prompt assembler drops — so a turn that retrieved nothing carries no corpus
    block at all (and the model has nothing to (mis)quote). The hard grounding
    rule itself lives once, in :mod:`prompts.assembly`, beside the slot it governs.
    """
    if not passages:
        return ""
    blocks = [f"[{i}] {p.label()}\n{p.text.strip()}" for i, p in enumerate(passages, start=1)]
    return "\n\n".join(blocks)
