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
from contextlib import asynccontextmanager

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Install the network guard at import time, before any networking library can
# run. Importing app.py is enough to make Eva offline-safe.
from net_guard import allow_summary, install_net_guard, is_installed

# Phase 2 capture pipeline (vault + L1 index + background extraction/embedding).
from memory import capture

# Phase 1 LLM runtime: the model-server supervisor and the async client.
from llm import client as llm_client
from llm import server as llm_server

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


def _message_text(raw: str) -> str:
    """Extract the user text from a ``/chat`` frame.

    Accepts either a bare string or a JSON object ``{"text": "..."}`` so the
    socket is friendly to both a quick curl/ws test and the future structured
    frontend client. Returns the stripped text (possibly empty).
    """
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(obj, dict):
            return str(obj.get("text") or obj.get("message") or "").strip()
    return raw


@app.websocket("/chat")
async def chat_ws(ws: WebSocket) -> None:
    """Streaming chat socket: receive a turn, stream Gemma's tokens back.

    Protocol (one turn, repeatable on the same connection):
      → client sends a text frame (bare text or ``{"text": "..."}``)
      ← ``{"type":"start"}`` then a sequence of ``{"type":"token","content":…}``
      ← ``{"type":"done"}`` when the reply completes
      ← ``{"type":"error","code":…,"message":…}`` on any failure

    Phase 1 sends the user's text straight to the model (no persona/memory yet —
    those slots arrive in Phase 4). The model server going down or still loading is
    surfaced as a graceful error frame, never an unhandled crash.
    """
    await ws.accept()
    try:
        while True:
            text = _message_text(await ws.receive_text())
            if not text:
                await ws.send_json(
                    {"type": "error", "code": "empty", "message": "empty message"}
                )
                continue
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

            await ws.send_json({"type": "start"})
            try:
                async for piece in llm_client.stream_chat(
                    [{"role": "user", "content": text}], priority=True
                ):
                    await ws.send_json({"type": "token", "content": piece})
                await ws.send_json({"type": "done"})
            except WebSocketDisconnect:
                raise
            except Exception as exc:  # noqa: BLE001 — surface as a graceful frame
                log.exception("chat stream failed")
                await ws.send_json(
                    {"type": "error", "code": "model_error", "message": str(exc)}
                )
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


if __name__ == "__main__":
    # Convenience launcher: `python app.py` runs the dev server. dev.sh uses
    # uvicorn directly with reload; both bind to loopback only.
    import uvicorn

    log.info("Starting Eva backend on http://127.0.0.1:%d", BACKEND_PORT)
    uvicorn.run(app, host="127.0.0.1", port=BACKEND_PORT)
