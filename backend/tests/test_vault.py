"""Phase 2 Step A — verify vault.py writes append-only, readable Markdown.

The vault is the source of truth, so these tests pin the storage contract: one
file per day, frontmatter written exactly once, turns appended in order with
timestamps, and the returned :class:`EntryRecord` matching what got written.
"""

from __future__ import annotations

import importlib
from datetime import datetime

import pytest
import yaml


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    import memory
    import memory.vault as vault_mod

    importlib.reload(memory)
    importlib.reload(vault_mod)
    return vault_mod


def test_save_creates_day_file_with_frontmatter(vault):
    when = datetime(2026, 6, 16, 9, 14, 3)
    rec = vault.save_entry("Felt good today.", "journal", when=when)

    path = vault.day_file("2026-06-16")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")

    fm = yaml.safe_load(text.split("---\n")[1])
    assert fm["date"] == "2026-06-16"
    assert fm["kind"] == "eva-journal-day"

    # Returned record matches what we asked to save.
    assert rec.date == "2026-06-16"
    assert rec.type == "journal"
    assert rec.text == "Felt good today."
    assert rec.word_count == 3
    assert rec.created_at == "2026-06-16T09:14:03"


def test_multiple_turns_same_day_append_only(vault):
    when = datetime(2026, 6, 16, 9, 0, 0)
    r1 = vault.save_entry("first message", "chat", when=when)
    r2 = vault.save_entry("second message", "chat", when=when.replace(minute=5))
    r3 = vault.save_entry("a journal entry", "journal", when=when.replace(minute=10))

    text = vault.day_file("2026-06-16").read_text(encoding="utf-8")
    # Exactly one frontmatter / one day heading.
    assert text.count("---\n") == 2  # opening and closing fence of the single block
    assert text.count("# 2026-06-16\n") == 1
    # All three turns present, in order.
    assert text.index("first message") < text.index("second message") < text.index("a journal entry")
    # Each turn carries its own id and timestamped header.
    for r in (r1, r2, r3):
        assert f"<!-- id: {r.id} -->" in text
    assert "## 09:00:00 · chat" in text
    assert "## 09:10:00 · journal" in text


def test_distinct_days_distinct_files(vault):
    vault.save_entry("monday", "journal", when=datetime(2026, 6, 15, 8, 0, 0))
    vault.save_entry("tuesday", "journal", when=datetime(2026, 6, 16, 8, 0, 0))
    assert vault.day_file("2026-06-15").exists()
    assert vault.day_file("2026-06-16").exists()


def test_rejects_bad_type(vault):
    with pytest.raises(ValueError):
        vault.save_entry("hi", "note")


def test_rejects_empty_text(vault):
    with pytest.raises(ValueError):
        vault.save_entry("   ", "chat")


def test_unique_ids(vault):
    ids = {vault.save_entry(f"msg {i}", "chat").id for i in range(5)}
    assert len(ids) == 5
