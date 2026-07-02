"""Phase 8 — the single settings store + the GET/PATCH /settings endpoints.

Covers: defaults when no file exists, a validated round-trip write, rejection of
an unknown key and an invalid value, and that a hand-edited junk/invalid file
degrades to defaults rather than breaking. All pointed at a temp vault.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Fresh settings module pointed at a temp vault (no file yet)."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    import settings

    return settings


def test_defaults_when_no_file(store):
    s = store.load()
    assert s["whisper_model_size"] == "base.en"
    # Phase 10 voice knobs default to off / natural pace.
    assert s["voice_enabled"] is False
    assert s["voice_speed"] == 1.0
    assert s["ai_provider_id"] == "local_llamacpp"
    assert s["ai_mode"] == "local"
    assert "api_key" not in s


def test_voice_enabled_round_trip(store):
    assert store.update({"voice_enabled": True})["voice_enabled"] is True
    assert store.get("voice_enabled") is True


def test_voice_speed_round_trip_and_range(store):
    assert store.update({"voice_speed": 1.15})["voice_speed"] == 1.15
    # Out of range (too fast / too slow) is rejected.
    with pytest.raises(ValueError):
        store.update({"voice_speed": 2.0})
    with pytest.raises(ValueError):
        store.update({"voice_speed": 0.1})
    # A non-number is rejected too (and a bool is not a valid speed).
    with pytest.raises(ValueError):
        store.update({"voice_speed": "fast"})
    with pytest.raises(ValueError):
        store.update({"voice_speed": True})


def test_out_of_range_stored_speed_falls_back_to_default(store):
    path = store._settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"voice_speed": 9.0}))
    assert store.load()["voice_speed"] == 1.0


def test_ranges_lists_voice_speed_bounds(store):
    r = store.ranges()["voice_speed"]
    assert r["min"] == store.VOICE_SPEED_MIN
    assert r["max"] == store.VOICE_SPEED_MAX
    assert r["step"] == store.VOICE_SPEED_STEP


def test_update_round_trip_persists(store):
    updated = store.update({"whisper_model_size": "small.en"})
    assert updated["whisper_model_size"] == "small.en"
    # Persisted to <vault>/settings.json and reread identically.
    assert store.get("whisper_model_size") == "small.en"
    on_disk = json.loads(store._settings_path().read_text())
    assert on_disk["whisper_model_size"] == "small.en"


def test_ai_provider_config_persists_without_secret(store):
    updated = store.update({
        "ai_provider_id": "openai_compatible_api",
        "ai_mode": "online",
        "api_base_url": "https://api.example.test/v1",
        "api_model": "example-model",
    })
    assert updated["ai_provider_id"] == "openai_compatible_api"
    on_disk = json.loads(store._settings_path().read_text())
    assert on_disk["api_base_url"] == "https://api.example.test/v1"
    assert "api_key" not in on_disk


def test_update_rejects_unknown_key(store):
    with pytest.raises(ValueError):
        store.update({"nonsense": 1})


def test_update_rejects_invalid_value(store):
    with pytest.raises(ValueError):
        store.update({"whisper_model_size": "huge.en"})


def test_invalid_stored_value_falls_back_to_default(store):
    path = store._settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"whisper_model_size": "huge.en"}))
    assert store.load()["whisper_model_size"] == "base.en"


def test_unreadable_file_falls_back_to_default(store):
    path = store._settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert store.load()["whisper_model_size"] == "base.en"


def test_options_lists_whisper_sizes(store):
    assert store.options()["whisper_model_size"] == ["base.en", "small.en"]


# --- endpoints ------------------------------------------------------------- #

def test_get_settings_endpoint(store):
    from app import app

    client = TestClient(app)
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["settings"]["whisper_model_size"] == "base.en"
    assert body["options"]["whisper_model_size"] == ["base.en", "small.en"]
    # Phase 10: the screen also gets numeric ranges + the vault path to display.
    assert body["ranges"]["voice_speed"]["min"] == store.VOICE_SPEED_MIN
    assert body["vault_path"].endswith("local_vault")


def test_patch_settings_endpoint_updates_voice(store):
    from app import app

    client = TestClient(app)
    r = client.patch("/settings", json={"voice_enabled": True, "voice_speed": 1.1})
    assert r.status_code == 200
    body = r.json()["settings"]
    assert body["voice_enabled"] is True
    assert body["voice_speed"] == 1.1


def test_patch_settings_endpoint_rejects_out_of_range_speed(store):
    from app import app

    client = TestClient(app)
    r = client.patch("/settings", json={"voice_speed": 5.0})
    assert r.status_code == 400


def test_patch_settings_endpoint_updates(store):
    from app import app

    client = TestClient(app)
    r = client.patch("/settings", json={"whisper_model_size": "small.en"})
    assert r.status_code == 200
    assert r.json()["settings"]["whisper_model_size"] == "small.en"
    assert store.get("whisper_model_size") == "small.en"


def test_patch_settings_endpoint_rejects_bad_value(store):
    from app import app

    client = TestClient(app)
    r = client.patch("/settings", json={"whisper_model_size": "huge.en"})
    assert r.status_code == 400
