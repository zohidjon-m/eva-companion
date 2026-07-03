"""Retrieval — the read loop's two lookups: corpus passages and journal memories.

This module owns both read-side retrievals that feed the chat prompt:

  * **Corpus retrieval** (Phase 7) — the read half of the RAG story. Given a
    message the intent gate decided is a ``question``/``advice_request``, it pulls
    the nearest corpus chunks, keeps those above a relevance threshold, and
    packages each with its source locator for a citation chip.
  * **Memory recall** (Phase 11) — "Eva remembers". On *every* turn it pulls the
    nearest past journal summaries from the ``journals`` collection, keeps those
    above a relevance threshold, recency-weights them, and returns the top few for
    the ``{memory_context}`` slot (see the §"Memory recall" block at the bottom).

The two are deliberately separate paths over separate collections: recall reads
the user's own past entries (part of *listening*, so it is not gated by intent),
while corpus retrieval reaches for the library (advice, gated by the listen-first
intent rule, §5.9). They never mix.

The corpus half is documented immediately below; memory recall has its own
section header further down.

Given a user message the intent gate has already decided is
a ``question`` or ``advice_request``, corpus retrieval pulls the nearest corpus
chunks, keeps only those above a relevance threshold, and packages each with the
source locator (file + page/section) that Phase 6 stored on the chunk.

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
from datetime import date

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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 11 — Memory recall ("Eva remembers").
#
# Every turn, recall the user's nearest past journal summaries from the L2
# ``journals`` collection and hand the top few to the ``{memory_context}`` slot so
# Eva can reference what the user told her before. Two disciplines shape this, both
# from EVA_MEMORY_ARCHITECTURE:
#
#   * **Code does the remembering, the model only narrates (§0.2).** Recall, the
#     relevance gate, and the recency weighting are deterministic here; the model
#     is merely *handed* the relevant past summaries and told to reference them
#     only if they fit. It is never asked to recall across history itself.
#   * **Never fabricate a memory (Phase 11 "Done when").** The distance threshold
#     is the honesty gate: a query unrelated to anything the user journaled clears
#     nothing, so ``recall_memories`` returns ``[]`` → no context block and no chip.
#     A memory chip therefore can only ever name a real past entry that was
#     genuinely close to what the user just said.
#
# Seeded demo data is already excluded upstream (``vector.recall`` filters
# ``is_seeded=False``), so the Phase-12 "seed never surfaces as a memory" rule
# holds here for free.
# ─────────────────────────────────────────────────────────────────────────────

# How many candidate summaries to pull from the collection before filtering. A few
# more than we keep, so recency weighting has room to reorder genuine matches.
MEMORY_N_RESULTS = 6

# How many memories survive to the prompt + chips. Kept small: a couple of on-point
# recollections read as "Eva remembers"; a wall of them reads as a search engine
# and bloats the chat context budget.
MEMORY_TOP_K = 3

# Maximum cosine distance for a past summary to count as a real memory. This is the
# anti-fabrication gate — everything above it is dropped, so an off-topic message
# surfaces no memory at all (Phase 11: "ask about something never journaled → no
# false memories").
#
# Tuned 2026-06-16 against a SMALL sample (4 real entries, bge-small) — re-check it
# on a fuller vault before trusting it long-term. The measured regimes were:
#   * a genuinely on-topic match           ~0.31–0.38
#   * vague recall ("what's on my mind",    ~0.42–0.46
#     "how have I been feeling lately")
#   * cross-topic bleed (an unrelated past  ~0.45–0.54
#     entry on a pointed query)
#   * a truly unrelated message's nearest   ~0.58–0.59
#     past entry
# 0.50 is chosen deliberately on the low side of that gap: for a journaling
# companion a wrong "I remember this" costs more trust than a quiet miss, so we
# favour the ~0.08 margin below the unrelated floor (~0.58) over squeezing out the
# last of vague-recall richness. It still clears the "what's on my mind lately"
# beat (its best matches land ≤0.46) while trimming most cross-topic bleed. Re-centre
# it the way the corpus MAX_DISTANCE was once there are more real summaries to measure.
MEMORY_MAX_DISTANCE = 0.50

# Recency weighting (§1 read loop: recent episodes are the "what's happening lately"
# baseline). Among memories that already passed the relevance gate, a more recent
# one is preferred — but relevance is never overridden by recency, only nudged by
# it. ``RECENCY_FLOOR`` is the fraction of a memory's relevance that always counts
# regardless of age; the remaining (1 - floor) fraction decays with age on a
# half-life. At 0.8 the recency lever spans only [0.8, 1.0] (a 1.25× swing), so a
# clearly more-relevant older memory still outranks a barely-relevant fresh one —
# recency breaks ties and surfaces "lately", it never manufactures a recent memory
# over a genuinely better-matching past one. Keeping this honest matters because the
# winner is what Eva references and what the chip names.
RECENCY_HALFLIFE_DAYS = 30.0
RECENCY_FLOOR = 0.8


@dataclass(frozen=True)
class Memory:
    """One recalled past journal summary plus what the chip and prompt need.

    ``date`` is the entry's day (``YYYY-MM-DD``); ``summary`` is the 4–5 sentence
    extraction summary that was embedded in Phase 2. ``distance`` is the cosine
    distance to the current message (kept for the threshold + diagnostics). ``mood``
    and ``themes`` ride along from the stored metadata for possible future use; the
    prompt block uses only date + summary today.
    """

    entry_id: str
    date: str
    summary: str
    mood: int | None
    themes: list[str]
    distance: float

    def chip_label(self) -> str:
        """A short human date for the "remembering …" chip, e.g. ``Jun 3``.

        Falls back to the raw ISO date if it can't be parsed, so a chip is never
        blank or crashes on a malformed stored date.
        """
        try:
            d = date.fromisoformat(self.date)
        except ValueError:
            return self.date
        return f"{d.strftime('%b')} {d.day}"

    def as_chip(self) -> dict:
        """Serialise to the ``memory`` frame payload the chat socket sends the UI.

        Only the date + label travel — the chip is a subtle "Eva remembered this
        day" affordance, not a way to re-read the entry (that is the journal browse
        screen's job). Keeping the summary server-side also avoids re-surfacing
        private past text in a transient UI chip.
        """
        return {"date": self.date, "label": self.chip_label()}


def _parse_memory_results(raw: dict) -> list[Memory]:
    """Turn ChromaDB's columnar recall result into a flat list of Memories.

    Mirrors :func:`_parse_results` (one query → index 0). ``themes`` was stored as
    a comma-joined string (Chroma metadata must be scalar; see
    :func:`vector.embed_summary`), so it is split back into a list here.
    """
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    ids = (raw.get("ids") or [[]])[0]

    memories: list[Memory] = []
    for i, (summary, meta, distance) in enumerate(zip(documents, metadatas, distances)):
        meta = meta or {}
        themes_raw = meta.get("themes") or ""
        themes = [t.strip() for t in themes_raw.split(",") if t.strip()]
        entry_id = meta.get("entry_id") or (ids[i] if i < len(ids) else "")
        memories.append(
            Memory(
                entry_id=entry_id,
                date=meta.get("date", ""),
                summary=summary or "",
                mood=meta.get("mood"),
                themes=themes,
                distance=float(distance),
            )
        )
    return memories


def _recency_weighted_score(memory: Memory, ref: date) -> float:
    """Score a *relevant* memory for ranking: relevance, nudged by recency.

    ``relevance`` maps cosine distance (0 = identical … 2 = opposite) to 0..1.
    Recency is a half-life decay on the memory's age; ``RECENCY_FLOOR`` guarantees
    a baseline so recency only reorders genuinely-relevant memories rather than
    burying an old-but-on-point one. A memory whose stored date can't be parsed is
    scored as if maximally old (floor only) rather than crashing the recall path.
    """
    relevance = max(0.0, 1.0 - memory.distance / 2.0)
    try:
        age_days = max(0, (ref - date.fromisoformat(memory.date)).days)
        recency = 0.5 ** (age_days / RECENCY_HALFLIFE_DAYS)
    except ValueError:
        recency = 0.0
    return relevance * (RECENCY_FLOOR + (1.0 - RECENCY_FLOOR) * recency)


def recall_memories(
    query_text: str,
    *,
    n_results: int = MEMORY_N_RESULTS,
    max_distance: float = MEMORY_MAX_DISTANCE,
    top_k: int = MEMORY_TOP_K,
    today: date | None = None,
) -> list[Memory]:
    """Return the user's past journal memories relevant to ``query_text``.

    Pulls candidate summaries from the ``journals`` collection, drops anything
    above ``max_distance`` (the anti-fabrication gate — an unrelated message keeps
    nothing), recency-weights the survivors, and returns at most ``top_k``, best
    first. ``today`` is injectable so recency is testable without a real clock.

    Returns ``[]`` — never raises — when the query is empty, nothing clears the
    threshold, or the vector store errors. Recall failing soft is deliberate: a
    missing memory is a non-event, but a crash or a fabricated one is not. Seeded
    demo data is excluded upstream by :func:`vector.recall`.
    """
    if not query_text or not query_text.strip():
        return []

    try:
        raw = vector.recall(query_text, n_results=n_results)
    except Exception:  # noqa: BLE001 — recall failure must not break the reply
        log.exception("journal recall failed; continuing with no memories")
        return []

    candidates = _parse_memory_results(raw)
    relevant = [m for m in candidates if m.distance <= max_distance]
    if not relevant:
        log.info(
            "recall: %d candidate(s), none above relevance threshold (max_distance=%.2f)",
            len(candidates), max_distance,
        )
        return []

    ref = today or date.today()
    ranked = sorted(relevant, key=lambda m: _recency_weighted_score(m, ref), reverse=True)
    kept = ranked[:top_k]
    log.info(
        "recall: %d candidate(s), %d relevant, %d kept (%s)",
        len(candidates), len(relevant), len(kept), ", ".join(m.date for m in kept),
    )
    return kept


def format_memory_context(memories: list[Memory]) -> str:
    """Render recalled memories into the ``{memory_context}`` prompt slot text.

    Each memory is prefixed with its date so Eva can say *when* something happened
    ("back in early June you mentioned…"). Returns ``""`` for an empty list, which
    the assembler drops — so a turn that recalled nothing carries no memory block,
    and the model has no past entry to (mis)reference. The "reference only if
    relevant" framing lives once, in the slot header in :mod:`prompts.assembly`.
    """
    if not memories:
        return ""
    blocks = [f"[{m.date}] {m.summary.strip()}" for m in memories]
    return "\n\n".join(blocks)


# ─────────────────────────────────────────────────────────────────────────────
# Episodes recall (R4). Semantic lookup over open loops + notable events (the
# ``episodes`` collection), so "the thing I never resolved" or "the day X
# happened" can be surfaced on its own terms. This provides the read-side seam;
# the conversation engine (R6/R9) decides when to call it and how to render it.
# The same anti-fabrication contract as journal recall holds: nothing relevant
# means an empty list, never an invented episode.
# ─────────────────────────────────────────────────────────────────────────────

# Pull a few candidates, keep a small on-point set — same rationale as the
# journal-recall knobs above.
EPISODE_N_RESULTS = 6
EPISODE_TOP_K = 3

# Anti-fabrication gate for episode units. Provisional: mirrors
# ``MEMORY_MAX_DISTANCE`` deliberately — the plan defers retuning thresholds until
# there are real episode embeddings to measure against, so this rides the journal
# gate rather than guessing a separate number. Re-centre once measured.
EPISODE_MAX_DISTANCE = 0.50


@dataclass(frozen=True)
class Episode:
    """One recalled episodic unit — an open loop or a notable event.

    ``type`` is ``"open_loop"`` or ``"event"`` (the discriminator stored in the
    ``episodes`` collection); ``text`` is the embedded unit text. ``distance`` is
    the cosine distance to the query (kept for the threshold + diagnostics).
    ``date``/``mood``/``themes`` ride along from the entry the unit came from.
    """

    entry_id: str
    date: str
    type: str
    text: str
    mood: int | None
    themes: list[str]
    distance: float

    def chip_label(self) -> str:
        """A short human date for a recall chip, e.g. ``Jun 3`` (falls back to ISO)."""
        try:
            d = date.fromisoformat(self.date)
        except ValueError:
            return self.date
        return f"{d.strftime('%b')} {d.day}"

    def as_chip(self) -> dict:
        """Serialise to the payload a future episodes chip would send the UI."""
        return {"date": self.date, "label": self.chip_label(), "type": self.type}


def _parse_episode_results(raw: dict) -> list[Episode]:
    """Turn ChromaDB's columnar episodes result into a flat list of Episodes.

    Mirrors :func:`_parse_memory_results` (one query → index 0). ``themes`` was
    stored comma-joined (Chroma metadata must be scalar; see
    :func:`vector.embed_episodes`), so it is split back into a list here.
    """
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    ids = (raw.get("ids") or [[]])[0]

    episodes: list[Episode] = []
    for i, (text, meta, distance) in enumerate(zip(documents, metadatas, distances)):
        meta = meta or {}
        themes_raw = meta.get("themes") or ""
        themes = [t.strip() for t in themes_raw.split(",") if t.strip()]
        entry_id = meta.get("entry_id") or (ids[i] if i < len(ids) else "")
        episodes.append(
            Episode(
                entry_id=entry_id,
                date=meta.get("date", ""),
                type=meta.get("type", ""),
                text=text or "",
                mood=meta.get("mood"),
                themes=themes,
                distance=float(distance),
            )
        )
    return episodes


def recall_episodes(
    query_text: str,
    *,
    n_results: int = EPISODE_N_RESULTS,
    max_distance: float = EPISODE_MAX_DISTANCE,
    top_k: int = EPISODE_TOP_K,
) -> list[Episode]:
    """Return the open loops / notable events relevant to ``query_text``.

    Pulls candidate units from the ``episodes`` collection, drops anything above
    ``max_distance`` (the anti-fabrication gate), and returns the closest
    ``top_k``. Unlike journal recall this is not recency-weighted: an open loop's
    value is often that it is *old and unresolved*, so relevance alone ranks it
    (recency shaping is deferred with the rest of the read-loop work).

    Returns ``[]`` — never raises — when the query is empty, nothing clears the
    threshold, or the vector store errors. Seeded demo data is excluded upstream
    by :func:`vector.recall_episodes`.
    """
    if not query_text or not query_text.strip():
        return []

    try:
        raw = vector.recall_episodes(query_text, n_results=n_results)
    except Exception:  # noqa: BLE001 — recall failure must not break the reply
        log.exception("episode recall failed; continuing with no episodes")
        return []

    candidates = _parse_episode_results(raw)
    relevant = [e for e in candidates if e.distance <= max_distance]
    if not relevant:
        log.info(
            "episode recall: %d candidate(s), none above threshold (max_distance=%.2f)",
            len(candidates), max_distance,
        )
        return []

    kept = sorted(relevant, key=lambda e: e.distance)[:top_k]
    log.info(
        "episode recall: %d candidate(s), %d relevant, %d kept",
        len(candidates), len(relevant), len(kept),
    )
    return kept
