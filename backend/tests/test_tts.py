"""Phase 9 — Kokoro text-to-speech (pipeline + audio fully stubbed).

These tests never load real Kokoro or torch — the suite must pass without them
installed. They stub the two seams in voice/tts.py (``_build_pipeline`` and, for
the WAV path, ``_synthesize_samples``) and assert the behaviour the plan depends
on:

* lazy load — no pipeline is built until the first synthesize;
* the resident pipeline is reused, not rebuilt, on later calls;
* the configured voice is ``af_heart`` at 24 kHz;
* a pipeline that can't load degrades to TTSUnavailable (→ text-only), not a crash;
* synthesize() produces a well-formed 24 kHz mono 16-bit WAV from samples;
* empty/whitespace input is a no-op (``b""``), never a synth call.
"""

from __future__ import annotations

import io
import wave

import pytest

from voice import tts


class _FakePipeline:
    """Stands in for Kokoro's KPipeline. Records the voice it was called with and
    yields one (graphemes, phonemes, audio) tuple of tiny float32 audio."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.speeds: list[float] = []

    def __call__(self, text, voice, speed=1.0):
        self.calls.append(voice)
        self.speeds.append(speed)
        import numpy as np

        # A short ramp so the WAV has non-trivial content to verify.
        audio = np.linspace(-0.5, 0.5, num=240, dtype=np.float32)
        yield ("g", "p", audio)


@pytest.fixture()
def tts_env(monkeypatch):
    """Reset the process-wide pipeline and stub the build seam.

    Yields the list of voices the fake pipeline was asked to synthesize, so tests
    can assert ``af_heart`` was used and that the build happened exactly once.
    """
    monkeypatch.setattr(tts, "_pipeline", None)
    fake = _FakePipeline()
    builds: list[int] = []

    def fake_build():
        builds.append(1)
        return fake

    monkeypatch.setattr(tts, "_build_pipeline", fake_build)
    return {"voices": fake.calls, "builds": builds, "speeds": fake.speeds}


def test_no_pipeline_until_first_synthesize(tts_env):
    assert not tts.is_loaded()
    assert tts_env["builds"] == []  # nothing built yet — lazy


def test_synthesize_loads_lazily_and_uses_af_heart_24k(tts_env):
    wav = tts.synthesize("Hello there, this is Eva.")
    assert tts.is_loaded()
    assert tts_env["builds"] == [1]  # built exactly once
    assert tts_env["voices"] == ["af_heart"]  # the demo voice

    # The bytes are a real WAV: mono, 16-bit, 24 kHz.
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == tts.SAMPLE_RATE == 24_000
        assert w.getnframes() == 240


def test_pipeline_is_reused_not_rebuilt(tts_env):
    tts.synthesize("First sentence.")
    tts.synthesize("Second sentence.")
    assert tts_env["builds"] == [1]  # second call reused the resident pipeline


def test_speed_is_read_from_settings_each_synthesis(tts_env, tmp_path, monkeypatch):
    """The voice-speed setting is passed to Kokoro per call (Phase 10 knob)."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    import settings

    settings.update({"voice_speed": 1.2})
    tts.synthesize("Speak a little faster, please.")
    assert tts_env["speeds"] == [1.2]


def test_empty_input_is_a_noop(tts_env):
    assert tts.synthesize("") == b""
    assert tts.synthesize("   \n  ") == b""
    assert tts_env["builds"] == []  # never even loaded the model


def test_unavailable_pipeline_raises_tts_unavailable(monkeypatch):
    monkeypatch.setattr(tts, "_pipeline", None)

    def boom():
        raise RuntimeError("kokoro not installed")

    monkeypatch.setattr(tts, "_build_pipeline", boom)
    with pytest.raises(tts.TTSUnavailable):
        tts.synthesize("This should fail to synthesize.")


def test_wav_encoding_clips_and_scales(monkeypatch):
    # Drive the WAV encoder directly with out-of-range samples; they must clip to
    # the 16-bit range rather than overflow.
    import numpy as np

    monkeypatch.setattr(tts, "_pipeline", object())  # mark "loaded" so no build
    monkeypatch.setattr(
        tts, "_synthesize_samples", lambda text: np.array([2.0, -2.0, 0.0], dtype=np.float32)
    )
    wav = tts.synthesize("clip me")
    with wave.open(io.BytesIO(wav), "rb") as w:
        frames = w.readframes(w.getnframes())
    pcm = np.frombuffer(frames, dtype="<i2")
    assert pcm.tolist() == [32767, -32767, 0]
