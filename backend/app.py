"""Eva backend — FastAPI application entry point (Phase 0 scaffold).

This is the spine the rest of the app hangs off of. In Phase 0 it does three
things and nothing more:

1. Installs the outbound network guard (privacy hard law) before anything else.
2. Serves ``GET /health`` so the desktop shell can show a live status dot.
3. Enables CORS for the local frontend origins so the Tauri/Vite UI can poll
   /health during development.

The backend is spawned by the Tauri shell as a sidecar (see EVA_SYSTEM_DESIGN
§4). It listens on 127.0.0.1:8000 only — never a public interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Install the network guard at import time, before any networking library can
# run. Importing app.py is enough to make Eva offline-safe.
from net_guard import allow_summary, install_net_guard, is_installed

# Phase 2 capture pipeline (vault + L1 index + background extraction/embedding).
# Phase 5 also reads the L1 index (db) and the L0 day files (vault) directly for
# the journal browse / read-only day view.
from memory import capture, db, vault, vault_dir

# Phase 13 L3 profile (the seam): what Eva understands about the user. Read into
# the {profile_slices} chat slot every turn; the Profile screen reads/edits it via
# GET/PUT /profile (profile.md ↔ profile.json sync). # DEMO-STUB until the L3 engine.
from memory import profile

# Phase 7 RAG: the intent gate (listen-first — only question/advice_request pull
# the corpus) and corpus retrieval (relevant, cited passages or nothing).
from intent import classifier as intent_classifier
from memory import retrieval

# Phase 1 LLM runtime: the model-server supervisor and the async client.
from llm import client as llm_client
from llm import server as llm_server

# Phase 4 chat surface: prompt assembly (the four template slots) + the interim
# keyword crisis-care floor that runs before the prompt reaches the model.
from prompts import assembly
from safety import crisis_check

# Phase 6 Library: corpus ingestion (save → load → chunk → embed → index) and the
# document manifest the Library screen lists from.
from ingest import corpus as corpus_ingest

# Phase 8 Voice-in: push-to-talk STT (faster-whisper, lazy-loaded on first /stt)
# and the single settings store whose whisper-size knob it reads.
# Phase 9 Voice-out: Kokoro TTS (lazy-loaded on the first voiced turn) driven by
# the streaming sentence queue, which splits Eva's reply at §7.5 boundaries and
# emits ordered audio frames over this same /chat socket alongside the text.
import settings as app_settings
from voice import stt, tts
from voice.sentence_queue import VoiceStream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("eva.app")

install_net_guard()

# Port the backend listens on (kept in one place; the shell + dev.sh use it).
BACKEND_PORT = 8000

# The supervised model server. §4: the backend owns the model server's lifecycle.
_llama = llm_server.LlamaServer()
_supervisor_task: asyncio.Task | None = None


def _autostart_enabled() -> bool:
    """Whether the backend should launch the model server on startup.

    Off by default so importing the app (tests, ``python app.py``) stays light and
    never blocks on a multi-GB model load. ``dev.sh`` / the shell set
    ``EVA_START_LLAMA=1`` to have the backend own the server as a sidecar.
    """
    return os.environ.get("EVA_START_LLAMA", "").strip().lower() in {"1", "true", "yes"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start/stop the model server alongside the backend (when autostart is on).

    Launch is non-blocking: ``wait_ready`` and ``supervise`` run as background
    tasks so the HTTP server is up immediately and ``/chat`` simply reports a
    not-ready/missing-model error until the model finishes loading. A missing GGUF
    never crashes startup — :meth:`LlamaServer.start` returns ``False`` gracefully.
    """
    global _supervisor_task
    if _autostart_enabled():
        if _llama.start():
            asyncio.create_task(_llama.wait_ready())
            _supervisor_task = asyncio.create_task(_llama.supervise())
    try:
        yield
    finally:
        if _supervisor_task is not None:
            _supervisor_task.cancel()
        _llama.stop()


app = FastAPI(title="Eva backend", version="0.1.0-phase1", lifespan=lifespan)

# The frontend runs from the Vite dev server (and, when packaged, the Tauri
# webview) on localhost. Allow exactly those local origins — not the wider web.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    """Liveness/readiness probe consumed by the shell's status dot.

    Reports ``model_present`` — the real check for the Gemma GGUF on disk — plus a
    ``model`` block (expected path, endpoint, and a download hint if it is
    missing) so the shell can guide first-run setup without crashing. The model
    server may still be loading even when present, so ``model_server_running``
    reflects whether the supervised subprocess is alive. ``net_guard`` and its
    detail (incl. how many outbound calls have been blocked this run) feed the
    Offline ✓ badge. ``voices`` reports whether the STT/TTS weights are already
    cached, so the Phase-10 first-run setup screen can show a live "ready ✓".
    """
    status = llm_server.model_status()
    return {
        "status": "ok",
        "model_present": status["model_present"],
        "model": status,
        "model_server_running": _llama.is_running(),
        "net_guard": is_installed(),
        "net_guard_detail": allow_summary(),
        "voices": {"stt": stt.weights_present(), "tts": tts.weights_present()},
    }


def _parse_frame(raw: str) -> tuple[str, bool, bool]:
    """Parse a ``/chat`` frame into ``(text, capture, voice)``.

    Accepts a bare string or a JSON object
    ``{"text": "...", "capture": true, "voice": false}`` so the socket stays
    friendly to a quick curl/ws test while letting the frontend control two flags:

    * ``capture`` (default ``True``) — every normal turn is saved; the UI sets it
      ``False`` only when *retrying* a turn whose user text was already persisted,
      so a retry never duplicates the journal entry.
    * ``voice`` (default ``False``) — whether to synthesize Eva's reply to speech
      for this turn (Phase 9). The UI sets it from the top-bar voice toggle; a bare
      text frame (or any client that doesn't ask) gets text only, so the heavy TTS
      path is opt-in per turn and the 8 GB budget is respected when voice is off.

    Returns the stripped text (possibly empty) with both flags resolved.
    """
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return raw, True, False
        if isinstance(obj, dict):
            text = str(obj.get("text") or obj.get("message") or "").strip()
            return text, bool(obj.get("capture", True)), bool(obj.get("voice", False))
    return raw, True, False


# How many prior turns of the *current session* to carry into the model so a
# multi-turn chat stays coherent. 12 = 6 user + 6 Eva turns, comfortably inside
# the 8192-token chat budget (CLAUDE.md) for short companion replies. Older turns
# fall out of the model's view but remain in the vault and on screen.
_MAX_HISTORY_TURNS = 12


def _capture_user_turn(text: str) -> None:
    """Persist one user chat turn (vault + L1) and schedule background extraction.

    Reuses the Phase-2 capture pipeline verbatim, so a chat turn is saved exactly
    like a ``POST /entry``: L0 Markdown first (the source of truth), then the L1
    index, then extraction + embedding in the background. Capture runs *before*
    generation so the entry survives a missing model or a reply interrupted
    mid-stream. A failure here is logged but never breaks the live reply — the
    conversation matters more than the derived index, and the L0 write is the part
    that effectively never fails.

    Note: only the *user's* turns are captured. The vault is the user's journal
    and the L1 extractor reads the user's words; Eva's replies live in the session
    history and the model context, not as journal entries.
    """
    try:
        rec = capture.capture_entry(text, "chat")
    except Exception:  # noqa: BLE001 — capture must never break the reply
        log.exception("failed to capture chat turn (continuing with reply)")
        return
    asyncio.create_task(capture.run_extraction_and_embed(rec.id, rec.text, rec.date))


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


@app.websocket("/chat")
async def chat_ws(ws: WebSocket) -> None:
    """Streaming chat socket: receive a turn, stream Eva's tokens back.

    Protocol (one turn, repeatable on the same connection):
      → client sends a frame (bare text or ``{"text": "...", "capture": bool}``)
      ← ``{"type":"start"}`` then a sequence of ``{"type":"token","content":…}``
      ← ``{"type":"done"}`` when the reply completes
      ← ``{"type":"error","code":…,"message":…}`` on any failure

    Phase 4 wraps the Phase-1 stream with the real chat surface; Phase 7 adds the
    listen-first RAG gate to it:
      1. Capture the user's turn (vault + L1) before anything else.
      2. Run the interim keyword crisis-care scan over the user's text.
      3. Classify intent (vent/question/advice_request). Only question and
         advice_request pull corpus passages; vent bypasses retrieval entirely so
         Eva literally has nothing to advise from (EVA_MEMORY_ARCHITECTURE §5.9).
         (Phase 11) Independently, recall the nearest past journal summaries above
         a relevance threshold (recency-weighted) into the memory slot — on every
         turn, since remembering the user's own entries is listening, not advice.
      4. Assemble the system prompt from the four template slots
         (:mod:`prompts.assembly`), appending the crisis addendum on a match and
         the retrieved passages (with the grounding rule) in the corpus slot.
      5. If passages were retrieved, send a ``citations`` frame so the UI can show
         source chips; then stream the reply, capped at 450 tokens, with this
         session's history for multi-turn coherence.
      6. (Phase 9) If the turn asked for voice, a :class:`VoiceStream` splits the
         streamed reply at §7.5 sentence boundaries and emits ordered ``audio``
         frames over this same socket alongside the text; ``audio_done`` follows
         the text ``done`` once the last sentence has been synthesized.

    The model server going down (even mid-reply) or still loading is surfaced as a
    graceful error frame, never an unhandled crash. ``history`` is per-connection:
    it lives only for the life of this socket.
    """
    await ws.accept()
    history: list[dict] = []

    # All sends on this socket are serialized through one lock. The Phase-9
    # VoiceStream worker emits audio frames from a separate coroutine while the main
    # loop writes text tokens; without the lock the two could interleave mid-frame
    # on the single WebSocket. Every send below goes through ``emit``.
    send_lock = asyncio.Lock()

    async def emit(frame: dict) -> None:
        async with send_lock:
            await ws.send_json(frame)

    try:
        while True:
            text, do_capture, want_voice = _parse_frame(await ws.receive_text())
            if not text:
                await emit(
                    {"type": "error", "code": "empty", "message": "empty message"}
                )
                continue

            # 1. Capture first — an absent model or a dropped reply never costs the
            #    entry. Retries (capture=False) skip this; their text is already saved.
            if do_capture:
                _capture_user_turn(text)

            if not llm_server.model_present():
                status = llm_server.model_status()
                await emit(
                    {
                        "type": "error",
                        "code": "model_missing",
                        "message": status.get("hint", "model not available"),
                        "model": status,
                    }
                )
                continue

            # 2. Interim crisis-care scan.
            addendum = crisis_check.crisis_addendum() if crisis_check.is_crisis(text) else ""

            # 3. Listen-first intent gate, then retrieval ONLY on a retrieving
            #    intent. A vent turn never queries the corpus — the discipline is
            #    structural (§5.9), not a prompt plea — and is logged either way so
            #    the bypass is visible in the demo logs.
            intent = await intent_classifier.classify(text)
            corpus_context, citations = "", []
            if intent.retrieves:
                passages = retrieval.retrieve_corpus(text)
                corpus_context = retrieval.format_corpus_context(passages)
                citations = [p.as_citation() for p in passages]
                log.info(
                    "intent=%s (%s) → retrieval fired, %d passage(s) cited",
                    intent.label, intent.method, len(citations),
                )
            else:
                log.info(
                    "intent=%s (%s) → retrieval BYPASSED (listen-first)",
                    intent.label, intent.method,
                )

            # 3b. (Phase 11) Memory recall — "Eva remembers". Runs on EVERY turn,
            #     independent of the listen-first gate above: recalling the user's
            #     OWN past entries is part of listening, not advice (the gate only
            #     governs the library/corpus). Code does the remembering and the
            #     relevance gate (recall_memories); the model is only handed the
            #     relevant summaries. The threshold means an unrelated message
            #     surfaces nothing, so Eva never fabricates a memory or a chip.
            #     The current turn was captured above, but its embedding runs in the
            #     background (slow extraction first), so it cannot recall itself.
            memories = retrieval.recall_memories(text)
            memory_context = retrieval.format_memory_context(memories)
            if memories:
                log.info(
                    "recall fired → %d past entr(y/ies) in context (%s)",
                    len(memories), ", ".join(m.date for m in memories),
                )

            # 3c. (Phase 13) Profile slices — what Eva understands about the user.
            #     Read on EVERY turn through the L3 seam (memory.profile): the core
            #     (who they are, their goals, their baseline) is always included so
            #     a reply can reference a stated goal unprompted, and topic-relevant
            #     patterns/loops are folded in when the message touches them. Returns
            #     "" when there is no profile (deleted profile.json → no slot, no
            #     crash), so this is a one-line fill of an already-present slot.
            profile_slices = profile.slices_for_prompt(text)
            if profile_slices:
                log.info(
                    "profile → %d slice(s) in context",
                    profile_slices.count("\n") + 1,
                )

            # 4. Assemble the system prompt with whatever slots are populated.
            system_prompt = assembly.build_chat_system_prompt(
                persona_addendum=addendum,
                memory_context=memory_context,
                profile_slices=profile_slices,
                corpus_context=corpus_context,
            )
            messages = _compose_messages(system_prompt, history, text)

            await emit({"type": "start"})
            # 5. Surface citations (if any) before the tokens, so the chips can
            #    render alongside the bubble. No frame when nothing was retrieved —
            #    a "not in your library" answer therefore carries no chips at all.
            if citations:
                await emit({"type": "citations", "citations": citations})
            # 5b. (Phase 11) Surface which past days Eva is remembering, so the demo
            #     audience SEES recall happen as a subtle chip. Only sent when real
            #     memories cleared the threshold — no memory, no chip, ever.
            if memories:
                await emit(
                    {"type": "memory", "memories": [m.as_chip() for m in memories]}
                )

            # 6. (Phase 9) When this turn asked for voice, spin up a VoiceStream: it
            #    feeds each token into the §7.5 sentence splitter and synthesizes
            #    completed sentences on a worker, emitting ordered ``audio`` frames
            #    over this socket while the text keeps streaming. Voice is per-turn
            #    and lazy — no Kokoro load happens unless want_voice is set.
            voice = VoiceStream(tts.synthesize, emit) if want_voice else None
            reply_parts: list[str] = []
            try:
                async for piece in llm_client.stream_chat(
                    messages, max_tokens=assembly.REPLY_MAX_TOKENS, priority=True
                ):
                    reply_parts.append(piece)
                    await emit({"type": "token", "content": piece})
                    if voice is not None:
                        await voice.feed(piece)
                # Text is complete: tell the UI to drop the streaming cursor now,
                # before we wait on any still-synthesizing audio.
                await emit({"type": "done"})
                if voice is not None:
                    # Drain the splitter's final sentence(s), let the worker finish
                    # synthesizing, then signal that no more audio will arrive.
                    await voice.finish()
                    await emit({"type": "audio_done"})
            except WebSocketDisconnect:
                if voice is not None:
                    voice.stop()
                    await voice.finish()
                raise
            except Exception as exc:  # noqa: BLE001 — surface as a graceful frame
                log.exception("chat stream failed")
                if voice is not None:
                    voice.stop()  # skip pending synthesis; the turn is aborting
                    await voice.finish()
                await emit(
                    {"type": "error", "code": "model_error", "message": str(exc)}
                )
                continue  # don't record a half/failed turn into history

            # 4. Record the completed turn for in-session coherence, bounded.
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": "".join(reply_parts)})
            if len(history) > _MAX_HISTORY_TURNS:
                del history[:-_MAX_HISTORY_TURNS]
    except WebSocketDisconnect:
        log.info("chat websocket disconnected")


class EntryIn(BaseModel):
    """Request body for ``POST /entry`` — one captured turn."""

    text: str = Field(..., min_length=1, description="The full entry/turn text.")
    type: str = Field("chat", description="'chat' or 'journal'.")


@app.post("/entry")
def create_entry(body: EntryIn, background: BackgroundTasks) -> dict:
    """Capture one entry and kick off background extraction.

    This is the model-agnostic capture endpoint that stands in until Phase 4 wires
    the real `/chat` WebSocket and Phase 5 the journal save — both of which will
    reuse the same ``capture`` pipeline. The entry is persisted synchronously (so
    the response means "saved"); extraction + embedding run after the response via
    FastAPI background tasks, so a slow or absent model never blocks the save.
    """
    try:
        rec = capture.capture_entry(body.text, body.type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    background.add_task(capture.run_extraction_and_embed, rec.id, rec.text, rec.date)
    return {"id": rec.id, "date": rec.date, "type": rec.type, "word_count": rec.word_count}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Journaling surface. Journaling is its own ritual, not a chat thread,
# so it gets its own small API. Saving reuses the Phase-2 capture pipeline
# verbatim (entry type 'journal', same extraction), the browse list and day view
# read from the L1 index and the L0 Markdown respectively, and the post-save
# acknowledgment is one bounded model call in Eva's voice.
# ─────────────────────────────────────────────────────────────────────────────

# A single-line preview is enough for the browse list; trim long entries.
_PREVIEW_CHARS = 140

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _preview(text: str | None) -> str:
    """Collapse an entry to a short single-line teaser for the browse list."""
    if not text:
        return ""
    flat = " ".join(text.split())
    return flat if len(flat) <= _PREVIEW_CHARS else flat[: _PREVIEW_CHARS - 1].rstrip() + "…"


class JournalIn(BaseModel):
    """Request body for ``POST /journal`` — one saved journal entry."""

    text: str = Field(..., min_length=1, description="The journal entry text.")


@app.post("/journal")
def save_journal(body: JournalIn, background: BackgroundTasks) -> dict:
    """Save a journal entry and kick off the same background extraction as chat.

    Writes through the Phase-2 capture pipeline with entry type ``journal`` — L0
    Markdown first (durable the moment this returns), then the L1 index, then
    extraction + embedding in the background. The response means "saved"; Eva's
    acknowledgment is a separate, non-blocking call (:func:`acknowledge_journal`)
    so a slow or absent model never delays the save confirmation.
    """
    try:
        rec = capture.capture_entry(body.text, "journal")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    background.add_task(capture.run_extraction_and_embed, rec.id, rec.text, rec.date)
    return {
        "id": rec.id,
        "date": rec.date,
        "word_count": rec.word_count,
        "created_at": rec.created_at,
    }


@app.get("/journal/days")
def journal_days() -> dict:
    """List the days that have journal entries, newest first (the browse list).

    Primary source is the L1 index (fast, gives counts + a recent preview). It is
    then unioned with any day files on disk that the index doesn't know about —
    e.g. an older entry placed by hand — so the Markdown source of truth is never
    invisible in the browse list even before it is (re)indexed.
    """
    conn = db.get_or_create_db()
    try:
        rows = db.list_journal_days(conn)
    finally:
        conn.close()

    by_date: dict[str, dict] = {
        r["date"]: {"date": r["date"], "count": r["count"], "preview": _preview(r["preview"])}
        for r in rows
    }
    for date in vault.list_day_dates():
        if date in by_date:
            continue
        turns = [t for t in vault.read_day(date) if t.type == "journal"]
        if turns:
            by_date[date] = {
                "date": date,
                "count": len(turns),
                "preview": _preview(turns[-1].text),
            }

    days = sorted(by_date.values(), key=lambda d: d["date"], reverse=True)
    return {"days": days}


@app.get("/journal/day/{date}")
def journal_day(date: str) -> dict:
    """Return one day's journal entries for the read-only day view.

    Reads the L0 Markdown day file directly (the source of truth), so a
    hand-placed older file renders correctly without any index entry. Returns the
    journal turns in written order; 404 if the day has no journal entries.
    """
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    turns = [t for t in vault.read_day(date) if t.type == "journal"]
    if not turns:
        raise HTTPException(status_code=404, detail="no journal entries for that day")
    return {
        "date": date,
        "entries": [{"id": t.id, "time": t.time, "text": t.text} for t in turns],
    }


class AcknowledgeIn(BaseModel):
    """Request body for ``POST /journal/acknowledge`` — which entry to reflect on."""

    entry_id: str = Field(..., description="The id returned by POST /journal.")


def _entry_text(entry_id: str) -> str | None:
    """Fetch a saved entry's text from the L1 index, or ``None`` if unknown.

    Uses ``get_or_create_db`` so a lookup against a brand-new vault (no entries
    yet) returns ``None`` rather than erroring on a missing table.
    """
    conn = db.get_or_create_db()
    try:
        row = db.get_entry(conn, entry_id)
    finally:
        conn.close()
    return row["text"] if row else None


async def _journal_acknowledgment(entry_text: str) -> str | None:
    """Produce Eva's one-line acknowledgment for a saved entry, or ``None``.

    One bounded, non-streamed model call using the persona-based journal prompt
    (:func:`prompts.assembly.build_journal_ack_prompt`). Returns ``None`` — never
    raises — when the model is missing or the call fails, so the acknowledgment is
    a gentle bonus that can be absent without ever putting the save at risk.
    Gemma has no system role, so the instruction is folded into the user message
    exactly as the chat path does.
    """
    if not llm_server.model_present():
        return None
    system_prompt = assembly.build_journal_ack_prompt()
    messages = [{"role": "user", "content": f"{system_prompt}\n\n{entry_text}"}]
    try:
        # A slightly cooler temperature than chat keeps the reflection grounded in
        # what they wrote rather than wandering. priority=True so the waiting user
        # isn't stuck behind this entry's own background extraction.
        reply = await llm_client.complete_chat(
            messages,
            max_tokens=assembly.JOURNAL_ACK_MAX_TOKENS,
            temp=0.7,
            priority=True,
        )
    except Exception:  # noqa: BLE001 — acknowledgment is best-effort, never fatal
        log.exception("journal acknowledgment failed (entry still saved)")
        return None
    line = " ".join(reply.split()).strip()
    return line or None


@app.post("/journal/acknowledge")
async def acknowledge_journal(body: AcknowledgeIn) -> dict:
    """Return one gentle Eva acknowledgment line for a just-saved journal entry.

    Looked up by entry id (so the client can't smuggle in different text than was
    saved). ``acknowledgment`` is ``null`` when the model is unavailable or the
    call fails — the entry is already safely saved either way.
    """
    text = _entry_text(body.entry_id)
    if text is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return {"acknowledgment": await _journal_acknowledgment(text)}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 13 — Profile. The user can read and correct what Eva understands about
# them. GET returns the human-readable profile.md rendering (the structured truth
# lives in profile.json); PUT saves an edited rendering, running the lenient
# profile.md → profile.json sync (§7.2) that turns edits into user-anchored
# corrections. Both go through the L3 seam (memory.profile); the real engine will
# write profile.json without changing these endpoints. # DEMO-STUB.
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/profile")
def get_profile_md() -> dict:
    """Return the profile for the Profile screen to render, or an absent state.

    ``present`` is ``False`` (with ``markdown: null``) when there is no profile —
    a fresh vault, or a deleted ``profile.json`` — so the screen shows its
    "Eva is still getting to know you" empty state instead of erroring.
    """
    markdown = profile.read_markdown()
    return {"present": markdown is not None, "markdown": markdown}


class ProfilePut(BaseModel):
    """Request body for ``PUT /profile`` — the edited ``profile.md`` text."""

    markdown: str = Field(..., description="The full edited profile.md text.")


@app.put("/profile")
def put_profile_md(body: ProfilePut) -> dict:
    """Save an edited ``profile.md`` and return the re-rendered profile + warnings.

    Runs the §7.2 sync: the edited Markdown is parsed against the current profile,
    each understood change is applied (text edits to existing claims become
    user-anchored corrections), and both ``profile.json`` and ``profile.md`` are
    rewritten. ``warnings`` lists any section that couldn't be applied (it was left
    unchanged) so the UI can tell the user. 404 when there is no profile to edit.
    """
    try:
        markdown, warnings = profile.save_markdown(body.markdown)
    except profile.NoProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"present": True, "markdown": markdown, "warnings": warnings}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 12 — Insights: mood capture + chart. The first real Insights block.
#
# GET /insights/mood is pure SQL over the denormalised mood_series table — no LLM
# (the moods were extracted at capture time in Phase 2). It honours §7.1's recall
# rule: live data only (is_seeded = 0) by default, with ?include_seeded=true for
# the demo chart so the backdated seed month (scripts/seed_demo.py) is shown. NULL
# mood is preserved as null all the way to the UI, which renders it as a gap in
# the line — never as zero.
# ─────────────────────────────────────────────────────────────────────────────


def _decode_emotions(raw: str | None) -> list:
    """Decode the stored emotions JSON into a list, tolerating bad/empty values.

    ``mood_series.emotions`` is a JSON copy made at extraction time; a malformed or
    NULL value must never break the chart, so anything that doesn't parse into a
    list degrades to ``[]`` (the point still plots from its mood).
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


@app.get("/insights/mood")
def insights_mood(
    date_from: str | None = Query(None, alias="from", description="Inclusive start day, YYYY-MM-DD."),
    date_to: str | None = Query(None, alias="to", description="Inclusive end day, YYYY-MM-DD."),
    include_seeded: bool = Query(False, description="Include is_seeded=1 demo rows (the demo chart)."),
) -> dict:
    """Return the mood time-series for the chart, oldest point first.

    Each point is ``{entry_id, date, mood, emotions, summary, is_seeded}``. ``mood``
    is an integer −5..+5 or ``null`` (a NULL extraction); the UI draws ``null`` as a
    gap, never zero (§7.1). ``summary`` is the entry's 4–5 sentence reflection for
    the hover tooltip (``null`` if the extraction stored none).

    Defaults to live data only (``is_seeded = 0``); ``include_seeded=true`` lifts
    that filter for the demo. The optional ``from``/``to`` bounds back the 7-/30-day
    toggle. No model is touched — this is a denormalised SQL read.
    """
    for label, value in (("from", date_from), ("to", date_to)):
        if value is not None and not _DATE_RE.match(value):
            raise HTTPException(status_code=400, detail=f"'{label}' must be YYYY-MM-DD")

    conn = db.get_or_create_db()
    try:
        rows = db.mood_series_range(
            conn, date_from=date_from, date_to=date_to, include_seeded=include_seeded
        )
    finally:
        conn.close()

    points = [
        {
            "entry_id": r["entry_id"],
            "date": r["date"],
            "mood": r["mood"],  # int or None — the UI renders None as a gap
            "emotions": _decode_emotions(r["emotions"]),
            "summary": r["summary"],
            "is_seeded": bool(r["is_seeded"]),
        }
        for r in rows
    ]
    return {"from": date_from, "to": date_to, "include_seeded": include_seeded, "points": points}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — Library. The user hands Eva their books; the corpus pipeline is real.
# Upload runs synchronously (load → chunk → embed → index) so the response means
# "indexed" and the UI can show its result immediately; the heavy embedding work
# runs in the threadpool because these are sync `def` handlers. The list and
# remove endpoints read/mutate the manifest the ingest module owns.
# ─────────────────────────────────────────────────────────────────────────────

# Reject absurdly large uploads before reading them into memory. 50 MB covers a
# long, image-heavy book while keeping a single upload well inside the 8 GB
# budget. (We only index the text layer; the bytes themselves are stored as-is.)
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@app.post("/corpus/upload")
def corpus_upload(file: UploadFile = File(...)) -> dict:
    """Ingest one uploaded document and return its record (ready or failed).

    Reads the upload, then runs the full ingest pipeline. A file Eva can't read
    (corrupt, encrypted, empty, unsupported) comes back with ``status='failed'``
    and a message — a 200 response, because the *request* succeeded and the
    Library should show the failure state, not a transport error. A 413 is raised
    only when the file exceeds the size cap.
    """
    data = file.file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return corpus_ingest.ingest_file(file.filename or "document", data)


@app.get("/corpus")
def corpus_list() -> dict:
    """List the ingested documents (newest first) with chunk counts and status."""
    return {"documents": corpus_ingest.list_documents()}


@app.delete("/corpus/{doc_id}")
def corpus_remove(doc_id: str) -> dict:
    """Remove a document: its chunks, its stored bytes, and its manifest entry."""
    if not corpus_ingest.remove_document(doc_id):
        raise HTTPException(status_code=404, detail="document not found")
    return {"removed": doc_id}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8 — Voice in (push-to-talk STT) + the settings knob behind it.
#
# POST /stt takes a recorded clip and returns text; the UI drops that text into
# the input box for the user to confirm, then sends it through the normal /chat or
# /journal pipeline — so a spoken turn is captured/extracted/grounded exactly like
# a typed one. The handler is a sync `def`, so FastAPI runs the (CPU-bound, model-
# loading) transcription in the threadpool and never blocks the async chat path
# (§8: the real-time chat path has priority). The model is lazy-loaded inside
# voice/stt.py on the first call here — never at startup (§4 memory budget).
#
# GET/PATCH /settings expose the single settings store (§9). Phase 8 wires one
# knob — the whisper model size — read live by stt.py so a change takes effect on
# the next transcription; Phase 10's Settings screen extends the same store.
# ─────────────────────────────────────────────────────────────────────────────

# An over-cap or pathological upload is rejected before it reaches whisper. 120 s
# of 16-bit mono at 48 kHz in a compressed container is well under this; the
# generous ceiling just stops a multi-hundred-MB blob from being read into RAM.
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


@app.post("/stt")
def stt_transcribe(file: UploadFile = File(...)) -> dict:
    """Transcribe one push-to-talk recording to text.

    Returns ``{"text", "duration", "model_size"}``. The first call lazy-loads
    faster-whisper (and downloads its weights if absent — the only permitted STT
    network call); later calls reuse the resident model, reloading only when the
    whisper size in Settings changes. Failures degrade gracefully, never crash:
      * empty upload → 400;
      * clip over the 120 s cap → 413 (cap also enforced in the UI);
      * model unavailable (absent + offline) → 503 with a "keep typing" message.
    """
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The recording was empty.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Recording exceeds the {_MAX_AUDIO_BYTES // (1024 * 1024)} MB limit.",
        )
    try:
        return stt.transcribe(data)
    except stt.AudioTooLong as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except stt.STTUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — a bad clip is a 400, not a 500
        log.exception("transcription failed")
        raise HTTPException(
            status_code=400, detail="Could not read that recording. Please try again."
        ) from exc


def _settings_response(settings: dict) -> dict:
    """Bundle the settings with the metadata the Settings screen renders against.

    Alongside the values: ``options`` (closed-set choices, for the whisper-size
    dropdown), ``ranges`` (numeric bounds, for the voice-speed slider), and
    ``vault_path`` (read-only display of where the user's data lives, with an
    "open in Finder" affordance). Model status and voice-weight presence are read
    from ``/health`` instead, so they are not duplicated here.
    """
    return {
        "settings": settings,
        "options": app_settings.options(),
        "ranges": app_settings.ranges(),
        "vault_path": str(vault_dir()),
    }


@app.get("/settings")
def get_settings() -> dict:
    """Return the current settings plus the choices/ranges/paths the UI renders."""
    return _settings_response(app_settings.load())


class SettingsPatch(BaseModel):
    """Partial settings update — Phase 10 extends Phase 8's single whisper knob.

    Every field is optional; only the ones sent are changed. ``None`` means
    "leave unchanged" and is stripped before the store sees the patch.
    """

    whisper_model_size: str | None = Field(
        None, description="faster-whisper model size: 'base.en' or 'small.en'."
    )
    voice_enabled: bool | None = Field(
        None, description="Whether Eva speaks her replies (persists the voice toggle)."
    )
    voice_speed: float | None = Field(
        None, description="Eva's speaking rate (Kokoro speed); 1.0 is natural pace."
    )


@app.patch("/settings")
def patch_settings(body: SettingsPatch) -> dict:
    """Apply a partial settings update and return the new settings.

    Only keys explicitly sent are changed. An invalid value (an unknown whisper
    size, or a speed out of range) is a 400 — :func:`settings.update` validates
    against the allowed set/range. The change is persisted to
    ``<vault>/settings.json`` and read live by its consumer: ``stt.py`` picks up a
    whisper size on the next transcription, and ``tts.py`` the speed on the next
    sentence — both with no restart.
    """
    patch = body.model_dump(exclude_none=True)
    if not patch:
        return _settings_response(app_settings.load())
    try:
        updated = app_settings.update(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _settings_response(updated)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 10 — Privacy audit + vault reveal.
#
# The Offline ✓ badge reads the guard state from /health; this endpoint is the
# on-demand "prove it" the Settings privacy panel calls — it reports whether the
# outbound block is installed and, crucially, whether anything has been blocked
# this run (the difference between "configured" and "verified holding"). The
# vault-reveal endpoint opens the user's data folder in their file manager — a
# local OS action on a fixed, non-user-supplied path, never a network call.
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/privacy/audit")
def privacy_audit() -> dict:
    """Report the live outbound-network guard state for the privacy panel.

    Returns the guard summary (installed?, allow-listed download host, and the
    count + last target of any blocked attempts) plus a plain-English verdict the
    UI can show verbatim. No active probing — the guard is always live, so its
    own bookkeeping is the audit (EVA_SYSTEM_DESIGN §9, §11 ``/privacy/audit``).
    """
    summary = allow_summary()
    if not summary["installed"]:
        verdict = "Outbound network guard is NOT active."
    elif summary["violations"]:
        verdict = (
            f"Guard active and holding: {summary['violations']} outbound "
            "attempt(s) were blocked this session."
        )
    else:
        verdict = "Guard active. No outbound connection has been attempted this session."
    return {"verdict": verdict, **summary}


@app.post("/vault/reveal")
def vault_reveal() -> dict:
    """Open the vault directory in the OS file manager (macOS ``open``).

    A convenience for the Settings "vault location" row so the user can see their
    own plain-Markdown data without hunting for the path. The path is fixed
    (:func:`memory.vault_dir`), never client-supplied, so there is nothing to
    inject. Returns the path either way; ``opened`` says whether the file manager
    was launched (false on a non-macOS host or if the directory doesn't exist yet).
    """
    import subprocess
    import sys

    path = vault_dir()
    if not path.exists():
        return {"path": str(path), "opened": False, "reason": "vault not created yet"}
    if sys.platform != "darwin":
        return {"path": str(path), "opened": False, "reason": "reveal is macOS-only"}
    try:
        subprocess.run(["open", str(path)], check=True, timeout=5)
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("vault reveal failed: %s", exc)
        return {"path": str(path), "opened": False, "reason": "could not open Finder"}
    return {"path": str(path), "opened": True}


if __name__ == "__main__":
    # Convenience launcher: `python app.py` runs the dev server. dev.sh uses
    # uvicorn directly with reload; both bind to loopback only.
    import uvicorn

    log.info("Starting Eva backend on http://127.0.0.1:%d", BACKEND_PORT)
    uvicorn.run(app, host="127.0.0.1", port=BACKEND_PORT)
