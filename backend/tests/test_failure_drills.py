"""Phase 15 — the failure drills, as a CI test.

This is the pytest face of ``scripts/demo_drills.py``: it runs the same automated
drills (model down, over-cap upload/audio, rapid-fire burst, offline guard) and
asserts every one fails *soft*. Keeping the checks in the script and merely
asserting them here means the demo-day report and the test suite can never drift.

The drill harness redirects the vault to a temp dir via ``EVA_VAULT_DIR``; we
snapshot and restore that env var so running this test never leaks the temp vault
into the other tests in the same session.
"""

import os
import sys
from pathlib import Path

# Make ``scripts/`` importable (it sits beside ``backend/``).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import demo_drills  # noqa: E402


def test_all_failure_drills_pass():
    """Every automated failure drill must report a soft, graceful outcome."""
    saved = os.environ.get("EVA_VAULT_DIR")
    try:
        results = demo_drills.run_all()
    finally:
        if saved is None:
            os.environ.pop("EVA_VAULT_DIR", None)
        else:
            os.environ["EVA_VAULT_DIR"] = saved

    assert results, "no drills ran"
    failed = [d for d in results if d.passed is False]
    assert not failed, "soft-fail drills regressed: " + "; ".join(
        f"{d.name} → {d.detail}" for d in failed
    )
