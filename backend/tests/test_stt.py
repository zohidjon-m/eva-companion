"""Phase 8 — speech-to-text via faster-whisper (model + audio fully stubbed).

These tests never load a real Whisper model or decode real audio — faster-whisper
isn't required to be installed for the suite to pass. Instead they stub the two
seams in voice/stt.py (``_build_model`` and ``_decode``) and assert the behaviour
the plan's checks depend on:

* lazy load — no model is built until the first transcribe;
* the 120 s cap is enforced (→ AudioTooLong → 413);
* a model that can't load degrades to STTUnavailable (→ 503), never a crash;
* switching the whisper size in settings makes the NEXT transcription reload the
  new model (the plan's "confirm via log" check, asserted structurally here);
* the /stt endpoint returns the transcribed text for a good clip.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import settings as app_settings
from voice import stt


class _FakeSegment:
    def __init__(self, text: str):
        self.text = text


class _FakeModel:
    """Stands in for faster-whisper's WhisperModel; records the size it was built
    for and returns one fixed segment regardless of the (ignored) audio."""

    def __init__(self, size: str):
        self.size = size

    def transcribe(self, audio, language="en", beam_size=5):
        return ([_FakeSegment("Two sentences. Here they are.")], {"duration": 3.0})


@pytest.fixture()
def stt_env(tmp_path, monkeypatch):
    """Reset stt's process-wide model and point settings at a temp vault.

    Yields a ``built`` list recording each size ``_build_model`` was called with,
    so tests can assert lazy-loading and reload-on-change. ``_decode`` is stubbed
    to return a list whose length encodes the requested duration (1 s = SAMPLE_RATE
    samples), so the 120 s cap can be exercised without real audio.
    """
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    monkeypatch.setattr(stt, "_model", None)
    monkeypatch.setattr(stt, "_loaded_size", None)

    built: list[str] = []

    def fake_build(size: str):
        built.append(size)
        return _FakeModel(size)

    monkeypatch.setattr(stt, "_build_model", fake_build)

    # Default: a short 3-second clip. Individual tests override the duration.
    def fake_decode(audio_bytes: bytes, seconds: float = 3.0):
        return [0.0] * int(stt.SAMPLE_RATE * seconds)

    monkeypatch.setattr(stt, "_decode", lambda data: fake_decode(data))
    return built


def test_no_model_loaded_until_first_transcribe(stt_env):
    assert not stt.is_loaded()
    assert stt_env == []  # nothing built yet — lazy


def test_transcribe_returns_text_and_loads_lazily(stt_env):
    out = stt.transcribe(b"fake-audio")
    assert out["text"] == "Two sentences. Here they are."
    assert out["model_size"] == "base.en"  # the default
    assert stt_env == ["base.en"]  # built exactly once, on first use
    assert stt.is_loaded()


def test_model_is_reused_not_rebuilt(stt_env):
    stt.transcribe(b"a")
    stt.transcribe(b"b")
    assert stt_env == ["base.en"]  # second call reused the resident model


def test_size_change_in_settings_reloads_on_next_transcribe(stt_env):
    stt.transcribe(b"a")
    assert stt_env == ["base.en"]
    # The user switches to small.en in Settings…
    app_settings.update({"whisper_model_size": "small.en"})
    out = stt.transcribe(b"b")
    # …and the NEXT transcription reloads with the new model.
    assert stt_env == ["base.en", "small.en"]
    assert out["model_size"] == "small.en"


def test_120s_cap_enforced(stt_env, monkeypatch):
    monkeypatch.setattr(stt, "_decode", lambda data: [0.0] * int(stt.SAMPLE_RATE * 130))
    with pytest.raises(stt.AudioTooLong):
        stt.transcribe(b"too-long")


def test_unavailable_model_raises_stt_unavailable(stt_env, monkeypatch):
    def boom(size):
        raise RuntimeError("weights missing and offline")

    monkeypatch.setattr(stt, "_build_model", boom)
    with pytest.raises(stt.STTUnavailable):
        stt.transcribe(b"a")


# --- endpoint ------------------------------------------------------------- #

def test_stt_endpoint_good_clip(stt_env):
    from app import app

    client = TestClient(app)
    r = client.post("/stt", files={"file": ("clip.webm", b"fake-audio", "audio/webm")})
    assert r.status_code == 200
    assert r.json()["text"] == "Two sentences. Here they are."


def test_stt_endpoint_empty_upload_is_400(stt_env):
    from app import app

    client = TestClient(app)
    r = client.post("/stt", files={"file": ("clip.webm", b"", "audio/webm")})
    assert r.status_code == 400


def test_stt_endpoint_over_cap_is_413(stt_env, monkeypatch):
    monkeypatch.setattr(stt, "_decode", lambda data: [0.0] * int(stt.SAMPLE_RATE * 130))
    from app import app

    client = TestClient(app)
    r = client.post("/stt", files={"file": ("clip.webm", b"x" * 100, "audio/webm")})
    assert r.status_code == 413


def test_stt_endpoint_unavailable_is_503(stt_env, monkeypatch):
    monkeypatch.setattr(stt, "_build_model", lambda size: (_ for _ in ()).throw(RuntimeError("nope")))
    from app import app

    client = TestClient(app)
    r = client.post("/stt", files={"file": ("clip.webm", b"x" * 100, "audio/webm")})
    assert r.status_code == 503
