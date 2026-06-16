"""Phase 14 — GET /insights/growth: a descriptive period comparison, never a verdict.

The report must (a) compute real deltas (entry counts, average mood, theme shifts)
over two windows, (b) auto-split the history when no windows are passed, (c) behave
on an empty vault, and — the hard rule (§12) — (d) read DESCRIPTIVELY: no praise,
no alarm, no judgment words anywhere in the prose it generates.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import app
from memory import db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    return TestClient(app)


def _entry(conn, *, date, mood, themes):
    entry_id = str(uuid.uuid4())
    db.insert_entry(
        conn, id=entry_id, date=date, type="journal", text="x", word_count=1,
        created_at=f"{date}T21:30:00", is_seeded=False,
    )
    db.create_pending_extraction(conn, entry_id)
    db.finalize_extraction(
        conn, entry_id, mood=mood, emotions=[], entities=[], themes=themes,
        events=[], stated_goals=[], behaviors=[], decisions=[], open_loops=[],
        self_judgments=[], summary="a reflection", extracted_at=f"{date}T21:30:01",
    )
    db.upsert_mood_series(conn, entry_id=entry_id, date=date, mood=mood, emotions=[], is_seeded=False)
    return entry_id


# Verdict / judgment vocabulary that must never appear in the generated prose.
_BANNED = [
    "better", "worse", "improv", "declin", "you should", "should try", "well done",
    "good job", "great job", "proud", "setback", "on track", "succeed", "success",
    "failing", "failure", "healthier", "unhealthy", "doing great", "keep it up",
]


def _all_prose(report: dict) -> str:
    parts = list(report["narrative"]) + [report["closing_question"], report["mood_delta"]["description"]]
    return " ".join(parts).lower()


def test_empty_vault_is_graceful(client):
    body = client.get("/insights/growth").json()
    assert body["empty"] is True


def test_explicit_periods_compute_deltas(client):
    conn = db.get_or_create_db()
    # Earlier window: low mood, "work"/"stress". Recent window: higher, "running".
    _entry(conn, date="2026-06-01", mood=-2, themes=["work", "stress"])
    _entry(conn, date="2026-06-02", mood=-3, themes=["work", "conflict"])
    _entry(conn, date="2026-06-10", mood=2, themes=["running", "calm"])
    _entry(conn, date="2026-06-11", mood=3, themes=["running", "faith"])
    conn.close()

    body = client.get("/insights/growth", params={
        "a_from": "2026-06-01", "a_to": "2026-06-05",
        "b_from": "2026-06-08", "b_to": "2026-06-12",
        "include_seeded": "false",
    }).json()

    assert body["empty"] is False
    assert body["period_a"]["entry_count"] == 2 and body["period_b"]["entry_count"] == 2
    assert body["period_a"]["avg_mood"] == -2.5 and body["period_b"]["avg_mood"] == 2.5
    assert body["mood_delta"]["change"] == 5.0
    assert "running" in body["theme_shifts"]["emerged"]
    assert "conflict" in body["theme_shifts"]["faded"] or "work" in body["theme_shifts"]["faded"]


def test_report_reads_descriptively_never_a_verdict(client):
    conn = db.get_or_create_db()
    _entry(conn, date="2026-06-01", mood=-4, themes=["work"])
    _entry(conn, date="2026-06-02", mood=-3, themes=["work"])
    _entry(conn, date="2026-06-10", mood=4, themes=["running"])
    _entry(conn, date="2026-06-11", mood=3, themes=["running"])
    conn.close()

    body = client.get("/insights/growth", params={
        "a_from": "2026-06-01", "a_to": "2026-06-05",
        "b_from": "2026-06-08", "b_to": "2026-06-12",
    }).json()
    prose = _all_prose(body)
    for word in _BANNED:
        assert word not in prose, f"growth report used a verdict word: {word!r}"
    assert body["is_descriptive"] is True
    # The closing line invites the user's own interpretation (it's a question).
    assert body["closing_question"].rstrip().endswith("?")


def test_auto_split_when_no_windows(client):
    conn = db.get_or_create_db()
    for d, m in [("2026-06-01", -2), ("2026-06-03", -1), ("2026-06-09", 2), ("2026-06-11", 3)]:
        _entry(conn, date=d, mood=m, themes=["work"])
    conn.close()

    body = client.get("/insights/growth").json()
    assert body["empty"] is False
    # The two halves together cover all four entries.
    assert body["period_a"]["entry_count"] + body["period_b"]["entry_count"] == 4


def test_auto_split_single_day_is_empty(client):
    """Entries all on one day can't be split into two stretches → empty state."""
    conn = db.get_or_create_db()
    _entry(conn, date="2026-06-16", mood=1, themes=["work"])
    _entry(conn, date="2026-06-16", mood=2, themes=["calm"])
    conn.close()
    assert client.get("/insights/growth").json()["empty"] is True


def test_partial_window_args_rejected(client):
    assert client.get("/insights/growth", params={"a_from": "2026-06-01"}).status_code == 400


def test_bad_date_rejected(client):
    assert client.get("/insights/growth", params={
        "a_from": "june", "a_to": "2026-06-05", "b_from": "2026-06-08", "b_to": "2026-06-12",
    }).status_code == 400
