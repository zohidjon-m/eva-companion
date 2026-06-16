#!/usr/bin/env python3
"""Phase 7 end-to-end RAG verification — REAL embeddings, REAL model, no mocks.

This drives the *live* backend (must already be running with EVA_START_LLAMA=1 on
127.0.0.1:8000) to prove the grounded-answer path actually works:

  1. Index a small, distinctive, made-up document into the corpus via the real
     POST /corpus/upload endpoint (real loader → chunker → bge-small embeddings).
  2. Send three messages through the real WS /chat path and, for each, report:
       - the intent class the server decided (read from the backend log)
       - whether retrieval fired and how many passages were cited
       - the cited source label(s)
       - Eva's actual streamed answer

The three probes map to the Phase-7 acceptance tests:
  a) a question whose answer IS in the document  → retrieval fires, correct chip,
     grounded answer.
  b) a question about something NOT in any document → no citation, Eva says she
     doesn't find it (no fabricated source).
  c) an ambiguous "I don't know what to do" → the model fallback classifier fires.

It only contacts loopback, and the embedding model is loaded offline by the
backend, so nothing here touches the network.

Usage:
    backend/.venv/bin/python scripts/verify_rag.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import websockets

BACKEND = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/chat"
BACKEND_LOG = Path("/tmp/eva_backend.log")

# A made-up document with content that cannot be confused with the model's own
# knowledge. The distinctive proper nouns ("Zephyrine moss", "Lake Quemby",
# "the Marrowford Accord of 1631") make a grounded answer unambiguous: if Eva
# says these things, she got them from the passage, not from training data.
TEST_DOC = """\
The Field Guide to the Hollowmere Wetlands

Chapter 3: Zephyrine Moss

Zephyrine moss is a pale violet moss that grows only along the northern shore of
Lake Quemby. Local naturalists prize it because it glows faintly blue for exactly
nine nights after the first frost of autumn. The moss was first catalogued by the
botanist Aldous Penhallow in 1842.

Chapter 4: The Marrowford Accord

The Marrowford Accord of 1631 was an agreement among the seven lakeside villages
to never drain the Hollowmere Wetlands. Under the Accord, each village appointed a
single Warden of the Reeds, whose duty was to count the heron nests every spring
and report the tally at the midsummer gathering.
"""

# (label, message). Order matters only for readability of the report.
PROBES = [
    ("a) in-document question",
     "What color does Zephyrine moss glow, and for how long after the first frost?"),
    ("b) not-in-any-document question",
     "What does the guide say about the migratory routes of Arctic terns?"),
    ("c) ambiguous statement",
     "I don't know what to do."),
]


def _fail(msg: str) -> None:
    print(f"\n[FATAL] {msg}", file=sys.stderr)
    sys.exit(1)


def _preflight() -> None:
    """Confirm the live backend + model server are up before we begin."""
    try:
        health = httpx.get(f"{BACKEND}/health", timeout=5).json()
    except Exception as e:  # noqa: BLE001
        _fail(f"backend not reachable at {BACKEND} ({e}). Start it with "
              f"EVA_START_LLAMA=1 first.")
    if not health.get("model_present"):
        _fail("model GGUF not present — cannot test the live generation path.")
    if not health.get("model_server_running"):
        _fail("model server not running — start the backend with EVA_START_LLAMA=1.")
    # The model may still be warming up; probe the OpenAI endpoint directly.
    for _ in range(60):
        try:
            if httpx.get("http://127.0.0.1:11500/v1/models", timeout=3).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    else:
        _fail("model server did not answer /v1/models in time.")
    print(f"[ok] backend up, model present, server ready (net_guard="
          f"{health.get('net_guard')})")


def _index_test_document() -> dict:
    """Upload the made-up document through the real ingest endpoint."""
    files = {"file": ("hollowmere_field_guide.md", TEST_DOC.encode("utf-8"), "text/markdown")}
    resp = httpx.post(f"{BACKEND}/corpus/upload", files=files, timeout=120)
    resp.raise_for_status()
    doc = resp.json()
    if doc.get("status") != "ready":
        _fail(f"test document failed to index: {doc.get('error')}")
    print(f"[ok] indexed test document '{doc['filename']}' → "
          f"{doc['chunk_count']} chunk(s), id={doc['id']}")
    return doc


def _log_size() -> int:
    try:
        return BACKEND_LOG.stat().st_size
    except OSError:
        return 0


def _read_log_since(offset: int) -> str:
    """Return the backend log text written since byte ``offset`` (best-effort)."""
    try:
        with BACKEND_LOG.open("r", errors="replace") as fh:
            fh.seek(offset)
            return fh.read()
    except OSError:
        return ""


def _parse_server_decision(log_chunk: str) -> dict:
    """Pull the intent + retrieval decision the server logged for this turn."""
    out: dict = {"intent": None, "method": None, "retrieval": None, "passages": None}
    for line in log_chunk.splitlines():
        if "intent=" in line and ("BYPASSED" in line or "retrieval fired" in line):
            # e.g. "intent=question (rule) → retrieval fired, 1 passage(s) cited"
            seg = line.split("intent=", 1)[1]
            label, _, rest = seg.partition(" ")
            out["intent"] = label.strip()
            if "(" in rest and ")" in rest:
                out["method"] = rest[rest.find("(") + 1: rest.find(")")]
            if "BYPASSED" in line:
                out["retrieval"] = "bypassed"
                out["passages"] = 0
            elif "retrieval fired" in line:
                out["retrieval"] = "fired"
                # "... fired, N passage(s) cited"
                try:
                    out["passages"] = int(line.split("fired,", 1)[1].strip().split()[0])
                except (IndexError, ValueError):
                    pass
        elif "intent=" in line and "fallback" in line:
            # model fallback log: "intent=advice_request (model fallback)"
            seg = line.split("intent=", 1)[1]
            out["intent"] = seg.split(" ", 1)[0].strip()
            out["method"] = "model"
    return out


async def _run_turn(message: str) -> dict:
    """Send one message over the live WS and collect Eva's reply + citations."""
    answer: list[str] = []
    citations: list[dict] = []
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({"text": message}))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
            t = frame.get("type")
            if t == "citations":
                citations = frame["citations"]
            elif t == "token":
                answer.append(frame["content"])
            elif t == "done":
                break
            elif t == "error":
                return {"error": frame.get("message", "unknown"), "citations": citations}
    return {"answer": "".join(answer).strip(), "citations": citations}


async def _main() -> int:
    _preflight()
    _index_test_document()
    # Let the just-written corpus vectors settle (Chroma persist is synchronous,
    # but give the upload's threadpool work a beat to be fully visible).
    await asyncio.sleep(1.0)

    print("\n" + "=" * 72)
    print("RAG PROBES (real embeddings, real model)")
    print("=" * 72)

    for label, message in PROBES:
        before = _log_size()
        result = await _run_turn(message)
        time.sleep(0.5)  # let the server flush its log lines for this turn
        decision = _parse_server_decision(_read_log_since(before))

        print(f"\n### {label}")
        print(f"  message      : {message!r}")
        print(f"  intent       : {decision['intent']} (method={decision['method']})")
        print(f"  retrieval    : {decision['retrieval']}  "
              f"passages={decision['passages']}")
        if result.get("error"):
            print(f"  ERROR        : {result['error']}")
            continue
        cites = result["citations"]
        if cites:
            for c in cites:
                snippet = " ".join(c["text"].split())[:90]
                print(f"  cited source : {c['label']}")
                print(f"                 “{snippet}…”")
        else:
            print("  cited source : (none)")
        print(f"  Eva's answer : {result['answer']}")

    print("\n" + "=" * 72)
    print("Done. Review the three probes against the expectations in the header.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
