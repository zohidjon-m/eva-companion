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


def test_update_round_trip_persists(store):
    updated = store.update({"whisper_model_size": "small.en"})
    assert updated["whisper_model_size"] == "small.en"
    # Persisted to <vault>/settings.json and reread identically.
    assert store.get("whisper_model_size") == "small.en"
    on_disk = json.loads(store._settings_path().read_text())
    assert on_disk["whisper_model_size"] == "small.en"


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
