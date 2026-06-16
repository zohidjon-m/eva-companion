#!/usr/bin/env python3
"""try_eva.py — a one-shot self-test of Eva's book-grounding and voice.

Run this from a Mac terminal while the backend + model server are already up
(``./dev.sh``). It exercises the *real* paths a user would, over the wire, and
prints an expect-vs-actual summary at the end so you can judge each piece yourself.

What it checks, in order:

  1. Health        — GET /health; bails with a clear "start ./dev.sh" message if
                     the model isn't up.
  2. Book / RAG    — uploads a book to /corpus, then sends an in-book question and
                     an off-topic question through the real WS /chat path. For
                     each it prints the question, the intent class, whether
                     retrieval fired, the citation returned (if any), and Eva's
                     full answer.
  3. TTS           — synthesizes a fixed sentence to scripts/_eva_voice.wav and
                     prints the `afplay` command so you can hear it.
  4. Voice round-trip — posts that wav to /stt and prints Whisper's transcription
                     next to the original sentence.

Design notes:
  * HTTP and the chat WebSocket use only the Python standard library (urllib +
    a tiny hand-rolled WS client), so the script runs under *any* interpreter —
    no pip installs needed to talk to the backend.
  * The TTS step imports the repo's ``voice.tts`` and calls Kokoro *in this
    script's interpreter*. Kokoro lives in your system ``python3`` (anaconda),
    not the backend's ``.venv`` — so if you want to actually hear Eva, run this
    with ``python3 scripts/try_eva.py``. If Kokoro isn't importable here the TTS
    (and round-trip) steps skip with a clear message rather than failing.
  * The intent label is read from the backend's rule-based classifier when it can
    be imported; otherwise it's inferred from whether a citation came back.

Nothing here writes to your vault except the corpus upload (a test book) and the
two chat turns (captured like any chat). It does not commit anything.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Config + repo wiring
# ─────────────────────────────────────────────────────────────────────────────
HOST = "127.0.0.1"
PORT = 8000
BASE = f"http://{HOST}:{PORT}"

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
SCRIPTS_DIR = REPO_ROOT / "scripts"
WAV_PATH = SCRIPTS_DIR / "_eva_voice.wav"
TESTBOOK_PATH = SCRIPTS_DIR / "_eva_testbook.txt"

# Let optional in-repo imports (voice.tts, intent.classifier) resolve.
sys.path.insert(0, str(BACKEND_DIR))

# The fixed sentence the TTS + round-trip tests use. Distinctive, multi-word, and
# clearly enunciated so Whisper has a fair shot at it.
TTS_SENTENCE = "Hello, I'm Eva. It is good to finally talk with you out loud."

# Built-in fallback "book" — a made-up creature so the model can't answer from
# its own priors; a correct answer therefore proves it read the document.
BUILTIN_BOOK = """The Field Guide to Marrow Valley

Chapter 3: The Ziblot

The Ziblot is a small nocturnal creature found only in the fictional Marrow
Valley. An adult Ziblot weighs exactly 3.2 kilograms and is easily recognized by
its seven-striped tail. Ziblots feed almost entirely on moonberries, a pale blue
fruit that ripens for just two weeks each autumn. During the rest of the year a
Ziblot survives on stored fat and sleeps in hollow urnwood trunks.

Ziblots are solitary and mark their territory with a faint cinnamon scent.
"""
BUILTIN_IN_BOOK_Q = "How much does an adult Ziblot weigh, and what do they eat?"
BUILTIN_OFF_TOPIC_Q = "How do I change a flat bicycle tire?"


# ─────────────────────────────────────────────────────────────────────────────
# Small output helpers
# ─────────────────────────────────────────────────────────────────────────────
def hr(title: str = "") -> None:
    line = "─" * 72
    if title:
        print(f"\n{line}\n  {title}\n{line}")
    else:
        print(line)


def info(msg: str) -> None:
    print(f"  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP (stdlib)
# ─────────────────────────────────────────────────────────────────────────────
def http_get_json(path: str, timeout: float = 10.0) -> dict:
    """GET a JSON endpoint and return the parsed body."""
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_multipart(
    path: str, field: str, filename: str, content: bytes, content_type: str,
    timeout: float = 180.0,
) -> tuple[int, dict]:
    """POST a single-file multipart form; return ``(status, json_body)``.

    Builds the multipart body by hand so no third-party HTTP client is needed.
    On an HTTP error the backend's JSON ``detail`` is parsed and returned with the
    status code, so the caller can show why an upload/transcription was rejected.
    """
    boundary = "----evatry" + uuid.uuid4().hex
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {"detail": f"HTTP {e.code}"}


# ─────────────────────────────────────────────────────────────────────────────
# Minimal WebSocket client for WS /chat (stdlib socket only)
# ─────────────────────────────────────────────────────────────────────────────
def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes or raise if the socket closes early."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("websocket closed unexpectedly")
        buf += chunk
    return buf


def _ws_send_text(sock: socket.socket, text: str) -> None:
    """Send one masked text frame (clients MUST mask, per RFC 6455)."""
    payload = text.encode("utf-8")
    header = bytearray([0x81])  # FIN=1, opcode=0x1 (text)
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    mask = os.urandom(4)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _ws_read_message(sock: socket.socket) -> tuple[int, bytes]:
    """Read one (possibly fragmented) WS message; return ``(opcode, payload)``.

    Reassembles continuation frames and answers a ping with a pong so a long
    reply never wedges on a keepalive. Control frames (ping/close) are returned
    to the caller too, identified by their opcode.
    """
    msg = b""
    first_opcode: int | None = None
    while True:
        b0, b1 = _recv_exact(sock, 2)
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        mask = _recv_exact(sock, 4) if masked else b""
        payload = _recv_exact(sock, length) if length else b""
        if masked:
            payload = bytes(p ^ mask[i % 4] for i, p in enumerate(payload))

        if opcode == 0x9:  # ping → pong, then keep reading
            _ws_send_frame(sock, 0xA, payload)
            continue
        if opcode == 0x8:  # close
            return 0x8, payload
        if first_opcode is None:
            first_opcode = opcode
        msg += payload
        if fin:
            return first_opcode or opcode, msg


def _ws_send_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    """Send a masked control/data frame with a small (<126 byte) payload."""
    header = bytearray([0x80 | opcode, 0x80 | len(payload)])
    mask = os.urandom(4)
    header += mask
    header += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header))


def chat(question: str, *, timeout: float = 180.0) -> dict:
    """Send one turn through WS /chat and collect the reply + citations.

    Returns ``{"answer", "citations", "error"}``. ``voice`` is false so no audio
    frames are produced — this is the text/RAG path only.
    """
    key = "dGhlIHNhbXBsZSBub25jZQ=="  # any base64 16-byte value is fine for a client
    handshake = (
        f"GET /chat HTTP/1.1\r\n"
        f"Host: {HOST}:{PORT}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock = socket.create_connection((HOST, PORT), timeout=timeout)
    try:
        sock.sendall(handshake.encode())
        # Read the handshake response headers (up to the blank line).
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(1024)
            if not chunk:
                raise ConnectionError("no handshake response")
            resp += chunk
        status_line = resp.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status_line:
            raise ConnectionError(f"websocket upgrade failed: {status_line}")

        _ws_send_text(sock, json.dumps({"text": question, "voice": False}))

        answer_parts: list[str] = []
        citations: list[dict] = []
        error: str | None = None
        while True:
            opcode, payload = _ws_read_message(sock)
            if opcode == 0x8:  # server closed
                break
            try:
                frame = json.loads(payload.decode("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            t = frame.get("type")
            if t == "token":
                answer_parts.append(frame.get("content", ""))
            elif t == "citations":
                citations = frame.get("citations", [])
            elif t == "done":
                break
            elif t == "error":
                error = frame.get("message", "unknown error")
                break
        return {"answer": "".join(answer_parts).strip(), "citations": citations, "error": error}
    finally:
        try:
            _ws_send_frame(sock, 0x8, b"")  # polite close
        except Exception:  # noqa: BLE001
            pass
        sock.close()


# ─────────────────────────────────────────────────────────────────────────────
# Optional in-repo imports (best-effort)
# ─────────────────────────────────────────────────────────────────────────────
def rule_intent(text: str) -> str | None:
    """Return the backend's *rule-based* intent label, or None if unavailable.

    Uses the same ``classify_rules`` the server runs first. If the classifier
    can't be imported in this interpreter (its deps aren't here), returns None and
    the caller falls back to inferring retrieval from the citations that came back.
    """
    try:
        from intent.classifier import classify_rules
    except Exception:  # noqa: BLE001 — deps not present in this interpreter
        return None
    try:
        return classify_rules(text)
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# The checks
# ─────────────────────────────────────────────────────────────────────────────
def check_health() -> dict:
    hr("1. HEALTH")
    try:
        health = http_get_json("/health")
    except Exception as e:  # noqa: BLE001
        info(f"✗ Could not reach the backend at {BASE} ({e}).")
        info("  Start Eva first:  ./dev.sh")
        sys.exit(1)

    present = bool(health.get("model_present"))
    running = bool(health.get("model_server_running"))
    model = health.get("model", {}) or {}
    info(f"model_present        : {present}")
    info(f"model_server_running : {running}")
    info(f"endpoint             : {model.get('endpoint', '?')}")
    if not (present and running):
        info("")
        if not present:
            info("✗ The Gemma model file isn't on disk.")
            if model.get("hint"):
                info(f"  {model['hint']}")
        else:
            info("✗ The model server isn't running yet.")
        info("  Start Eva (backend + model server):  ./dev.sh")
        info("  (Give it ~10 s to load the model, then re-run this script.)")
        sys.exit(1)
    info("✓ Model is up.")
    return {"ok": True}


def check_book_rag(book_path: Path, in_q: str, off_q: str) -> dict:
    hr("2. BOOK / RAG")
    data = book_path.read_bytes()
    ctype = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(book_path.suffix.lower(), "application/octet-stream")

    info(f"Uploading: {book_path.name}  ({len(data)} bytes, {ctype})")
    status, doc = http_post_multipart(
        "/corpus/upload", "file", book_path.name, data, ctype
    )
    if status != 200 or doc.get("status") != "ready" or not doc.get("chunk_count"):
        info(f"✗ Upload/indexing failed (HTTP {status}): {doc}")
        return {"ok": False, "reason": "indexing failed"}
    info(f"✓ Indexed '{doc['filename']}' → {doc['chunk_count']} chunk(s); id={doc['id']}")

    # Ingestion is synchronous, but confirm the doc shows up ready in the manifest.
    try:
        listing = http_get_json("/corpus").get("documents", [])
        ready = any(d.get("id") == doc["id"] and d.get("status") == "ready" for d in listing)
        info(f"  /corpus lists it ready: {ready}")
    except Exception:  # noqa: BLE001
        pass

    results = {}
    for tag, q in (("IN-BOOK", in_q), ("OFF-TOPIC", off_q)):
        hr(f"   {tag} QUESTION")
        info(f"Q: {q}")
        label = rule_intent(q)
        out = chat(q)
        cites = out["citations"]

        # Intent + retrieval reporting. The server retrieves only on
        # question/advice_request; vent bypasses it entirely.
        if label is not None:
            fired = label in ("question", "advice_request")
            info(f"intent class    : {label}  (rule-based)")
            info(f"retrieval fired : {'yes' if fired else 'no (vent bypass)'}")
        else:
            info("intent class    : (classifier not importable here — inferring)")
            info(f"retrieval fired : {'yes' if cites else 'unknown'} (from citations)")

        if cites:
            for c in cites:
                info(f"citation        : {c.get('label')}")
        else:
            info("citation        : (none returned)")

        if out["error"]:
            info(f"⚠ chat error    : {out['error']}")
        info("Eva's answer    :")
        for line in (out["answer"] or "(no answer)").splitlines() or ["(empty)"]:
            print(f"      {line}")
        results[tag] = {"label": label, "citations": cites, "answer": out["answer"], "error": out["error"]}

    return {"ok": True, "results": results}


def check_tts() -> dict:
    hr("3. TTS (voice out)")
    info(f"Sentence: {TTS_SENTENCE!r}")
    try:
        from voice import tts
    except Exception as e:  # noqa: BLE001
        info(f"✗ Could not import voice.tts in this interpreter ({e}).")
        return {"ok": False, "reason": "import failed"}
    try:
        wav = tts.synthesize(TTS_SENTENCE)
    except tts.TTSUnavailable as e:
        info(f"✗ Kokoro isn't available in this interpreter: {e}")
        info("  Tip: run with system python3 (which has kokoro):")
        info(f"       python3 {Path(__file__).relative_to(REPO_ROOT)}")
        return {"ok": False, "reason": "kokoro unavailable"}
    except Exception as e:  # noqa: BLE001
        info(f"✗ Synthesis failed: {e}")
        return {"ok": False, "reason": str(e)}

    WAV_PATH.write_bytes(wav)
    info(f"✓ Wrote {len(wav)} bytes → {WAV_PATH}")
    info("  Hear it with:")
    info(f"      afplay {WAV_PATH}")
    return {"ok": True, "wav": WAV_PATH}


def check_voice_roundtrip(tts_ok: bool) -> dict:
    hr("4. VOICE ROUND-TRIP (TTS → STT)")
    if not tts_ok or not WAV_PATH.exists():
        info("✗ Skipped — no wav from the TTS step to transcribe.")
        return {"ok": False, "reason": "no wav"}

    info("Posting the synthesized wav to /stt (first call may load Whisper)…")
    status, body = http_post_multipart(
        "/stt", "file", "recording.wav", WAV_PATH.read_bytes(), "audio/wav"
    )
    if status != 200:
        info(f"✗ /stt failed (HTTP {status}): {body.get('detail', body)}")
        return {"ok": False, "reason": body.get("detail")}
    transcript = body.get("text", "")
    info(f"original     : {TTS_SENTENCE}")
    info(f"transcription: {transcript}")
    info(f"(whisper model: {body.get('model_size')}, {body.get('duration')}s of audio)")
    return {"ok": True, "transcript": transcript}


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
def summarize(rag: dict, tts_res: dict, stt_res: dict) -> None:
    hr("SUMMARY — expected vs actual")

    print("\n  [Health]")
    print("    expect: model up and answering.")
    print("    actual: ✓ model up (we got past the health gate).")

    print("\n  [Book / RAG — in-book question]")
    print("    expect: Eva answers FROM the book and returns a citation/source.")
    if rag.get("ok"):
        r = rag["results"]["IN-BOOK"]
        cite = r["citations"][0]["label"] if r["citations"] else "NONE"
        print(f"    actual: citation={cite}; intent={r['label']}")
        print(f"            answer: {_oneline(r['answer'])}")
        if not r["citations"]:
            print("            ⚠ no citation came back — check the book indexed and the")
            print("              question really is answered in it.")
    else:
        print(f"    actual: ✗ {rag.get('reason')}")

    print("\n  [Book / RAG — off-topic question]")
    print("    expect: retrieval finds nothing relevant in the book → NO citation")
    print("            returned. Eva never cites a source she didn't retrieve (she")
    print("            may still answer from general knowledge — that's fine).")
    if rag.get("ok"):
        r = rag["results"]["OFF-TOPIC"]
        cite = r["citations"][0]["label"] if r["citations"] else "NONE (good)"
        print(f"    actual: citation={cite}; intent={r['label']}")
        print(f"            answer: {_oneline(r['answer'])}")
        if r["citations"]:
            print("            ⚠ a citation came back for an off-topic question — look at")
            print("              whether the passage is actually relevant.")
    else:
        print(f"    actual: ✗ {rag.get('reason')}")

    print("\n  [TTS]")
    print("    expect: a playable wav of the fixed sentence at scripts/_eva_voice.wav.")
    if tts_res.get("ok"):
        print(f"    actual: ✓ wrote {WAV_PATH.name}; play it with  afplay {WAV_PATH}")
    else:
        print(f"    actual: ✗ {tts_res.get('reason')} (see the TTS section above)")

    print("\n  [Voice round-trip]")
    print("    expect: Whisper's transcription closely matches the original sentence.")
    if stt_res.get("ok"):
        print(f"    actual: original     : {TTS_SENTENCE}")
        print(f"            transcription: {stt_res['transcript']}")
    else:
        print(f"    actual: ✗ {stt_res.get('reason')}")

    print("\n  Nothing was committed. The test book and wav live under scripts/.")
    print()


def _oneline(text: str, limit: int = 160) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def parse_args(argv: list[str]) -> tuple[Path, str, str]:
    """Resolve the book + two questions from argv, or fall back to the built-in set."""
    if len(argv) == 0:
        TESTBOOK_PATH.write_text(BUILTIN_BOOK, encoding="utf-8")
        print(f"  (no args — using the built-in test book at {TESTBOOK_PATH.name})")
        return TESTBOOK_PATH, BUILTIN_IN_BOOK_Q, BUILTIN_OFF_TOPIC_Q
    if len(argv) == 3:
        book = Path(argv[0]).expanduser()
        if not book.exists():
            print(f"✗ Book file not found: {book}")
            sys.exit(2)
        if book.suffix.lower() not in (".pdf", ".txt", ".md"):
            print(f"✗ Unsupported book type: {book.suffix} (use .pdf, .txt, or .md)")
            sys.exit(2)
        return book, argv[1], argv[2]
    print(__doc__.strip().splitlines()[0])
    print("\nUsage:")
    print("  python3 scripts/try_eva.py")
    print("      → run with the built-in test book and questions")
    print("  python3 scripts/try_eva.py <book.pdf|.txt|.md> \"<in-book question>\" \"<off-topic question>\"")
    print("      → run against your own book and questions")
    sys.exit(2)


def main() -> None:
    print("Eva self-test  —  interpreter:", sys.executable)
    book_path, in_q, off_q = parse_args(sys.argv[1:])

    check_health()
    rag = check_book_rag(book_path, in_q, off_q)
    tts_res = check_tts()
    stt_res = check_voice_roundtrip(tts_res.get("ok", False))
    summarize(rag, tts_res, stt_res)


if __name__ == "__main__":
    main()
