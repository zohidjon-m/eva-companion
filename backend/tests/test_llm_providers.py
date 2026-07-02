"""Hybrid LLM provider registry and secret-handling tests."""

from __future__ import annotations

import json
import importlib


def test_provider_registry_lists_expected_adapters():
    from llm import providers

    ids = {p["provider_id"] for p in providers.list_provider_metadata()}
    assert {
        "local_llamacpp",
        "local_openai_compatible",
        "openai_compatible_api",
        "anthropic",
        "gemini",
    }.issubset(ids)


def test_session_api_key_not_written_to_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    import settings
    from llm import providers

    importlib.reload(settings)
    settings.update({
        "ai_provider_id": "openai_compatible_api",
        "ai_mode": "online",
        "api_base_url": "https://api.example.test/v1",
        "api_model": "example-model",
    })
    providers.set_session_api_key("openai_compatible_api", "secret-value")

    assert providers.api_key_for("openai_compatible_api") == "secret-value"
    on_disk = json.loads(settings._settings_path().read_text())
    assert "api_key" not in on_disk
    assert "secret-value" not in json.dumps(on_disk)
