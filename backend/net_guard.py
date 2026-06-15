"""Outbound network kill-switch — Eva's privacy hard law, enforced in code.

CLAUDE.md rule 4: at runtime Eva makes *no* outbound network calls. The only
permitted exception is the first-run model/voice download, whose host is named
explicitly via the ``EVA_ALLOW_HOST`` environment variable.

This module monkeypatches the standard library socket layer so that any attempt
to open a connection to a host that is not loopback and not the single
allow-listed download host raises :class:`OutboundBlocked` and is logged. It is
installed at backend startup *in Phase 0* — not deferred to Phase 10 — so that
no dependency can quietly phone home during development. The "Offline ✓" badge
UI is wired later (Phase 10), but the block that makes the badge truthful lives
here from day one.

Why patch ``socket`` rather than a single HTTP client: every Python networking
library (requests, httpx, urllib, aiohttp, chromadb, huggingface_hub, ...)
ultimately calls ``socket.connect``. Guarding the choke point catches them all,
including ones we have not imported yet.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket

log = logging.getLogger("eva.net_guard")

# Originals captured at import time, before any patching, so the guard can both
# delegate to them and use the real resolver to pre-resolve the allow host.
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_getaddrinfo = socket.getaddrinfo

# Hostnames that always mean "this machine" and are therefore always allowed.
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", ""}

_installed = False
_allow_name: str | None = None
_allow_ips: set[str] = set()


class OutboundBlocked(OSError):
    """Raised when code attempts a forbidden outbound network connection.

    Subclasses :class:`OSError` so callers that already handle connection
    errors degrade gracefully instead of seeing an unfamiliar exception type.
    """


def _host_from_address(address: object) -> str | None:
    """Return the host portion of a socket address, or ``None`` if not an IP socket.

    AF_INET addresses are ``(host, port)`` and AF_INET6 are
    ``(host, port, flowinfo, scope_id)``; AF_UNIX addresses are a path string.
    Only the tuple forms carry a network host we need to police.
    """
    if isinstance(address, (tuple, list)) and address:
        return str(address[0])
    return None


def _is_allowed(host: str | None) -> bool:
    """Decide whether an outbound connection to ``host`` is permitted.

    Allowed: loopback (by name or IP literal) and the single ``EVA_ALLOW_HOST``
    download host (matched by name and by every IP it currently resolves to,
    since most libraries resolve the name to an IP before calling connect).
    Everything else is denied — privacy is the default, access is the exception.
    """
    if host is None:
        return True  # AF_UNIX / non-network socket — nothing to police.

    name = host.lower()
    if name in _LOOPBACK_NAMES:
        return True

    # IP literal: the common case, because libraries usually resolve the
    # hostname to an address before handing it to connect().
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_loopback:
            return True
        return host in _allow_ips

    # Bare hostname (some libraries let connect() do the DNS): allow only the
    # explicitly named download host.
    if _allow_name is not None and name == _allow_name.lower():
        return True
    return False


def _deny(address: object) -> "OutboundBlocked":
    """Log a blocked attempt and build the exception to raise."""
    log.error(
        "net_guard: BLOCKED outbound connection to %r (privacy hard law; "
        "set EVA_ALLOW_HOST to permit a first-run download host)",
        address,
    )
    host = _host_from_address(address)
    return OutboundBlocked(
        f"Eva net guard blocked an outbound connection to {host!r}. "
        "Runtime network access is forbidden; only a first-run download to "
        "EVA_ALLOW_HOST is allowed."
    )


def _guarded_connect(self, address, *args, **kwargs):
    if not _is_allowed(_host_from_address(address)):
        raise _deny(address)
    return _orig_connect(self, address, *args, **kwargs)


def _guarded_connect_ex(self, address, *args, **kwargs):
    if not _is_allowed(_host_from_address(address)):
        raise _deny(address)
    return _orig_connect_ex(self, address, *args, **kwargs)


def install_net_guard() -> None:
    """Install the outbound socket block. Idempotent and safe to call repeatedly.

    Reads ``EVA_ALLOW_HOST`` once and pre-resolves it (using the real resolver)
    so that connections to its current IP addresses are recognised as allowed.
    Call this as early as possible at backend startup, before any networking
    library has a chance to run.
    """
    global _installed, _allow_name, _allow_ips
    if _installed:
        return

    _allow_name = os.environ.get("EVA_ALLOW_HOST") or None
    _allow_ips = set()
    if _allow_name:
        try:
            for info in _orig_getaddrinfo(_allow_name, None):
                _allow_ips.add(info[4][0])
            log.info(
                "net_guard: allow-listed first-run download host %s -> %s",
                _allow_name,
                sorted(_allow_ips),
            )
        except OSError as exc:
            # Cannot resolve now (e.g. offline): still allow by name, and it
            # will resolve when the download actually runs.
            log.warning(
                "net_guard: could not pre-resolve EVA_ALLOW_HOST=%s (%s); "
                "allowing by name only",
                _allow_name,
                exc,
            )

    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    _installed = True
    log.info(
        "net_guard: outbound socket block active (loopback + %s only)",
        _allow_name or "no download host",
    )


def is_installed() -> bool:
    """Return whether the guard is currently active. Used by /health and tests."""
    return _installed


def allow_summary() -> dict:
    """Return the current allow-list state for diagnostics and the /health view."""
    return {
        "installed": _installed,
        "allow_host": _allow_name,
        "allow_ips": sorted(_allow_ips),
    }
