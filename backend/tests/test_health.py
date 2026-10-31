"""Test the /health endpoint shape (Phase 0 scaffold + Phase 1 model status)."""

from fastapi.testclient import TestClient

from app import app
from llm import server as llm_server

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["net_guard"] is True
    # Phase 1: model_present is now a REAL check of the GGUF on disk (no longer a
    # hardcoded stub). It must agree with the server module's own detection.
    assert body["model_present"] is llm_server.model_present()
    # The model block carries the path + endpoint so the shell can guide setup.
    assert body["model"]["model_path"].endswith(".gguf")
    assert body["model"]["endpoint"].endswith(":11500")


def test_health_reports_download_hint_when_model_missing(monkeypatch):
    """When the GGUF is absent, /health stays up and surfaces the download hint."""
    monkeypatch.setattr(llm_server, "model_present", lambda: False)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["model_present"] is False
    assert "download_model.py" in body["model"]["hint"]


def test_health_allows_packaged_windows_origin():
    """Let the Windows Tauri webview reach its local backend after packaging."""
    response = client.get("/health", headers={"Origin": "http://tauri.localhost"})
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_health_does_not_allow_an_external_web_origin():
    """Keep the desktop-origin fix from allowing arbitrary websites to read Eva."""
    response = client.get("/health", headers={"Origin": "https://unrelated.example"})
    assert "access-control-allow-origin" not in response.headers
