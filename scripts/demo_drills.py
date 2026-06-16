#!/usr/bin/env python3
"""Failure drills — prove every demo failure mode fails *soft* (Phase 15).

The point of the demo is that nothing on stage produces a stack trace or a frozen
screen. Phase 15's job is to confirm that the soft-fail handling built across the
earlier phases actually holds, in one runnable harness that prints a pass/fail
report. The plan's five categories:

    model down            a chat turn with the model file missing, and with the
                          model *server* down (file present) → graceful error
                          frames, never a crash; the socket stays usable.
    mic denied            UI/OS-level (getUserMedia rejection) — verified in the
                          frontend (ui/src/voice/useRecorder.ts micErrorMessage);
                          reported here as a manual ✎ row with where to confirm it.
    Wi-Fi off             every feature must still work offline. The backend truth
                          behind that promise — the outbound net guard — is asserted
                          here (it is *why* pulling Wi-Fi changes nothing at runtime).
    huge PDF              an over-cap upload → 413 with a clear message, not an OOM.
    rapid-fire            many turns sent back-to-back on one socket → each gets a
                          clean frame, the server never falls over.

It runs entirely in-process via FastAPI's TestClient against a *throwaway* vault
(``EVA_VAULT_DIR`` → a temp dir), so it never touches the user's real data and
needs no running server. The Gemma GGUF lives under ``models/`` (not the vault),
so "model present, server down" is the natural state here; "model file missing" is
simulated by pointing the path at nothing for one drill.

Usage:
    backend/.venv/bin/python scripts/demo_drills.py        # run + print report
    backend/.venv/bin/python -m pytest backend/tests/test_failure_drills.py  # CI form
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# One drill outcome. ``passed`` is True/False for an automated check, or ``None``
# for a drill that is genuinely manual (mic/OS) and only documented here.
Drill = namedtuple("Drill", "name passed detail")

# Over-cap blobs for the size-guard drills (just past the app's limits: 50 MB
# upload, 25 MB audio). The guards reject on length before parsing, so the bytes'
# content is irrelevant.
_OVER_UPLOAD = b"0" * (51 * 1024 * 1024)
_OVER_AUDIO = b"0" * (26 * 1024 * 1024)


def _setup_env() -> Path:
    """Redirect the vault to a temp dir and import the app under it.

    Symlinks the real ``models/`` cache in (best-effort) so the embedding model
    loads from disk rather than attempting a (net-guard-blocked) download during
    the chat drills — keeping them fast and fully offline. Returns the temp vault.
    """
    tmp = Path(tempfile.mkdtemp(prefix="eva-drills-"))
    os.environ["EVA_VAULT_DIR"] = str(tmp)
    os.environ.setdefault("EVA_START_LLAMA", "0")  # never autostart the model server
    real_models = _REPO_ROOT / "local_vault" / "models"
    try:
        if real_models.is_dir():
            os.symlink(real_models, tmp / "models")
    except OSError:
        pass  # recall/corpus still fail soft to [] without the cache
    sys.path.insert(0, str(_REPO_ROOT / "backend"))
    return tmp


def _send_turn(client, text: str) -> list[dict]:
    """Send one chat turn and return every frame up to and including its terminal.

    A turn ends at the first ``done`` or ``error`` frame. Used by the model-down
    drills; the TestClient runs the socket in-process so this never hits the network.
    """
    frames: list[dict] = []
    with client.websocket_connect("/chat") as ws:
        ws.send_text(json.dumps({"text": text}))
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame.get("type") in {"done", "error"}:
                break
    return frames


# ── the drills ────────────────────────────────────────────────────────────────


def drill_model_file_missing(client, app_mod) -> Drill:
    """Model GGUF absent → chat returns a 'model_missing' error frame, no crash."""
    server = app_mod.llm_server
    original = server.MODEL_PATH
    server.MODEL_PATH = Path("/nonexistent/gemma.gguf")
    try:
        frames = _send_turn(client, "hello Eva")
    finally:
        server.MODEL_PATH = original
    err = next((f for f in frames if f.get("type") == "error"), None)
    ok = err is not None and err.get("code") == "model_missing" and bool(err.get("message"))
    detail = (
        f"error frame code={err.get('code')!r}, hint present={bool(err.get('message'))}"
        if err
        else f"no error frame (got {[f.get('type') for f in frames]})"
    )
    return Drill("model down — file missing", ok, detail)


def drill_model_server_down(client, app_mod) -> Drill:
    """Model file present but server down → graceful 'model_error', socket survives.

    The Gemma file is on disk (under models/) but no llama_cpp server is listening
    on :11500, so the stream call gets a connection error — which the chat handler
    turns into an error frame instead of letting it escape. We then send a second
    turn on a fresh socket to confirm the backend is still serving.
    """
    frames = _send_turn(client, "I had a long day today")
    types = [f.get("type") for f in frames]
    err = next((f for f in frames if f.get("type") == "error"), None)
    graceful = err is not None and err.get("code") in {"model_error", "model_missing"}
    # Backend still up afterwards?
    still_up = client.get("/health").status_code == 200
    ok = graceful and still_up
    detail = f"frames={types}, code={err.get('code') if err else None}, health_ok={still_up}"
    return Drill("model down — server down", ok, detail)


def drill_huge_upload(client, app_mod) -> Drill:
    """A >50 MB upload is rejected with 413 before it can exhaust memory."""
    resp = client.post(
        "/corpus/upload",
        files={"file": ("huge.pdf", _OVER_UPLOAD, "application/pdf")},
    )
    ok = resp.status_code == 413 and "limit" in resp.json().get("detail", "").lower()
    return Drill("huge PDF — over size cap", ok, f"status={resp.status_code}, detail={resp.json().get('detail')!r}")


def drill_empty_upload(client, app_mod) -> Drill:
    """An empty upload is a clean 400, not a confusing success."""
    resp = client.post("/corpus/upload", files={"file": ("empty.txt", b"", "text/plain")})
    ok = resp.status_code == 400
    return Drill("huge PDF — empty file", ok, f"status={resp.status_code}, detail={resp.json().get('detail')!r}")


def drill_oversize_audio(client, app_mod) -> Drill:
    """A >25 MB STT clip is rejected with 413, never read into the model."""
    resp = client.post("/stt", files={"file": ("clip.webm", _OVER_AUDIO, "audio/webm")})
    ok = resp.status_code == 413
    return Drill("rapid-fire — oversize audio", ok, f"status={resp.status_code}")


def drill_rapid_fire(client, app_mod, n: int = 8) -> Drill:
    """Many turns back-to-back on one socket → each handled, server never crashes.

    With the model server down each turn resolves to an error frame; the test is
    that all ``n`` are processed in order on a single connection and the socket is
    still alive for an (n+1)-th send. This exercises the per-socket send lock and
    the loop's resilience to a burst of input — the 'impatient demo-er' case.
    """
    errors = 0
    survived = False
    with client.websocket_connect("/chat") as ws:
        for i in range(n):
            ws.send_text(json.dumps({"text": f"message {i}"}))
        # Each turn emits start...error; read until we've seen n terminals.
        seen_terminals = 0
        while seen_terminals < n:
            frame = ws.receive_json()
            if frame.get("type") in {"done", "error"}:
                seen_terminals += 1
                if frame.get("type") == "error":
                    errors += 1
        # One more turn proves the socket is still usable after the burst.
        ws.send_text(json.dumps({"text": "still there?"}))
        while True:
            frame = ws.receive_json()
            if frame.get("type") in {"done", "error"}:
                survived = True
                break
    ok = errors == n and survived
    return Drill("rapid-fire — burst of turns", ok, f"{errors}/{n} handled, socket survived={survived}")


def drill_offline_guard(client, app_mod) -> Drill:
    """Wi-Fi off is a non-event because nothing outbound is allowed at runtime.

    Asserts the backend truth behind that: /health reports the guard installed and
    /privacy/audit returns the 'guard active' verdict. This is the structural reason
    pulling the network during the demo changes nothing — there is no runtime call
    to fail.
    """
    health = client.get("/health").json()
    audit = client.get("/privacy/audit").json()
    ok = bool(health.get("net_guard")) and "active" in audit.get("verdict", "").lower()
    return Drill("Wi-Fi off — net guard holds", ok, f"net_guard={health.get('net_guard')}, verdict={audit.get('verdict')!r}")


def drill_mic_denied(client, app_mod) -> Drill:
    """Mic-denied is OS/UI-level — documented, verified in the recorder hook."""
    return Drill(
        "mic denied — manual",
        None,
        "getUserMedia rejection → calm message + fall back to typing "
        "(ui/src/voice/useRecorder.ts: micErrorMessage). Verify live by clicking "
        "Deny on the mic prompt.",
    )


# Automated drills run by both the CLI and the pytest wrapper; the manual one is
# appended for the printed report only.
_AUTOMATED = [
    drill_model_file_missing,
    drill_model_server_down,
    drill_huge_upload,
    drill_empty_upload,
    drill_oversize_audio,
    drill_rapid_fire,
    drill_offline_guard,
]


def run_all() -> list[Drill]:
    """Run every automated drill in-process and return the results.

    Importable so ``tests/test_failure_drills.py`` can assert on the same checks
    the demo-day report shows — one source of truth for "the drills pass".
    """
    _setup_env()
    from fastapi.testclient import TestClient
    import app as app_mod

    results: list[Drill] = []
    with TestClient(app_mod.app) as client:
        for fn in _AUTOMATED:
            try:
                results.append(fn(client, app_mod))
            except Exception as exc:  # a drill that itself errors is a failure
                results.append(Drill(fn.__name__, False, f"drill raised: {exc!r}"))

    # The chat drills called the real stream_chat, which acquired the module-global
    # asyncio lock in llm.client on the TestClient's event loop. That loop is now
    # closed, leaving the lock bound to a dead loop — which would break any later
    # code that uses it on a different loop (e.g. the in-suite test_llm lock test).
    # Rebind it to a fresh, unbound lock so we leave global state clean. Harmless in
    # the standalone-script case (the process exits straight after).
    import asyncio
    from llm import client as _llm_client

    _llm_client._model_lock = asyncio.Lock()  # noqa: SLF001 — deliberate isolation reset
    _llm_client._chat_waiting = 0  # noqa: SLF001
    return results


def main() -> int:
    results = run_all()
    results.append(drill_mic_denied(None, None))  # manual row, report only

    print("\n" + "═" * 74)
    print("  Eva — failure drills (Phase 15)")
    print("═" * 74)
    failed = 0
    for d in results:
        if d.passed is True:
            mark = "PASS"
        elif d.passed is False:
            mark = "FAIL"
            failed += 1
        else:
            mark = " ✎  "
        print(f"  [{mark}] {d.name}")
        print(f"         {d.detail}")
    print("═" * 74)
    automated = [d for d in results if d.passed is not None]
    passed = sum(1 for d in automated if d.passed)
    print(f"  {passed}/{len(automated)} automated drills passed; 1 manual (✎) to verify live.")
    print("═" * 74 + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
