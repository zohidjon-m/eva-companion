#!/usr/bin/env python3
"""Manual Phase 0 check: prove the outbound socket guard blocks the open web.

Run from the backend venv:

    cd backend && source .venv/bin/activate
    python ../scripts/check_net_guard.py

Expected: the loopback attempt is allowed (connection refused is fine), and the
outbound request to example.com is BLOCKED and logged. Exits non-zero if the
guard fails to block — so it doubles as a CI smoke test.
"""

import logging
import socket
import sys

sys.path.insert(0, "backend")  # allow running from repo root or backend/

import net_guard  # noqa: E402

logging.basicConfig(level=logging.INFO)


def main() -> int:
    net_guard.install_net_guard()
    ok = True

    # 1. loopback should be allowed (the guard must not raise)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", 9))
    except net_guard.OutboundBlocked:
        print("FAIL: loopback was blocked")
        ok = False
    except OSError:
        print("OK:   loopback allowed (connection refused/timeout is expected)")
    finally:
        s.close()

    # 2. the open web must be blocked — matches the plan's requests.get example
    try:
        import requests

        requests.get("https://example.com", timeout=2)
        print("FAIL: requests.get('https://example.com') was NOT blocked")
        ok = False
    except ImportError:
        # fall back to stdlib if requests isn't installed
        try:
            socket.create_connection(("example.com", 443), timeout=2)
            print("FAIL: outbound connection to example.com was NOT blocked")
            ok = False
        except net_guard.OutboundBlocked:
            print("OK:   outbound connection to example.com blocked")
    except Exception as exc:  # requests wraps OutboundBlocked in ConnectionError
        if "net guard" in str(exc).lower():
            print("OK:   requests.get('https://example.com') blocked by net guard")
        else:
            print(f"FAIL: blocked for the wrong reason: {exc!r}")
            ok = False

    print("\nPASS" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
