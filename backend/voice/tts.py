"""Text-to-speech — Eva's voice, synthesized locally with Kokoro (Phase 9).

Component B-5 (EVA_SYSTEM_DESIGN §5): ``synthesize(text) -> wav``. The chat loop
feeds Eva's streamed reply through :mod:`voice.sentence_queue`, which buffers it to
sentence boundaries and calls :func:`synthesize` once per sentence; the resulting
24 kHz WAV chunks are emitted over the chat WebSocket and played back in order, so
Eva starts speaking almost as soon as she starts writing.

The same three rules that shape :mod:`voice.stt` shape this module:

1. **Lazy-loaded on first use, never at startup.** Loading Kokoro at boot alongside
   the model server (and possibly whisper) would blow the 8 GB M1 Air budget (§4).
   The pipeline is built on the first :func:`synthesize` call and kept resident.
2. **One fixed voice for the demo.** ``af_heart`` (plan Phase 9). Phase 10's
   Settings screen adds a voice/speed knob; the seam is :data:`VOICE` here.
3. **The first-use download is the only permitted network call.** Kokoro fetches
   its weights/voice once; thereafter everything is local and the privacy net-guard
   is never tripped in normal use.

Like the model server, Kokoro (and its torch dependency) need not live in the
backend's own venv — set ``$EVA_TTS_PYTHON`` notwithstanding, the import is done
in-process here, so the interpreter running the backend must have ``kokoro``
available when voice is used. If it can't be loaded, voice degrades to text
(:class:`TTSUnavailable`), never a crash (§9 "STT/TTS failure → fall back to text").
"""

from __future__ import annotations

import io
import logging
import os
import threading
import wave

import settings as app_settings

# Force the HuggingFace stack offline BEFORE Kokoro/huggingface_hub import, exactly
# as memory/vector.py does for the embedder. Two reasons: (1) privacy — no update
# check or download is attempted at runtime; (2) cleanliness — without this, Kokoro
# probes HF on every load and the net-guard logs a burst of BLOCKED-connection
# warnings (it still recovers from cache, but noisily). The one-time weight/voice
# download happens in a separate process (scripts/download_kokoro_model.py) that
# does NOT set these, so it can fetch from HF.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

log = logging.getLogger("eva.voice.tts")

# Kokoro emits 24 kHz mono float32 audio (its native rate). We keep the WAV at that
# rate end-to-end — no resampling — so the browser plays exactly what Kokoro made.
SAMPLE_RATE = 24_000

# The demo voice (plan Phase 9). ``af_*`` are Kokoro's American-English voices;
# ``af_heart`` is warm and conversational, matching Eva's persona. A single seam
# so Phase 10 can wire a Settings dropdown without touching the synth path.
VOICE = "af_heart"

# Kokoro language pack. ``"a"`` = American English (English-only, per CLAUDE.md).
LANG_CODE = "a"


class TTSError(RuntimeError):
    """Base class for text-to-speech failures surfaced to the voice layer."""


class TTSUnavailable(TTSError):
    """Kokoro could not be loaded (e.g. not installed, or weights absent offline).

    The sentence queue catches this once, tells the client voice is unavailable,
    and continues text-only — voice is always a graceful fall-back to typing/reading,
    never a crash (§9).
    """


# Process-wide singleton. Kokoro's pipeline holds its weights in RAM; we build it
# once and reuse. ``_lock`` guards both the build and each synthesis call: Kokoro is
# not guaranteed thread-safe and TTS runs on a worker thread (see sentence_queue),
# so serializing keeps it simple and safe. Synthesis is naturally low-concurrency —
# sentences are produced one at a time by a single reply stream.
_pipeline = None
_lock = threading.Lock()


def is_loaded() -> bool:
    """Whether the Kokoro pipeline is resident. For diagnostics/tests, not the API."""
    return _pipeline is not None


def current_speed() -> float:
    """Return the speaking rate from settings (Kokoro ``speed``), default 1.0.

    Read fresh on every synthesis — exactly like :func:`voice.stt.current_model_size`
    — so changing the speed in Settings takes effect on the next sentence Eva
    speaks, with no restart and no pipeline reload (only the model weights are
    cached; speed is a per-call argument).
    """
    try:
        return float(app_settings.get("voice_speed"))
    except (TypeError, ValueError):
        return 1.0


def weights_present() -> bool:
    """Best-effort: are Kokoro's weights already on disk (so voice can load offline)?

    Used by the first-run setup screen to show a live "voice ready ✓" without
    importing torch/Kokoro. Kokoro fetches ``hexgrad/Kokoro-82M`` into the
    HuggingFace hub cache, so we look for that snapshot directory. This is a
    presence heuristic, not a guarantee the load will succeed; it never raises.
    """
    from pathlib import Path

    hub = os.environ.get("HF_HOME")
    roots = []
    if hub:
        roots.append(Path(hub) / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    try:
        for root in roots:
            snap = root / "models--hexgrad--Kokoro-82M" / "snapshots"
            if snap.is_dir() and any(snap.iterdir()):
                return True
    except OSError:
        pass
    return False


def _build_pipeline():
    """Construct Kokoro's ``KPipeline`` for English. Seam stubbed in tests.

    Imported lazily so merely importing this module (and thus ``app``) never drags
    in torch/Kokoro — the heavy load happens only on the first real synthesis.
    """
    from kokoro import KPipeline

    return KPipeline(lang_code=LANG_CODE)


def _ensure_pipeline():
    """Return the resident pipeline, building it on first use. Caller holds ``_lock``.

    Lazy: nothing loads until the first :func:`synthesize`. A failure to load
    (Kokoro missing, weights absent + offline) is turned into :class:`TTSUnavailable`
    so the caller degrades to text rather than crashing.
    """
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    log.info("loading Kokoro TTS pipeline (lazy, first use); voice=%s", VOICE)
    try:
        _pipeline = _build_pipeline()
    except Exception as exc:  # noqa: BLE001 — degrade to text, never crash (§9)
        log.exception("failed to load Kokoro TTS")
        raise TTSUnavailable(
            "Could not load Eva's voice (Kokoro). Run the one-time setup "
            "(backend/.venv/bin/python scripts/download_kokoro_model.py) to fetch "
            "the voice; until then you can keep reading her replies as text."
        ) from exc
    log.info("Kokoro TTS pipeline ready")
    return _pipeline


def prewarm() -> bool:
    """Eagerly load the Kokoro pipeline so the first voiced reply doesn't stall.

    The first :func:`synthesize` otherwise pays the full pipeline build (torch +
    weights, a few seconds) mid-conversation. Calling this ahead of time — when
    voice is enabled, on a background thread — moves that cost off the user's first
    spoken turn. Best-effort: it shares ``synthesize``'s lock and swallows
    :class:`TTSUnavailable` (weights absent / offline), so a failed prewarm simply
    leaves voice lazy and text-only, exactly as before. Returns whether the
    pipeline is now resident.
    """
    with _lock:
        try:
            _ensure_pipeline()
            return True
        except TTSUnavailable:
            return False


def _synthesize_samples(text: str):
    """Synthesize ``text`` to a 1-D float32 array of 24 kHz samples. Seam for tests.

    Kokoro yields the audio in one or more segments; for a single sentence (≤ 80
    words, by the sentence queue's contract) that's typically one segment, but we
    concatenate defensively so a longer line still comes back whole and in order.
    Caller holds ``_lock``.
    """
    import numpy as np

    pipeline = _ensure_pipeline()
    speed = current_speed()
    parts: list = []
    for _graphemes, _phonemes, audio in pipeline(text, voice=VOICE, speed=speed):
        # Kokoro returns a torch tensor (or array-like) per segment; normalise to a
        # 1-D float32 numpy array without assuming torch is importable here.
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        parts.append(np.asarray(audio, dtype=np.float32).reshape(-1))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts)


def _wav_bytes(samples) -> bytes:
    """Encode a float32 [-1, 1] sample array as a 24 kHz mono 16-bit PCM WAV.

    Pure stdlib (``wave``) + numpy, so there's no soundfile/scipy dependency and
    the bytes are a self-contained WAV the browser can play directly. An empty
    array yields ``b""`` (the caller treats that as "nothing to play").
    """
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        return b""
    # Clip out-of-range values, then scale to signed 16-bit PCM.
    clipped = np.clip(samples, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm16.tobytes())
    return buf.getvalue()


def synthesize(text: str) -> bytes:
    """Synthesize one sentence to a 24 kHz mono WAV chunk. The module's public API.

    Returns the WAV bytes, or ``b""`` for empty/whitespace input (a no-op the
    caller skips). Lazy-loads Kokoro on the first call and reuses it thereafter;
    the whole call is serialized under ``_lock`` because Kokoro isn't guaranteed
    thread-safe and this runs on a worker thread. Raises :class:`TTSUnavailable`
    when the pipeline can't be loaded, so the voice layer can fall back to text.
    """
    text = (text or "").strip()
    if not text:
        return b""
    with _lock:
        samples = _synthesize_samples(text)
        wav = _wav_bytes(samples)
    log.debug("synthesized %d char(s) → %d wav byte(s)", len(text), len(wav))
    return wav
