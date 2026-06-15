#!/usr/bin/env python3
"""Manual smoke test for the Phase-1 streaming chat socket (WS /chat).

Connects to the backend's chat WebSocket, sends one message, and prints the
streamed tokens as they arrive — the Phase-1 "tokens stream back, reply is
coherent" check.

Prereqs:
  1. Model server up (backend with EVA_START_LLAMA=1, or scripts/run_model_mac.sh).
  2. Backend up on :8000 (dev.sh, or `uvicorn app:app` from backend/).

Usage:
  python scripts/ws_test.py                 # sends "hello"
  python scripts/ws_test.py "how are you?"  # sends your own message
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets  # bundled with the backend deps (uvicorn[standard])

WS_URL = "ws://127.0.0.1:8000/chat"


async def main(message: str) -> int:
    """Send one turn and stream the reply; return an exit code."""
    print(f"connecting to {WS_URL} …")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({"text": message}))
        print(f"> {message}\n< ", end="", flush=True)
        while True:
            frame = json.loads(await ws.recv())
            kind = frame.get("type")
            if kind == "token":
                print(frame.get("content", ""), end="", flush=True)
            elif kind == "done":
                print("\n[done]")
                return 0
            elif kind == "error":
                print(f"\n[error: {frame.get('code')}] {frame.get('message')}")
                return 1
            # "start" and anything else: ignore.


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "hello"
    raise SystemExit(asyncio.run(main(msg)))
