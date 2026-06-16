"""Phase 10 — the privacy audit + vault reveal endpoints and the /health voices block.

The Offline ✓ badge reads guard truth from /health; the Settings privacy panel
calls /privacy/audit for the on-demand "prove it". These tests assert the shape
and the holding/violation verdict without making any real outbound connection
(the guard raises before a packet leaves the box).
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

import net_guard
from app import app

client = TestClient(app)


def test_health_reports_voice_presence():
    """/health carries a voices block the first-run screen reads for live ✓."""
    body = client.get("/health").json()
    assert set(body["voices"]) == {"stt", "tts"}
    assert isinstance(body["voices"]["stt"], bool)
    assert isinstance(body["voices"]["tts"], bool)
    # The guard detail now includes the violation bookkeeping the badge reads.
    assert "violations" in body["net_guard_detail"]


def test_privacy_audit_clean_when_nothing_blocked():
    net_guard.reset_violations()
    body = client.get("/privacy/audit").json()
    assert body["installed"] is True
    assert body["violations"] == 0
    assert "No outbound" in body["verdict"]


def test_privacy_audit_reports_a_blocked_attempt():
    net_guard.reset_violations()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    with pytest.raises(net_guard.OutboundBlocked):
        s.connect(("1.1.1.1", 443))
    s.close()

    body = client.get("/privacy/audit").json()
    assert body["violations"] == 1
    assert body["last_blocked"] == "1.1.1.1"
    assert "blocked" in body["verdict"].lower()
    net_guard.reset_violations()


def test_vault_reveal_returns_the_path(tmp_path, monkeypatch):
    """Reveal reports the vault path; it does not open Finder for a missing dir."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "nope"))
    body = client.post("/vault/reveal").json()
    assert body["path"].endswith("nope")
    assert body["opened"] is False  # directory doesn't exist → not opened
