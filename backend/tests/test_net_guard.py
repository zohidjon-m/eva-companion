"""Tests for the outbound network guard (Phase 0).

Verifies the privacy hard law: outbound connections are blocked except to
loopback and the single EVA_ALLOW_HOST download host. These tests do not make
real outbound connections — the guard raises before the socket leaves the box.
"""

import socket

import pytest

import net_guard


@pytest.fixture(autouse=True)
def guard_installed():
    """Ensure the guard is installed for every test in this module."""
    net_guard.install_net_guard()
    assert net_guard.is_installed()


def test_loopback_ip_is_allowed_to_attempt():
    """Connecting to a loopback IP is permitted (refusal is fine, blocking is not)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        # Nothing may be listening on this port; we only assert the guard does
        # NOT raise OutboundBlocked. A real ConnectionRefused/timeout is OK.
        s.connect(("127.0.0.1", 9))
    except net_guard.OutboundBlocked:
        pytest.fail("loopback connection was wrongly blocked")
    except OSError:
        pass  # refused/timed out — that's the network, not the guard
    finally:
        s.close()


def test_localhost_name_is_allowed_to_attempt():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect(("localhost", 9))
    except net_guard.OutboundBlocked:
        pytest.fail("localhost connection was wrongly blocked")
    except OSError:
        pass
    finally:
        s.close()


def test_public_ip_is_blocked():
    """A non-loopback IP literal must be blocked before any packet leaves."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    with pytest.raises(net_guard.OutboundBlocked):
        s.connect(("1.1.1.1", 443))
    s.close()


def test_public_hostname_is_blocked_via_create_connection():
    """socket.create_connection -> connect path is also guarded."""
    with pytest.raises(net_guard.OutboundBlocked):
        socket.create_connection(("example.com", 443), timeout=0.2)


def test_connect_ex_is_blocked():
    """connect_ex (used by some libraries) is guarded too."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    with pytest.raises(net_guard.OutboundBlocked):
        s.connect_ex(("8.8.8.8", 53))
    s.close()


def test_requests_is_blocked():
    """The exact scenario from the plan: requests.get to a public host fails."""
    requests = pytest.importorskip("requests")
    with pytest.raises(Exception) as exc_info:
        requests.get("https://example.com", timeout=1)
    # requests wraps the OSError in ConnectionError; the guard's message rides along.
    assert "net guard" in str(exc_info.value).lower() or isinstance(
        exc_info.value, net_guard.OutboundBlocked
    )


def test_blocked_attempt_is_counted_and_recorded():
    """Phase 10: every block bumps the violation count + remembers the target,
    so the Offline ✓ badge can turn warning-red when something tries to leave."""
    net_guard.reset_violations()
    assert net_guard.violations() == 0
    assert net_guard.allow_summary()["last_blocked"] is None

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    with pytest.raises(net_guard.OutboundBlocked):
        s.connect(("1.1.1.1", 443))
    s.close()

    assert net_guard.violations() == 1
    summary = net_guard.allow_summary()
    assert summary["violations"] == 1
    assert summary["last_blocked"] == "1.1.1.1"


def test_loopback_does_not_count_as_a_violation():
    """An allowed (loopback) attempt must never register as a blocked call."""
    net_guard.reset_violations()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", 9))
    except net_guard.OutboundBlocked:
        pytest.fail("loopback wrongly blocked")
    except OSError:
        pass
    finally:
        s.close()
    assert net_guard.violations() == 0
