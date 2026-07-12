"""The trace substrate — the atom every later eval layer consumes.

:func:`trace_turn` drives the *real* chat pipeline over one input and returns a
:class:`Trace`: a versioned, replayable snapshot of how the context window was
assembled this turn. It deliberately reuses production code paths
(:mod:`engine.turn`, :mod:`memory.retrieval`, :mod:`memory.profile`,
:mod:`prompts.assembly`) rather than re-implementing retrieval, so the trace can
never drift from what the app actually does.

Two things the trace captures that the runtime ``meta`` frame does not (and that
later slices need):

* **Raw candidates before threshold filtering.** For the distance-gated sources
  (memory recall, corpus RAG) we also pull the full candidate pool with the
  threshold relaxed, tagging each candidate ``kept``/dropped against the real
  run. This is what lets the Slice-2 threshold sweep re-threshold offline with no
  re-embedding — the whole pool and its distances are already on the trace.
* **Provenance.** A content hash of the vault + profile + Chroma store, the
  embedding model, the seeded-data filter mode, and the threshold constants in
  force — so a trace (and any sweep built on it) is exactly reproducible.

Determinism note (see design doc §1): retrieval and assembly are model-free, but
:func:`intent.classifier.classify` calls the model on the *ambiguous residue*
(``method="model"``). The trace records ``intent_method`` as the stratum
discriminator, and ``trace_turn`` accepts ``force_intent`` so a caller that needs
a deterministic run can inject the intent instead of rolling the 2B classifier.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from pydantic import BaseModel, Field

import memory
from engine.turn import TurnState, assemble_context, check_in, classify
from intent.classifier import IntentResult
from memory import retrieval, vector
from prompts import assembly

# Bump when the Trace shape changes so stored traces can be migrated/rejected.
SCHEMA_VERSION = 1

# A generous distance ceiling that keeps *every* candidate (cosine distance runs
# 0..2), so a relaxed retrieval call returns the whole pool with its distances.
_KEEP_ALL_DISTANCE = 2.0


def _approx_tokens(text: str) -> int:
    """Rough token count (~4 chars/token). Labelled ``approx`` everywhere it is
    surfaced — a real tokenizer is a later refinement, not needed to reason about
    per-slot budget share."""
    return round(len(text) / 4)


def _sha16(*chunks: bytes) -> str:
    """Short (16-hex) content hash over the given byte chunks."""
    h = hashlib.sha256()
    for c in chunks:
        h.update(c)
    return h.hexdigest()[:16]


def _read_bytes(path: Path) -> bytes:
    """Read a file's bytes, or ``b""`` if it isn't there (a fresh vault)."""
    try:
        return path.read_bytes()
    except OSError:
        return b""


# ─────────────────────────────────────────────────────────────────────────────
# Trace schema (design doc §3). ``distance`` is optional because only the two
# vector-gated sources (memory, corpus) are cosine-ranked; recent episodes and
# profile slices are not distance-thresholded, so they carry no distance.
# ─────────────────────────────────────────────────────────────────────────────


class RetrievedItem(BaseModel):
    id: str
    label: str
    distance: float | None = None  # cosine distance (memory/corpus only)
    score: float | None = None     # post-weighting score where one exists
    kept: bool                     # entered the window this turn?


class SlotTrace(BaseModel):
    name: str                      # episodes | memory | profile | corpus
    gated_off: bool = False        # corpus on a non-retrieving (listen-first) turn
    candidates: list[RetrievedItem]  # RAW pool, pre-threshold (kept flag marks survivors)
    kept: list[RetrievedItem]      # what actually rendered into the window
    rendered_chars: int
    approx_tokens: int


class Provenance(BaseModel):
    vault_hash: str
    profile_snapshot_hash: str
    chroma_store_hash: str         # hash of chroma.sqlite3 — a cheap collection-version proxy
    embedding_model: str
    seeded_filter_mode: str
    thresholds: dict[str, float]
    intent_forced: bool


class Trace(BaseModel):
    schema_version: int = SCHEMA_VERSION
    provenance: Provenance
    input: str
    mode: str
    intent_label: str
    intent_method: str             # "rule" (deterministic) | "model" | "forced"
    corpus_gate_fired: bool
    slots: list[SlotTrace]
    system_prompt: str             # the VERBATIM assembled system prompt
    total_prompt_tokens: int       # approx, over the full messages sent to the model
    step_latency_ms: dict[str, float]
    reply: str | None = None
    citations: list[dict] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Provenance + candidate capture helpers.
# ─────────────────────────────────────────────────────────────────────────────


def _provenance(*, intent_forced: bool) -> Provenance:
    """Snapshot everything needed to replay this trace exactly."""
    vd = memory.vault_dir()
    db_bytes = _read_bytes(vd / "eva.db")
    profile_bytes = _read_bytes(vd / "profile.json")
    chroma_bytes = _read_bytes(vd / "chroma" / "chroma.sqlite3")
    return Provenance(
        vault_hash=_sha16(db_bytes, profile_bytes),
        profile_snapshot_hash=_sha16(profile_bytes),
        chroma_store_hash=_sha16(chroma_bytes),
        embedding_model=f"{vector.EMBED_MODEL} (asymmetric query-prefix)",
        seeded_filter_mode="recall excludes is_seeded=True",
        thresholds={
            "MAX_DISTANCE": retrieval.MAX_DISTANCE,
            "MEMORY_MAX_DISTANCE": retrieval.MEMORY_MAX_DISTANCE,
            "EPISODE_MAX_DISTANCE": retrieval.EPISODE_MAX_DISTANCE,
        },
        intent_forced=intent_forced,
    )


def _passage_key(passage: retrieval.Passage) -> str:
    """A stable identity for a corpus passage (it has no first-class id)."""
    return _sha16(
        f"{passage.source_file}|{passage.page}|{passage.section}|{passage.text}".encode()
    )


def _memory_candidates(text: str, kept_ids: set[str]) -> list[RetrievedItem]:
    """The full journal-recall candidate pool, threshold relaxed, kept-tagged."""
    pool = retrieval.recall_memories(
        text, max_distance=_KEEP_ALL_DISTANCE, top_k=10_000
    )
    return [
        RetrievedItem(
            id=m.entry_id,
            label=f"{m.date} ({m.chip_label()})",
            distance=round(m.distance, 4),
            kept=m.entry_id in kept_ids,
        )
        for m in pool
    ]


def _corpus_candidates(text: str, kept_keys: set[str]) -> list[RetrievedItem]:
    """The full corpus candidate pool, threshold relaxed, kept-tagged.

    Only meaningful on a retrieving turn — the caller passes this over only when
    the listen-first gate actually fired, matching what production ever pulls.
    """
    pool = retrieval.retrieve_corpus(text, max_distance=_KEEP_ALL_DISTANCE)
    return [
        RetrievedItem(
            id=_passage_key(p),
            label=p.label(),
            distance=round(p.distance, 4),
            kept=_passage_key(p) in kept_keys,
        )
        for p in pool
    ]


def _slot(
    name: str,
    rendered: str,
    kept: list[RetrievedItem],
    candidates: list[RetrievedItem] | None = None,
    *,
    gated_off: bool = False,
) -> SlotTrace:
    """Assemble one SlotTrace; candidates default to the kept set when a source
    isn't distance-gated (episodes/profile) and so has no wider pool."""
    return SlotTrace(
        name=name,
        gated_off=gated_off,
        candidates=candidates if candidates is not None else list(kept),
        kept=kept,
        rendered_chars=len(rendered),
        approx_tokens=_approx_tokens(rendered),
    )


# ─────────────────────────────────────────────────────────────────────────────
# The entry point.
# ─────────────────────────────────────────────────────────────────────────────


async def trace_turn(
    text: str,
    *,
    mode: str = assembly.DEFAULT_CHAT_MODE,
    history: list[dict] | None = None,
    force_intent: str | None = None,
    run_model: bool = False,
) -> Trace:
    """Run the real pipeline over ``text`` and return a full :class:`Trace`.

    ``force_intent`` injects a fixed intent label (``method="forced"``) instead of
    running the classifier — use it for deterministic scenarios so the retrieval
    under test isn't hostage to the 2B classifier's ambiguous-turn coin-flip.
    ``run_model=True`` additionally streams a reply so generation-facing slices can
    reuse the same trace; it requires a configured provider and is off by default.
    """
    state = TurnState(text=text, mode=mode, history=history or [])
    latency: dict[str, float] = {}

    t0 = time.perf_counter()
    if force_intent is not None:
        state.intent = IntentResult(label=force_intent, method="forced")
    else:
        await classify(state)
    latency["classify_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    await assemble_context(state)
    latency["assemble_context_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    await check_in(state)
    latency["check_in_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # The verbatim assembled system prompt (recomputed cleanly — check_in folds it
    # into the first message; this reproduces the exact same assembly in isolation).
    system_prompt = assembly.build_chat_system_prompt(
        mode=state.mode,
        persona_addendum=state.addendum,
        episodes_context=state.episodes_context,
        memory_context=state.memory_context,
        profile_slices=state.profile_slices,
        corpus_context=state.corpus_context,
    )

    gate_fired = bool(state.intent and state.intent.retrieves)

    # ── build the four context slots ──────────────────────────────────────────
    kept_memory_ids = {m.entry_id for m in state.memories}
    memory_kept = [
        RetrievedItem(
            id=m.entry_id,
            label=f"{m.date} ({m.chip_label()})",
            distance=round(m.distance, 4),
            kept=True,
        )
        for m in state.memories
    ]
    episodes_kept = [
        RetrievedItem(id=e.entry_id, label=e.date, kept=True) for e in state.episodes
    ]
    profile_kept = [
        RetrievedItem(
            id=s.id,
            label=f"{s.kind}: {s.text[:48]}",
            score=s.confidence,
            kept=True,
        )
        for s in state.profile_slice_items
    ]
    kept_passage_keys = {_passage_key(p) for p in state.passages}
    corpus_kept = [
        RetrievedItem(
            id=_passage_key(p), label=p.label(), distance=round(p.distance, 4), kept=True
        )
        for p in state.passages
    ]

    slots = [
        _slot("episodes", state.episodes_context, episodes_kept),
        _slot(
            "memory",
            state.memory_context,
            memory_kept,
            _memory_candidates(text, kept_memory_ids),
        ),
        _slot("profile", state.profile_slices, profile_kept),
        _slot(
            "corpus",
            state.corpus_context,
            corpus_kept,
            _corpus_candidates(text, kept_passage_keys) if gate_fired else [],
            gated_off=not gate_fired,
        ),
    ]

    reply: str | None = None
    citations: list[dict] = list(state.citations)
    if run_model:
        from llm import client as llm_client

        t0 = time.perf_counter()
        parts: list[str] = []
        async for piece in llm_client.stream_chat(state.messages):
            parts.append(piece)
        reply = "".join(parts)
        latency["reason_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    total_tokens = _approx_tokens("\n".join(m["content"] for m in state.messages))

    return Trace(
        provenance=_provenance(intent_forced=force_intent is not None),
        input=text,
        mode=mode,
        intent_label=state.intent.label if state.intent else "?",
        intent_method=state.intent.method if state.intent else "?",
        corpus_gate_fired=gate_fired,
        slots=slots,
        system_prompt=system_prompt,
        total_prompt_tokens=total_tokens,
        step_latency_ms=latency,
        reply=reply,
        citations=citations,
    )
