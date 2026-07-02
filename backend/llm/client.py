"""Async model client — the ONE path the backend uses to reach the model.

Everything that talks to Gemma goes through this module: the real-time ``/chat``
turn (:func:`stream_chat`) and bounded background jobs like L1 extraction
(:func:`complete_chat`). Centralising access here is what lets us keep a single
``asyncio.Lock`` in front of the one shared model server.

**Concurrency & priority (EVA_SYSTEM_DESIGN §8).** The backend is async and the
real-time chat path must never wait behind a background job. Both entry points
serialise through ``_model_lock``; a ``priority`` flag (default ``True`` for chat)
makes a chat turn jump ahead of background extraction. Background callers
(``priority=False``) yield first and then wait while any chat turn is queued or
in flight, so a journaling reply is never stuck behind a nightly/extraction call.
Access is coarse-grained — one model, one lock — which is exactly the §8 design:
"the heavy work happens where latency doesn't matter."

**Sampling (CLAUDE.md).** Set per request here, not on the server:
chat uses temp 1.0 / top_p 0.95 / top_k 64; extraction uses temp 0.3. The server's
``--n_ctx`` is the maximum; per-request budgeting is done via ``max_tokens`` and
(later) message truncation — never by changing the server flag.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from llm import providers, server

log = logging.getLogger("eva.llm.client")

# OpenAI-compatible chat endpoint served by the native ``llama-server`` binary on
# :11500. Only loopback is contacted, so the privacy net-guard permits it.
CHAT_URL = f"{server.BASE_URL}/v1/chat/completions"

# Chat sampling (CLAUDE.md): warm, varied companion voice.
CHAT_TEMPERATURE = 1.0
CHAT_TOP_P = 0.95
CHAT_TOP_K = 64
# Stop generation at the gemma turn boundary. The GGUF's embedded template should
# stop here on its own, but if it doesn't, the model runs past <end_of_turn> and
# regenerates a whole new turn — which `_strip_leaks` then hides by removing the
# marker, so the reply renders as a verbatim duplicate. Passing the markers as a
# hard `stop` halts generation at the boundary instead of stripping-and-continuing.
CHAT_STOP = ["<end_of_turn>", "<eos>"]
# Extraction sampling: low temperature for consistently parseable JSON.
EXTRACT_TEMPERATURE = 0.3

DEFAULT_MAX_TOKENS = 450  # §9 default reply length (hard cap 1000 in later modes).

# Connect fast (server is local); allow a long read for a full streamed reply.
_CONNECT_TIMEOUT = 15.0
_READ_TIMEOUT = 300.0

# Belt-and-suspenders: strip any special turn/eos tokens that a chat template
# might leak into visible content. With the gemma-4 GGUF's embedded template this
# does nothing; it guards against template/format drift.
_LEAK_TOKENS = ("<end_of_turn>", "<eos>", "<turn|>", "<|turn>", "<bos>")


# ── model-access lock with chat priority (EVA_SYSTEM_DESIGN §8) ───────────────
_model_lock = asyncio.Lock()
# Number of priority (chat) callers currently waiting for, or holding, the lock.
# Background callers defer while this is > 0 so chat always wins the race.
_chat_waiting = 0
_BACKGROUND_POLL_S = 0.05


@asynccontextmanager
async def _model_access(priority: bool):
    """Acquire the shared model lock, honouring chat priority (§8).

    A ``priority`` (chat) caller registers its intent *before* awaiting the lock,
    so background work yields to it. A non-priority (background) caller first
    yields the event loop, then waits until no chat turn is pending, then takes
    the lock. The result: a real-time turn is never blocked by background
    extraction, while a background job already mid-call simply finishes its one
    bounded request before the chat turn proceeds (it is never cancelled).
    """
    global _chat_waiting
    if priority:
        _chat_waiting += 1
    try:
        if not priority:
            await asyncio.sleep(0)  # let any ready chat coroutine register first
            while _chat_waiting > 0:
                await asyncio.sleep(_BACKGROUND_POLL_S)
        async with _model_lock:
            yield
    finally:
        if priority:
            _chat_waiting -= 1


def _strip_leaks(text: str) -> str:
    """Remove any leaked special tokens from visible model output."""
    for tok in _LEAK_TOKENS:
        if tok in text:
            text = text.replace(tok, "")
    return text


def _build_payload(
    messages: list[dict],
    *,
    max_tokens: int,
    temperature: float,
    top_p: float | None,
    top_k: int | None,
    stop: list[str] | None,
    stream: bool,
) -> dict:
    """Assemble the OpenAI chat-completions request body.

    ``top_p``/``top_k``/``stop`` are omitted when ``None`` so extraction can fall
    back to server defaults exactly as the original Phase-2 call did.
    """
    payload: dict = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    if top_p is not None:
        payload["top_p"] = top_p
    if top_k is not None:
        payload["top_k"] = top_k
    if stop:
        payload["stop"] = stop
    return payload


def _timeout():
    import httpx

    return httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)


async def stream_chat(
    messages: list[dict],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temp: float = CHAT_TEMPERATURE,
    top_p: float | None = CHAT_TOP_P,
    top_k: int | None = CHAT_TOP_K,
    priority: bool = True,
    stop: list[str] | None = CHAT_STOP,
) -> AsyncIterator[str]:
    """Stream a chat reply token-by-token from the model server.

    Yields text pieces as they arrive (Server-Sent Events from the OpenAI
    endpoint). This is the real-time path, so ``priority`` defaults to ``True`` —
    it takes the model lock ahead of any background job. ``max_tokens`` bounds the
    reply (and thus the per-request context budget); ``temp``/``top_p``/``top_k``
    carry the chat sampling defaults from CLAUDE.md.

    Raises on transport/server errors (e.g. the server is down) so the caller —
    the ``/chat`` WebSocket — can surface a graceful error to the UI.
    """
    options = providers.ChatOptions(
        max_tokens=max_tokens,
        temperature=temp,
        top_p=top_p,
        top_k=top_k,
        stop=stop,
        stream=True,
    )
    async with _model_access(priority):
        async for piece in providers.selected_provider().stream_chat(messages, options):
            if piece:
                yield _strip_leaks(piece)


async def complete_chat(
    messages: list[dict],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temp: float = CHAT_TEMPERATURE,
    top_p: float | None = CHAT_TOP_P,
    top_k: int | None = CHAT_TOP_K,
    priority: bool = False,
    stop: list[str] | None = CHAT_STOP,
) -> str:
    """Return a full (non-streamed) chat completion as text.

    Used by bounded background jobs — notably L1 extraction — which want the whole
    reply at once, not a stream. Defaults to ``priority=False`` so it defers to
    real-time chat turns. Same lock and endpoint as :func:`stream_chat`, so there
    is exactly one model-access path.
    """
    options = providers.ChatOptions(
        max_tokens=max_tokens,
        temperature=temp,
        top_p=top_p,
        top_k=top_k,
        stop=stop,
        stream=False,
    )
    async with _model_access(priority):
        text = await providers.selected_provider().complete_chat(messages, options)
    return _strip_leaks(text)


def provider_configured() -> bool:
    """Return whether the selected AI provider has enough config to be used."""
    return providers.is_configured()


async def provider_status() -> providers.ProviderStatus:
    """Return the selected AI provider's redacted readiness status."""
    return await providers.selected_provider_status()
