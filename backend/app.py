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
from memory import capture, db, vault

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
    reflects whether the supervised subprocess is alive. ``net_guard`` is included
    so the future Offline ✓ badge (Phase 10) can read the truth from the backend.
    """
    status = llm_server.model_status()
    return {
        "status": "ok",
        "model_present": status["model_present"],
        "model": status,
        "model_server_running": _llama.is_running(),
        "net_guard": is_installed(),
        "net_guard_detail": allow_summary(),
    }


def _parse_frame(raw: str) -> tuple[str, bool]:
    """Parse a ``/chat`` frame into ``(text, capture)``.

    Accepts a bare string or a JSON object ``{"text": "...", "capture": true}`` so
    the socket stays friendly to a quick curl/ws test while letting the frontend
    ask for a regenerate-without-re-saving turn. ``capture`` defaults to ``True``
    (every normal turn is saved); the UI sets it ``False`` only when *retrying* a
    turn whose user text was already persisted, so a retry never duplicates the
    journal entry. Returns the stripped text (possibly empty).
    """
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return raw, True
        if isinstance(obj, dict):
            text = str(obj.get("text") or obj.get("message") or "").strip()
            return text, bool(obj.get("capture", True))
    return raw, True


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

    Phase 4 wraps the Phase-1 stream with the real chat surface:
      1. Capture the user's turn (vault + L1) before anything else.
      2. Run the interim keyword crisis-care scan over the user's text.
      3. Assemble the system prompt from the four template slots
         (:mod:`prompts.assembly`), appending the crisis addendum on a match.
      4. Stream the reply, capped at 450 tokens, with this session's history for
         multi-turn coherence.

    The model server going down (even mid-reply) or still loading is surfaced as a
    graceful error frame, never an unhandled crash. ``history`` is per-connection:
    it lives only for the life of this socket.
    """
    await ws.accept()
    history: list[dict] = []
    try:
        while True:
            text, do_capture = _parse_frame(await ws.receive_text())
            if not text:
                await ws.send_json(
                    {"type": "error", "code": "empty", "message": "empty message"}
                )
                continue

            # 1. Capture first — an absent model or a dropped reply never costs the
            #    entry. Retries (capture=False) skip this; their text is already saved.
            if do_capture:
                _capture_user_turn(text)

            if not llm_server.model_present():
                status = llm_server.model_status()
                await ws.send_json(
                    {
                        "type": "error",
                        "code": "model_missing",
                        "message": status.get("hint", "model not available"),
                        "model": status,
                    }
                )
                continue

            # 2–3. Interim crisis-care scan, then assemble the system prompt.
            addendum = crisis_check.crisis_addendum() if crisis_check.is_crisis(text) else ""
            system_prompt = assembly.build_chat_system_prompt(persona_addendum=addendum)
            messages = _compose_messages(system_prompt, history, text)

            await ws.send_json({"type": "start"})
            reply_parts: list[str] = []
            try:
                async for piece in llm_client.stream_chat(
                    messages, max_tokens=assembly.REPLY_MAX_TOKENS, priority=True
                ):
                    reply_parts.append(piece)
                    await ws.send_json({"type": "token", "content": piece})
                await ws.send_json({"type": "done"})
            except WebSocketDisconnect:
                raise
            except Exception as exc:  # noqa: BLE001 — surface as a graceful frame
                log.exception("chat stream failed")
                await ws.send_json(
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


if __name__ == "__main__":
    # Convenience launcher: `python app.py` runs the dev server. dev.sh uses
    # uvicorn directly with reload; both bind to loopback only.
    import uvicorn

    log.info("Starting Eva backend on http://127.0.0.1:%d", BACKEND_PORT)
    uvicorn.run(app, host="127.0.0.1", port=BACKEND_PORT)
