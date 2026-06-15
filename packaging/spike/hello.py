"""Packaging spike — a minimal FastAPI app to prove the Tauri sidecar mechanism.

This is NOT the real backend. Its only job is to verify that we can take a
FastAPI + uvicorn process, freeze it into a single self-contained binary with
PyInstaller, and have that binary launch and answer HTTP on macOS — the exact
shape of how the real backend ships as a Tauri sidecar (EVA_SYSTEM_DESIGN §4).

If this spike fails, we want to know in Phase 0, not Phase 15.

Modes:
  (default)      run the server on 127.0.0.1:<port> until killed
  --selfcheck    start the server, hit /ping once, print the result, exit 0/1
"""

from __future__ import annotations

import sys
import threading
import time
import urllib.request

import uvicorn
from fastapi import FastAPI

PORT = 8765

app = FastAPI(title="Eva packaging spike")


@app.get("/ping")
def ping() -> dict:
    """Trivial liveness endpoint the spike checks against itself."""
    return {"pong": True, "frozen": getattr(sys, "frozen", False)}


def _serve() -> None:
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def _selfcheck() -> int:
    """Run the server in a thread, call /ping, report, and exit.

    Lets the build script confirm the frozen binary actually serves HTTP
    without needing a second process or curl.
    """
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    for _ in range(50):  # up to ~5s for the server to come up
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/ping", timeout=0.5
            ) as r:
                body = r.read().decode()
                print(f"selfcheck OK: {body}")
                return 0
        except Exception:
            time.sleep(0.1)
    print("selfcheck FAILED: server did not answer /ping", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        raise SystemExit(_selfcheck())
    print(f"Eva packaging spike listening on http://127.0.0.1:{PORT} (Ctrl-C to stop)")
    _serve()
