"""Shared test helpers for Eva backend tests.

The helpers here patch only test-time seams. Production runtime checks stay in
place; socket tests that mock the model stream explicitly opt into a ready
provider so they do not depend on a real llama-server binary.
"""

from __future__ import annotations


def force_local_llamacpp_provider(monkeypatch) -> None:
    """Pin tests to the local llama.cpp provider instead of user settings."""

    from llm import providers as llm_providers

    monkeypatch.setattr(
        llm_providers,
        "selected_provider_id",
        lambda: llm_providers.LOCAL_LLAMA_CPP,
    )


def stub_chat_provider_ready(monkeypatch) -> None:
    """Make mocked /chat stream tests pass provider readiness gates."""

    import app as app_mod
    from llm import client as llm_client
    from llm import providers as llm_providers
    from llm import server as llm_server

    force_local_llamacpp_provider(monkeypatch)
    monkeypatch.setattr(llm_server, "model_present", lambda: True)
    monkeypatch.setattr(llm_client, "provider_configured", lambda: True)
    monkeypatch.setattr(app_mod._llama, "is_running", lambda: True)

    async def ready_status() -> llm_providers.ProviderStatus:
        return llm_providers.ProviderStatus(
            llm_providers.LOCAL_LLAMA_CPP,
            True,
            True,
            "Provider reachable.",
        )

    monkeypatch.setattr(llm_client, "provider_status", ready_status)
