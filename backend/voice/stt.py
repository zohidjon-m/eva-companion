"""Speech-to-text — push-to-talk transcription via faster-whisper.

Component B-4 (EVA_SYSTEM_DESIGN §5): ``transcribe(audio) -> text``. The user
holds the mic, speaks, releases; the recorded clip is posted to ``POST /stt`` and
turned into text that lands in the input box for them to confirm and send through
the *normal* pipeline — so capture, intent, and RAG are identical whether a turn
was typed or spoken.

Three rules from CLAUDE.md / the plan shape this module:

1. **Lazy-loaded on first use, never at startup.** Loading whisper at boot
   alongside the model server would blow the 8 GB M1 Air budget (§4). The model
   is loaded on the first ``transcribe`` call and kept resident thereafter.
2. **Model size is configurable via settings.** Default ``base.en`` (int8); the
   user can switch to ``small.en`` in Settings if accuracy is poor on their
   accent. :func:`transcribe` reads the configured size each call and *reloads*
   the model when it changes — so switching takes effect on the next utterance,
   logged so the change is visible.
3. **The first-use download is the only permitted network call.** faster-whisper
   fetches its weights from HuggingFace the first time a size is used. We try a
   fully-offline load first (no network attempt at all once cached, so the
   privacy net-guard is never tripped in normal use); only if the weights are
   absent do we fall back to the permitted one-time download.

Note on hardware: faster-whisper runs on CTranslate2, which has **no Apple-Metal
backend** — STT executes on the CPU with int8 weights. On the M1 Air that is
still well inside the §9 budget (< ~1.5 s after release for a couple of
sentences), because base.en is tiny and the clip is short. The model server keeps
the GPU; STT and chat therefore don't contend for the same accelerator.
"""

from __future__ import annotations

import io
import logging
import os
import threading

import settings as app_settings
from memory import vault_dir

log = logging.getLogger("eva.voice.stt")

# Hard cap on a single recording (EVA_SYSTEM_DESIGN §9: "voice input ≤ 120 s").
# The UI stops recording at this length; the backend enforces it again so a
# malformed or oversized upload can never pin the CPU on a huge transcription.
MAX_AUDIO_SECONDS = 120

# faster-whisper resamples everything to 16 kHz mono internally; we decode to the
# same rate up front so the duration check and the transcription see one signal.
SAMPLE_RATE = 16_000

# int8 weights are the plan's default for both sizes — small RAM, fast on CPU.
# Overridable via env for a machine where a different precision is wanted, but the
# default is what the demo runs. Device "auto" resolves to CPU on Apple Silicon
# (CTranslate2 has no Metal); leaving it "auto" keeps the door open for a CUDA box.
COMPUTE_TYPE = os.environ.get("EVA_WHISPER_COMPUTE", "int8")
DEVICE = os.environ.get("EVA_WHISPER_DEVICE", "auto")

# Greedy-ish beam. faster-whisper defaults to 5; for short push-to-talk clips of
# clear English this is comfortably fast and accurate. Kept as a constant so the
# latency/quality trade-off is visible and tunable in one place.
BEAM_SIZE = 5


class STTError(RuntimeError):
    """Base class for speech-to-text failures surfaced to the API layer."""


class STTUnavailable(STTError):
    """The whisper model could not be loaded (e.g. absent and offline).

    Maps to a 503 at the API boundary: STT is a graceful fall-back to typing,
    never a crash (§9 "STT/TTS failure → fall back to text").
    """


class AudioTooLong(STTError):
    """The submitted clip exceeds the 120 s cap (EVA_SYSTEM_DESIGN §9)."""


def whisper_cache_dir() -> str:
    """Return the on-disk directory faster-whisper downloads/loads weights from.

    Kept inside the vault (``<vault>/models/whisper``) so all of Eva's local
    artifacts live in one user-owned place and a vault move carries the voice
    models with it. Created lazily by faster-whisper on first download.
    """
    return str(vault_dir() / "models" / "whisper")


# Process-wide singleton. CTranslate2 holds the weights in RAM; we load once and
# reuse. ``_lock`` guards both the (re)load and each transcription: faster-whisper
# is configured single-worker, and push-to-talk is low-concurrency, so serializing
# is simplest and safe. ``_loaded_size`` records which size is resident so we can
# detect a Settings change and reload.
_model = None
_loaded_size: str | None = None
_lock = threading.Lock()


def current_model_size() -> str:
    """Return the whisper size the *settings* currently select (not necessarily
    the one resident in memory — that only updates on the next transcribe)."""
    return str(app_settings.get("whisper_model_size"))


def is_loaded() -> bool:
    """Whether a model is resident. Used by tests and diagnostics, not the API."""
    return _model is not None


def _build_model(size: str):
    """Construct a ``WhisperModel`` for ``size``, offline-first.

    Tries a fully-local load (``local_files_only=True``) first: if the weights are
    already cached this makes **no network attempt at all**, so Eva stays silent
    on the wire in normal use. Only when the weights are absent do we fall back to
    faster-whisper's download — the single permitted first-run network call
    (CLAUDE.md rule 4). A download blocked by the net-guard (host not allow-listed)
    surfaces as :class:`STTUnavailable`, i.e. "set up voice first", not a crash.
    """
    from faster_whisper import WhisperModel

    cache = whisper_cache_dir()
    try:
        return WhisperModel(
            size, device=DEVICE, compute_type=COMPUTE_TYPE,
            download_root=cache, local_files_only=True,
        )
    except Exception:  # noqa: BLE001 — not cached yet; try the permitted download
        log.info(
            "whisper %s not cached — downloading weights "
            "(first-run, the only permitted STT network call)", size,
        )
        return WhisperModel(
            size, device=DEVICE, compute_type=COMPUTE_TYPE,
            download_root=cache, local_files_only=False,
        )


def _ensure_model():
    """Return the resident model, loading or reloading it to match settings.

    Lazy: nothing is loaded until the first call. On every call it compares the
    resident size to the configured one and reloads on a mismatch — this is the
    mechanism behind "switch to small.en in Settings → the next transcription uses
    the new model", and the reload is logged so that change is auditable. Caller
    must hold ``_lock``.
    """
    global _model, _loaded_size
    size = current_model_size()
    if _model is not None and _loaded_size == size:
        return _model

    if _model is None:
        log.info("loading whisper model %r (lazy, first use) on device=%s/%s",
                 size, DEVICE, COMPUTE_TYPE)
    else:
        log.info("whisper model size changed %r → %r; reloading", _loaded_size, size)

    try:
        _model = _build_model(size)
    except Exception as exc:  # noqa: BLE001 — degrade to "type instead", never crash
        log.exception("failed to load whisper model %r", size)
        raise STTUnavailable(
            f"Could not load the {size} speech model. "
            "Voice may not be set up yet — you can keep typing."
        ) from exc
    _loaded_size = size
    log.info("whisper model %r ready", size)
    return _model


def _decode(audio_bytes: bytes):
    """Decode arbitrary recorded audio to a 16 kHz mono float32 array.

    Uses faster-whisper's PyAV-backed decoder, so whatever container the browser's
    MediaRecorder produced (webm/opus, ogg, mp4/aac…) is handled without a system
    ffmpeg. Returns the sample array; the caller derives duration from its length.
    """
    from faster_whisper.audio import decode_audio

    return decode_audio(io.BytesIO(audio_bytes), sampling_rate=SAMPLE_RATE)


def transcribe(audio_bytes: bytes) -> dict:
    """Transcribe one recorded clip to text. The module's whole public surface.

    Steps, in order: decode the clip to 16 kHz mono; enforce the 120 s cap
    (:class:`AudioTooLong`); ensure the model matching the current setting is
    loaded (lazy, reloading on a size change); run the transcription; return the
    joined text plus the clip duration and the model size used.

    Returns ``{"text": str, "duration": float, "model_size": str}``. An empty or
    silent clip yields ``text=""`` — a no-op the UI simply ignores, not an error.
    Raises :class:`AudioTooLong` for an over-cap clip and :class:`STTUnavailable`
    when the model can't be loaded; both are translated to clean HTTP responses by
    the API layer so voice always degrades to typing rather than crashing.
    """
    audio = _decode(audio_bytes)
    duration = len(audio) / SAMPLE_RATE
    if duration > MAX_AUDIO_SECONDS:
        raise AudioTooLong(
            f"Recording is {duration:.0f}s; the limit is {MAX_AUDIO_SECONDS}s."
        )

    with _lock:
        model = _ensure_model()
        size = _loaded_size or current_model_size()
        # language is pinned to English (the *.en models are English-only anyway),
        # which skips whisper's language-detection pass and shaves latency.
        segments, _info = model.transcribe(
            audio, language="en", beam_size=BEAM_SIZE,
        )
        text = "".join(seg.text for seg in segments).strip()

    log.info("transcribed %.1fs of audio with %s → %d char(s)", duration, size, len(text))
    return {"text": text, "duration": round(duration, 2), "model_size": size}
