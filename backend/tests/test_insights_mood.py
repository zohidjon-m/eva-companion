"""Phase 12 — GET /insights/mood: the mood chart's data plumbing.

Pure SQL over mood_series (no model), so these tests insert rows directly and
assert the endpoint's three contracts:
  * live data only by default (is_seeded = 0); ?include_seeded=true lifts it;
  * NULL mood is preserved as null all the way out (the chart's gap, never zero);
  * the from/to bounds filter by day, and the summary is joined for the hover.
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


def _seed_point(
    conn,
    *,
    date: str,
    mood: int | None,
    summary: str | None = "a summary long enough to be real",
    is_seeded: bool = False,
    created_at: str | None = None,
) -> str:
    """Insert one entry + done extraction + mood point directly, return entry id."""
    entry_id = str(uuid.uuid4())
    db.insert_entry(
        conn, id=entry_id, date=date, type="journal", text="x",
        word_count=1, created_at=created_at or f"{date}T21:30:00", is_seeded=is_seeded,
    )
    db.create_pending_extraction(conn, entry_id)
    if summary is not None:
        db.finalize_extraction(
            conn, entry_id, mood=mood, emotions=[], entities=[], themes=[],
            events=[], stated_goals=[], behaviors=[], decisions=[],
            open_loops=[], self_judgments=[], summary=summary, extracted_at=f"{date}T21:30:01",
        )
    db.upsert_mood_series(
        conn, entry_id=entry_id, date=date, mood=mood, emotions=[], is_seeded=is_seeded,
    )
    return entry_id


def test_empty_vault_returns_no_points(client):
    resp = client.get("/insights/mood")
    assert resp.status_code == 200
    assert resp.json()["points"] == []


def test_live_only_by_default_seed_included_on_flag(client):
    conn = db.get_or_create_db()
    _seed_point(conn, date="2026-06-01", mood=2, is_seeded=False)
    _seed_point(conn, date="2026-05-20", mood=-1, is_seeded=True)
    conn.close()

    live = client.get("/insights/mood").json()["points"]
    assert [p["date"] for p in live] == ["2026-06-01"]
    assert all(p["is_seeded"] is False for p in live)

    both = client.get("/insights/mood", params={"include_seeded": "true"}).json()["points"]
    # Oldest first: the seeded May point precedes the live June point.
    assert [p["date"] for p in both] == ["2026-05-20", "2026-06-01"]
    assert any(p["is_seeded"] for p in both)


def test_null_mood_is_preserved_as_gap_not_zero(client):
    conn = db.get_or_create_db()
    _seed_point(conn, date="2026-06-02", mood=None)
    conn.close()

    point = client.get("/insights/mood").json()["points"][0]
    assert point["mood"] is None  # the UI draws this as a gap; it is never 0


def test_summary_joined_for_hover(client):
    conn = db.get_or_create_db()
    _seed_point(conn, date="2026-06-03", mood=1, summary="The day I finally went for that run by the river.")
    conn.close()

    point = client.get("/insights/mood").json()["points"][0]
    assert point["summary"] == "The day I finally went for that run by the river."


def test_date_bounds_filter_inclusive(client):
    conn = db.get_or_create_db()
    for d in ("2026-06-01", "2026-06-05", "2026-06-10"):
        _seed_point(conn, date=d, mood=0)
    conn.close()

    got = client.get("/insights/mood", params={"from": "2026-06-05", "to": "2026-06-10"}).json()
    assert [p["date"] for p in got["points"]] == ["2026-06-05", "2026-06-10"]
    assert got["from"] == "2026-06-05" and got["to"] == "2026-06-10"


def test_bad_date_is_rejected(client):
    assert client.get("/insights/mood", params={"from": "june"}).status_code == 400
    assert client.get("/insights/mood", params={"to": "2026/06/01"}).status_code == 400


def test_same_day_points_ordered_by_created_at(client):
    conn = db.get_or_create_db()
    _seed_point(conn, date="2026-06-04", mood=1, summary="evening", created_at="2026-06-04T21:00:00")
    _seed_point(conn, date="2026-06-04", mood=3, summary="morning", created_at="2026-06-04T08:00:00")
    conn.close()

    summaries = [p["summary"] for p in client.get("/insights/mood").json()["points"]]
    assert summaries == ["morning", "evening"]


def test_recomputed_entry_replaces_mood_point(client):
    conn = db.get_or_create_db()
    entry_id = _seed_point(conn, date="2026-06-07", mood=-1)
    db.upsert_mood_series(
        conn,
        entry_id=entry_id,
        date="2026-06-07",
        mood=4,
        emotions=[{"name": "calm", "intensity": 0.8}],
        is_seeded=False,
    )
    conn.close()

    points = client.get("/insights/mood").json()["points"]
    assert len(points) == 1
    assert points[0]["entry_id"] == entry_id
    assert points[0]["mood"] == 4
    assert points[0]["emotions"] == [{"name": "calm", "intensity": 0.8}]
