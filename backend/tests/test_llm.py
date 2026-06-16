"""Phase 1 — model server supervisor + async client + WS /chat protocol.

These exercise everything that does NOT need a live model: the exact launch
command, the readiness/missing-model reporting, the chat-priority lock from
EVA_SYSTEM_DESIGN §8, request building, and the streaming socket's wire protocol
(with the model call mocked). Live streaming is covered by the manual
scripts/ws_test.py check in the phase report.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app import app
from llm import client as llm_client
from llm import server as llm_server
from memory import capture, retrieval


def _stub_capture(monkeypatch):
    """Neutralize the Phase-4 capture side-effects for the WS protocol tests.

    The `/chat` socket now persists each user turn, schedules background
    extraction, and (Phase 11) recalls past memories. These tests are about the
    wire protocol, not storage, so we stub all three: capture returns a fake
    record, extraction is a no-op, and recall returns nothing (so no memory frame
    is emitted and the protocol stays the deterministic start→token…→done these
    tests assert). Returns a list that records the texts capture was called with,
    so a test can assert the turn was captured.
    """
    captured: list[str] = []

    def fake_capture(text, entry_type):
        captured.append(text)
        return capture.vault.EntryRecord(
            id="t", date="2026-06-16", type=entry_type, text=text,
            word_count=len(text.split()), created_at="2026-06-16T00:00:00",
        )

    async def fake_extract(*args, **kwargs):
        return "done"

    monkeypatch.setattr(capture, "capture_entry", fake_capture)
    monkeypatch.setattr(capture, "run_extraction_and_embed", fake_extract)
    monkeypatch.setattr(retrieval, "recall_memories", lambda *a, **k: [])
    return captured


# ── server: launch command & status ─────────────────────────────────────────
def test_server_command_has_required_flags():
    cmd = llm_server.server_command("python3")
    s = " ".join(cmd)
    assert "-m llama_cpp.server" in s
    assert cmd[0] == "python3"
    # All layers on the Metal GPU.
    assert "--n_gpu_layers" in cmd and cmd[cmd.index("--n_gpu_layers") + 1] == "-1"
    # Real-time chat context budget.
    assert cmd[cmd.index("--n_ctx") + 1] == "8192"
    # q8_0 KV cache (ggml type 8) + the flash attention it requires.
    assert cmd[cmd.index("--type_k") + 1] == "8"
    assert cmd[cmd.index("--type_v") + 1] == "8"
    assert cmd[cmd.index("--flash_attn") + 1] == "true"
    # Port + model path.
    assert cmd[cmd.index("--port") + 1] == "11500"
    assert cmd[cmd.index("--model") + 1].endswith("gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf")
    # gemma-4 ships its own template; forcing the gemma-1/2 handler leaks tokens.
    assert "--chat_format" not in cmd


def test_model_status_missing_includes_download_hint(monkeypatch):
    monkeypatch.setattr(llm_server, "model_present", lambda: False)
    status = llm_server.model_status()
    assert status["model_present"] is False
    assert "download_model_mac.sh" in status["hint"]
    assert status["endpoint"].endswith(":11500")


def test_server_does_not_start_without_model(monkeypatch):
    monkeypatch.setattr(llm_server, "model_present", lambda: False)
    srv = llm_server.LlamaServer()
    assert srv.start() is False  # graceful, no crash
    assert srv.is_running() is False


# ── client: request building ─────────────────────────────────────────────────
def test_build_payload_omits_none_sampling():
    # Extraction path: top_p/top_k None → omitted so server defaults apply.
    payload = llm_client._build_payload(
        [{"role": "user", "content": "x"}],
        max_tokens=900,
        temperature=0.3,
        top_p=None,
        top_k=None,
        stop=["<end_of_turn>"],
        stream=False,
    )
    assert payload["temperature"] == 0.3
    assert "top_p" not in payload and "top_k" not in payload
    assert payload["stop"] == ["<end_of_turn>"]
    assert payload["stream"] is False


def test_build_payload_includes_chat_sampling():
    payload = llm_client._build_payload(
        [{"role": "user", "content": "x"}],
        max_tokens=450,
        temperature=1.0,
        top_p=0.95,
        top_k=64,
        stop=None,
        stream=True,
    )
    assert (payload["top_p"], payload["top_k"]) == (0.95, 64)
    assert "stop" not in payload


def test_strip_leaks():
    assert llm_client._strip_leaks("hi<end_of_turn>") == "hi"
    assert llm_client._strip_leaks("plain text") == "plain text"


# ── client: §8 chat-priority lock ────────────────────────────────────────────
def test_background_defers_to_chat():
    """A background (priority=False) job waits for an in-flight chat turn."""
    order: list[str] = []

    async def chat():
        async with llm_client._model_access(priority=True):
            order.append("chat-start")
            await asyncio.sleep(0.1)
            order.append("chat-end")

    async def background():
        await asyncio.sleep(0.01)  # ensure the chat turn registers first
        async with llm_client._model_access(priority=False):
            order.append("bg")

    async def run():
        await asyncio.gather(chat(), background())

    asyncio.run(run())
    assert order == ["chat-start", "chat-end", "bg"]


def test_lock_serializes_access():
    """Two callers never hold the model lock at the same time."""
    active = 0
    max_active = 0

    async def use(priority: bool):
        nonlocal active, max_active
        async with llm_client._model_access(priority=priority):
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1

    async def run():
        await asyncio.gather(use(True), use(False), use(True))

    asyncio.run(run())
    assert max_active == 1


# ── WS /chat: wire protocol (model mocked) ───────────────────────────────────
def test_ws_chat_streams_tokens(monkeypatch):
    async def fake_stream(messages, **kwargs):
        for piece in ["Hello", ", ", "there!"]:
            yield piece

    monkeypatch.setattr(llm_server, "model_present", lambda: True)
    monkeypatch.setattr(llm_client, "stream_chat", fake_stream)
    captured = _stub_capture(monkeypatch)

    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("hi")
        assert ws.receive_json() == {"type": "start"}
        tokens = []
        while True:
            frame = ws.receive_json()
            if frame["type"] == "done":
                break
            assert frame["type"] == "token"
            tokens.append(frame["content"])
    assert "".join(tokens) == "Hello, there!"
    # The user's turn was captured (Phase 4 wires /chat to the vault pipeline).
    assert captured == ["hi"]


def test_ws_chat_reports_missing_model(monkeypatch):
    monkeypatch.setattr(llm_server, "model_present", lambda: False)
    captured = _stub_capture(monkeypatch)
    client = TestClient(app)
    with client.websocket_connect("/chat") as ws:
        ws.send_text("hi")
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["code"] == "model_missing"
    # An absent model must never cost the entry: capture ran before the check.
    assert captured == ["hi"]
