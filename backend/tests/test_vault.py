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
    # Each turn carries its stable UID in the timestamped V2 header.
    for r in (r1, r2, r3):
        assert f"· {r.type} · {r.id}" in text
    assert "## 09:00:00 · chat ·" in text
    assert "## 09:10:00 · journal ·" in text


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


def test_read_day_parses_v2_header_ids(vault):
    rec = vault.save_entry("body", "journal", when=datetime(2026, 6, 16, 9, 0, 0))

    turns = vault.read_day("2026-06-16")

    assert len(turns) == 1
    assert turns[0].id == rec.id
    assert turns[0].type == "journal"
    assert turns[0].text == "body"


def test_read_day_parses_legacy_comment_ids(vault):
    path = vault.day_file("2026-06-16")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ndate: 2026-06-16\nkind: eva-journal-day\ncreated_at: now\n---\n\n"
        "# 2026-06-16\n\n"
        "## 09:00:00 · journal\n"
        "<!-- id: legacy-id -->\n\n"
        "legacy body\n",
        encoding="utf-8",
    )

    turns = vault.read_day("2026-06-16")

    assert len(turns) == 1
    assert turns[0].id == "legacy-id"
    assert turns[0].text == "legacy body"


def test_update_entry_keeps_uid_and_changes_body(vault):
    rec = vault.save_entry("before", "journal", when=datetime(2026, 6, 16, 9, 0, 0))

    updated = vault.update_entry(rec.id, "after")

    assert updated is not None
    assert updated.id == rec.id
    turns = vault.read_day("2026-06-16")
    assert turns[0].id == rec.id
    assert turns[0].text == "after"


def test_source_hash_ignores_surrounding_whitespace(vault):
    assert vault.source_hash(" body\n") == vault.source_hash("body")
    assert vault.source_hash("body") != vault.source_hash("changed")


def test_backfill_promotes_legacy_comment_id(vault):
    path = vault.day_file("2026-06-16")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# 2026-06-16\n\n"
        "## 09:00:00 · journal\n"
        "<!-- id: legacy-id -->\n\n"
        "legacy body\n",
        encoding="utf-8",
    )

    report = vault.backfill_entry_uids()
    second = vault.backfill_entry_uids()

    text = path.read_text(encoding="utf-8")
    assert report.files_changed == 1
    assert report.entries_changed == 1
    assert report.errors == ()
    assert second.files_changed == 0
    assert "## 09:00:00 · journal · legacy-id" in text
    assert "<!-- id:" not in text
    assert vault.read_day("2026-06-16")[0].id == "legacy-id"


def test_backfill_generates_uid_for_missing_id(vault):
    path = vault.day_file("2026-06-16")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 2026-06-16\n\n## 09:00:00 · journal\n\nbody\n", encoding="utf-8")

    report = vault.backfill_entry_uids()
    turn = vault.read_day("2026-06-16")[0]

    assert report.entries_changed == 1
    assert turn.id is not None
    assert len(turn.id) == 36


def test_backfill_conflicting_ids_fail_without_rewrite(vault):
    path = vault.day_file("2026-06-16")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = (
        "# 2026-06-16\n\n"
        "## 09:00:00 · journal · header-id\n"
        "<!-- id: comment-id -->\n\n"
        "body\n"
    )
    path.write_text(original, encoding="utf-8")

    report = vault.backfill_entry_uids()

    assert report.files_changed == 0
    assert len(report.errors) == 1
    assert path.read_text(encoding="utf-8") == original
