"""The chat-turn pipeline: six ordered steps over one :class:`TurnState`.

This is orchestration, not new behavior — each step delegates to the existing
collaborators (:mod:`intent.classifier`, :mod:`memory.retrieval`,
:mod:`memory.profile`, :mod:`prompts.assembly`, :mod:`safety.crisis_check`). The
listen-first gate stays structural: :func:`assemble_context` only ever calls
``retrieve_corpus`` when the intent retrieves, so a vent/process/ambient turn has
no corpus in its context window at all.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from intent import classifier as intent_classifier
from intent.classifier import IntentResult
from memory import profile, retrieval
from prompts import assembly
from safety import crisis_check

log = logging.getLogger("eva.engine.turn")

# The send callback the socket hands the engine so ``reason`` can stream frames
# without knowing anything about the websocket or its send-lock.
EmitFn = Callable[[dict], Awaitable[None]]


@dataclass
class TurnState:
    """Everything one chat turn accumulates, passed step to step.

    The socket sets ``text``/``mode``/``history``; the steps fill the rest. Keeping
    it one mutable object (rather than threading many return values) is what lets
    the pipeline read as ``classify → assemble_context → …`` at the call site.
    """

    text: str
    mode: str = assembly.DEFAULT_CHAT_MODE
    history: list[dict] = field(default_factory=list)

    # classify
    intent: IntentResult | None = None

    # check_in
    addendum: str = ""  # crisis-care text, when the input trips the keyword scan

    # assemble_context — context slots + the raw objects behind the chips
    episodes: list[retrieval.RecentEpisode] = field(default_factory=list)
    memories: list[retrieval.Memory] = field(default_factory=list)
    passages: list[retrieval.Passage] = field(default_factory=list)
    profile_slice_items: list[profile.ProfileSlice] = field(default_factory=list)
    episodes_context: str = ""
    memory_context: str = ""
    profile_slices: str = ""
    corpus_context: str = ""
    citations: list[dict] = field(default_factory=list)

    # check_in (prompt finalization) / reason
    messages: list[dict] = field(default_factory=list)
    reply_parts: list[str] = field(default_factory=list)

    @property
    def reply_text(self) -> str:
        """The reply assembled so far (or in full, after ``reason``)."""
        return "".join(self.reply_parts)

    def meta_frame(self) -> dict:
        """The ``meta`` frame: intent, persona, and what got retrieved this turn.

        Streamed to the UI so the chosen intent/persona is auditable and the
        Phase-21 debug panel has something to render. Counts come from the slots
        that were actually assembled, so they never over-claim.
        """
        return {
            "type": "meta",
            "intent": self.intent.label if self.intent else None,
            "method": self.intent.method if self.intent else None,
            "persona": self.mode,
            "retrieved": {
                "corpus": len(self.citations),
                "memory": len(self.memories),
                "episodes": len(self.episodes),
                "profile": len(self.profile_slice_items),
            },
        }


def _compose_messages(system_prompt: str, history: list[dict], user_text: str) -> list[dict]:
    """Build the OpenAI ``messages`` list, folding the system prompt into turn 1.

    The session ``history`` (alternating user/assistant turns) plus the new user
    turn form the body. Rather than send a separate ``system`` role — which the
    gemma-4 GGUF's embedded chat template does not accept — the system prompt is
    prepended to the *first* message in the window (always a user turn). This is
    template-agnostic and keeps Eva's persona present even after the oldest turns
    age out of the window.
    """
    turns = [*history, {"role": "user", "content": user_text}]
    first = turns[0]
    turns[0] = {**first, "content": f"{system_prompt}\n\n{first['content']}"}
    return turns


async def classify(state: TurnState) -> TurnState:
    """Step 1 — decide intent, which decides whether retrieval is even allowed."""
    state.intent = await intent_classifier.classify(state.text)
    return state


async def assemble_context(state: TurnState) -> TurnState:
    """Step 2 — build the context window under the listen-first gate.

    Recent L1 episodes, relevance recall, and profile slices are independent, so
    they run concurrently. Corpus retrieval is the one gated, dependent step: it
    runs *only* when ``intent.retrieves`` (ask_info/ask_advice). A vent/process/
    ambient turn never calls ``retrieve_corpus`` — the discipline is enforced here,
    by what reaches the window, not by the prompt.
    """
    recall_task = asyncio.create_task(asyncio.to_thread(retrieval.recall_memories, state.text))
    episodes_task = asyncio.create_task(asyncio.to_thread(retrieval.recent_episodes))

    retrieves = bool(state.intent and state.intent.retrieves)
    label = state.intent.label if state.intent else "?"
    method = state.intent.method if state.intent else "?"
    advice_mode = label == intent_classifier.ASK_ADVICE
    profile_task = asyncio.create_task(
        asyncio.to_thread(profile.retrieve_slices, state.text, advice_mode=advice_mode)
    )
    if retrieves:
        state.passages = await asyncio.to_thread(retrieval.retrieve_corpus, state.text)
        state.corpus_context = retrieval.format_corpus_context(state.passages)
        state.citations = [p.as_citation() for p in state.passages]
        log.info("intent=%s (%s) → retrieval fired, %d passage(s) cited",
                 label, method, len(state.citations))
    else:
        log.info("intent=%s (%s) → retrieval BYPASSED (listen-first)", label, method)

    # Relevance recall — "Eva remembers" — runs on every turn, independent of the
    # corpus gate (recalling the user's OWN entries is listening, not advice).
    state.memories = await recall_task
    state.memory_context = retrieval.format_memory_context(state.memories)
    if state.memories:
        log.info("recall fired → %d past entr(y/ies) in context (%s)",
                 len(state.memories), ", ".join(m.date for m in state.memories))

    # Recent L1 episodes — the chronological "lately" baseline. De-duplicated
    # against the relevance recall so the same entry is never injected twice.
    # The filter is applied to ``state.episodes`` itself (not just at render time)
    # so what the prompt carries and what ``meta.retrieved.episodes`` reports are
    # the same set — the audit contract can't over-report.
    recalled_ids = {m.entry_id for m in state.memories}
    state.episodes = [e for e in await episodes_task if e.entry_id not in recalled_ids]
    state.episodes_context = retrieval.format_episodes_context(state.episodes)

    state.profile_slice_items = await profile_task
    state.profile_slices = profile.format_slices(state.profile_slice_items)
    if state.profile_slice_items:
        log.info("profile → %d slice(s) in context", len(state.profile_slice_items))
    return state


async def check_in(state: TurnState) -> TurnState:
    """Step 3 — input guardrail seam, then finalize the guarded model input.

    Today the only rail is the interim keyword crisis-care scan; its addendum
    rides on the persona block so care travels with Eva's voice. This is where a
    Phase-20 out-of-scope/topic rail would also live. The final system prompt +
    ``messages`` are assembled here, after the addendum is known, so the reasoning
    step receives fully-prepared input.
    """
    if crisis_check.is_crisis(state.text):
        state.addendum = crisis_check.crisis_addendum()
    system_prompt = assembly.build_chat_system_prompt(
        mode=state.mode,
        persona_addendum=state.addendum,
        episodes_context=state.episodes_context,
        memory_context=state.memory_context,
        profile_slices=state.profile_slices,
        corpus_context=state.corpus_context,
    )
    state.messages = _compose_messages(system_prompt, state.history, state.text)
    return state


async def reason(
    state: TurnState,
    stream: AsyncIterator[str],
    *,
    emit: EmitFn,
    on_token: Callable[[str], Awaitable[None]] | None = None,
) -> TurnState:
    """Step 4 — stream the reply.

    ``stream`` is the model token iterator (the socket builds it so the client and
    its options stay in one place); each piece is appended to ``reply_parts``,
    emitted as a ``token`` frame, and — if voice is on — fed to ``on_token``. The
    socket wraps this call in its own try/except so a disconnect or model error is
    surfaced as a graceful frame and the voice worker is stopped; the engine stays
    out of that socket-lifecycle concern.
    """
    async for piece in stream:
        state.reply_parts.append(piece)
        await emit({"type": "token", "content": piece})
        if on_token is not None:
            await on_token(piece)
    return state


def _citation_key(citation: dict) -> tuple[Any, ...]:
    """A stable identity for a citation, for the check_out backing test."""
    return (
        citation.get("source_file"),
        citation.get("page"),
        citation.get("section"),
        citation.get("text"),
    )


def check_out(state: TurnState) -> TurnState:
    """Step 5 — output guardrail seam: no invented citations.

    Every surfaced citation must trace to a passage actually retrieved this turn.
    By construction they do (``assemble_context`` builds them from ``passages``),
    but formalizing the check here means a future change can't smuggle a citation
    the corpus never yielded onto the UI — a misattributed source is a real harm,
    not a glitch. Any unbacked citation is dropped and logged. This is the seam the
    Phase-20 rails (grounded-citation enforcement over Eva's generated text) extend.
    """
    allowed = {_citation_key(p.as_citation()) for p in state.passages}
    kept = [c for c in state.citations if _citation_key(c) in allowed]
    dropped = len(state.citations) - len(kept)
    if dropped:
        log.warning("check_out dropped %d unbacked citation(s)", dropped)
    state.citations = kept
    return state


def persist(state: TurnState) -> str:
    """Step 6 — hand the finished reply back for the caller to record.

    The conversation transcript and bounded session history live on the socket
    (it owns ``conv_id`` and the per-connection history), so persistence proper is
    the caller's; this step just returns the assembled reply text as the pipeline's
    result and marks the turn complete.
    """
    return state.reply_text
