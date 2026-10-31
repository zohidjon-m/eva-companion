"""Protect personal vaults when preparing the optional fictional showcase."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("eva_showcase", ROOT / "scripts/build_showcase.py")
showcase = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(showcase)
FIXTURE = ROOT / "scripts/fixtures/showcase_alex.json"


def test_existing_vault_is_rejected_without_changes(tmp_path):
    """Refuse an unclaimed journal so setup cannot mix fictional and private data."""
    journal = tmp_path / "journal"
    journal.mkdir()
    original = journal / "2026-10-01.md"
    original.write_text("Private entry", encoding="utf-8")
    with pytest.raises(ValueError, match="Existing Eva data"):
        showcase.prepare_vault(tmp_path, FIXTURE, "2026-10-31")
    assert original.read_text(encoding="utf-8") == "Private entry"
    assert not (tmp_path / showcase.MARKER).exists()


def test_same_fixture_resumes_without_replacing_progress(tmp_path):
    """Keep entry IDs and completed stages stable after an interrupted invocation."""
    _, state = showcase.prepare_vault(tmp_path, FIXTURE, "2026-10-31")
    state["entries"]["introduction"] = {"id": "stable-demo-id"}
    state["completed_stages"].append("baseline")
    showcase.write_json(tmp_path / showcase.MARKER, state)
    _, resumed = showcase.prepare_vault(tmp_path, FIXTURE, "2026-10-31")
    assert resumed == state


def test_changed_date_or_fixture_requires_new_vault(tmp_path):
    """Reject incompatible replays rather than silently changing a demo's history."""
    vault = tmp_path / "vault"
    showcase.prepare_vault(vault, FIXTURE, "2026-10-31")
    with pytest.raises(ValueError, match="different fixture or base date"):
        showcase.prepare_vault(vault, FIXTURE, "2026-11-01")
    changed = tmp_path / "changed.json"
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["persona"] = "Another persona"
    changed.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(ValueError, match="different fixture or base date"):
        showcase.prepare_vault(vault, changed, "2026-10-31")
