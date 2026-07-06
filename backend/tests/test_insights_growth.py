"""R10 - GET /insights/growth: descriptive computed analytics."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import app
from memory import db, growth


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    return TestClient(app)


def _entry(
    conn,
    *,
    date,
    mood,
    themes=None,
    stated_goals=None,
    behaviors=None,
    open_loops=None,
    summary="a reflection",
):
    """Insert an entry plus done extraction and mood point for growth tests."""
    entry_id = str(uuid.uuid4())
    db.insert_entry(
        conn,
        id=entry_id,
        date=date,
        type="journal",
        text=summary,
        word_count=len(summary.split()),
        created_at=f"{date}T21:30:00",
        is_seeded=False,
    )
    db.create_pending_extraction(conn, entry_id)
    db.finalize_extraction(
        conn,
        entry_id,
        mood=mood,
        emotions=[],
        entities=[],
        themes=themes or [],
        events=[],
        stated_goals=stated_goals or [],
        behaviors=behaviors or [],
        decisions=[],
        open_loops=open_loops or [],
        self_judgments=[],
        summary=summary,
        extracted_at=f"{date}T21:30:01",
    )
    db.upsert_mood_series(
        conn, entry_id=entry_id, date=date, mood=mood, emotions=[], is_seeded=False
    )
    return entry_id


_BANNED = [
    "better", "worse", "improv", "declin", "you should", "should try",
    "well done", "good job", "great job", "proud", "setback", "on track",
    "succeed", "success", "failing", "failure", "healthier", "unhealthy",
    "doing great", "keep it up",
]


def _all_prose(report: dict) -> str:
    parts = list(report["narrative"]) + [
        report["closing_question"],
        report["mood_delta"]["description"],
    ]
    return " ".join(parts).lower()


def test_empty_vault_is_graceful(client):
    body = client.get("/insights/growth").json()
    assert body["empty"] is True


def test_explicit_periods_compute_mood_theme_loop_and_behavior_deltas(client):
    conn = db.get_or_create_db()
    _entry(
        conn,
        date="2026-06-01",
        mood=-2,
        themes=["work", "stress"],
        stated_goals=[{"text": "exercise regularly", "is_new": True}],
        behaviors=["skipped exercise"],
        open_loops=[{"description": "book dentist", "status": "open"}],
        summary="Said exercise matters, skipped exercise, and still needed to book dentist.",
    )
    _entry(
        conn,
        date="2026-06-02",
        mood=-3,
        themes=["work", "conflict"],
        behaviors=["skipped exercise"],
        summary="Skipped exercise again.",
    )
    _entry(
        conn,
        date="2026-06-10",
        mood=2,
        themes=["running", "calm"],
        stated_goals=[{"text": "exercise regularly", "is_new": False}],
        behaviors=["exercise session"],
        open_loops=[{"description": "book dentist", "status": "resolved"}],
        summary="Exercise session done, dentist booked.",
    )
    _entry(
        conn,
        date="2026-06-11",
        mood=3,
        themes=["running", "faith"],
        behaviors=["skipped exercise"],
        summary="Skipped exercise once, wrote about faith.",
    )
    conn.close()

    body = client.get("/insights/growth", params={
        "a_from": "2026-06-01", "a_to": "2026-06-05",
        "b_from": "2026-06-08", "b_to": "2026-06-12",
    }).json()

    assert body["empty"] is False
    assert body["period_a"]["entry_count"] == 2
    assert body["period_b"]["entry_count"] == 2
    assert body["period_a"]["avg_mood"] == -2.5
    assert body["period_b"]["avg_mood"] == 2.5
    assert body["mood_delta"]["change"] == 5.0
    assert "running" in body["theme_shifts"]["emerged"]

    assert body["period_a"]["open_loops"]["open"]["count"] == 1
    assert body["period_a"]["open_loops"]["resolution_rate"] == 0.0
    assert body["period_b"]["open_loops"]["resolved"]["count"] == 1
    assert body["period_b"]["open_loops"]["resolution_rate"] == 1.0
    assert body["open_loop_delta"]["resolution_rate_change"] == 1.0

    assert body["period_a"]["behaviors"]["contradicting"]["count"] == 2
    assert body["period_b"]["behaviors"]["aligned"]["count"] == 1
    assert body["period_b"]["behaviors"]["contradicting"]["count"] == 1
    assert body["behavior_delta"]["change"]["aligned"] == 1
    assert body["behavior_delta"]["change"]["contradicting"] == -1


def test_high_impact_growth_claims_are_dropped_without_verification(client):
    conn = db.get_or_create_db()
    _entry(
        conn,
        date="2026-06-01",
        mood=0,
        themes=["exercise"],
        stated_goals=[{"text": "exercise regularly", "is_new": True}],
    )
    _entry(
        conn,
        date="2026-06-10",
        mood=0,
        themes=["exercise"],
        stated_goals=[{"text": "exercise regularly", "is_new": False}],
        behaviors=["skipped exercise"],
        open_loops=[{"description": "book dentist", "status": "resolved"}],
    )
    report = growth.compare_periods(
        conn,
        a_from="2026-06-01",
        a_to="2026-06-05",
        b_from="2026-06-08",
        b_to="2026-06-12",
    )
    verified = growth.compare_periods(
        conn,
        a_from="2026-06-01",
        a_to="2026-06-05",
        b_from="2026-06-08",
        b_to="2026-06-12",
        verifier=lambda _claim, _evidence: True,
    )
    conn.close()

    assert report["verified_claims"] == []
    assert verified["verified_claims"]


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
    assert body["closing_question"].rstrip().endswith("?")


def test_auto_split_when_no_windows(client):
    conn = db.get_or_create_db()
    for d, m in [("2026-06-01", -2), ("2026-06-03", -1), ("2026-06-09", 2), ("2026-06-11", 3)]:
        _entry(conn, date=d, mood=m, themes=["work"])
    conn.close()

    body = client.get("/insights/growth").json()
    assert body["empty"] is False
    assert body["period_a"]["entry_count"] + body["period_b"]["entry_count"] == 4


def test_auto_split_single_day_is_empty(client):
    conn = db.get_or_create_db()
    _entry(conn, date="2026-06-16", mood=1, themes=["work"])
    _entry(conn, date="2026-06-16", mood=2, themes=["calm"])
    conn.close()
    assert client.get("/insights/growth").json()["empty"] is True


def test_partial_window_args_rejected(client):
    assert client.get("/insights/growth", params={"a_from": "2026-06-01"}).status_code == 400


def test_bad_date_rejected(client):
    assert client.get("/insights/growth", params={
        "a_from": "june", "a_to": "2026-06-05",
        "b_from": "2026-06-08", "b_to": "2026-06-12",
    }).status_code == 400


def _high_impact_seed(conn):
    """Seed one earlier + one recent entry that yield a high-impact candidate."""
    _entry(
        conn,
        date="2026-06-01",
        mood=0,
        themes=["exercise"],
        stated_goals=[{"text": "exercise regularly", "is_new": True}],
    )
    _entry(
        conn,
        date="2026-06-10",
        mood=0,
        themes=["exercise"],
        stated_goals=[{"text": "exercise regularly", "is_new": False}],
        behaviors=["skipped exercise"],
        open_loops=[{"description": "book dentist", "status": "resolved"}],
    )


def test_compare_periods_verified_surfaces_model_supported_claims(client):
    import asyncio

    conn = db.get_or_create_db()
    _high_impact_seed(conn)

    async def yes_model(_prompt, *, temperature, max_tokens):
        return "yes"

    async def no_model(_prompt, *, temperature, max_tokens):
        return "no"

    kept = asyncio.run(growth.compare_periods_verified(
        conn, a_from="2026-06-01", a_to="2026-06-05",
        b_from="2026-06-08", b_to="2026-06-12", call_model=yes_model,
    ))
    dropped = asyncio.run(growth.compare_periods_verified(
        conn, a_from="2026-06-01", a_to="2026-06-05",
        b_from="2026-06-08", b_to="2026-06-12", call_model=no_model,
    ))
    unconfigured = asyncio.run(growth.compare_periods_verified(
        conn, a_from="2026-06-01", a_to="2026-06-05",
        b_from="2026-06-08", b_to="2026-06-12", call_model=None,
    ))
    conn.close()

    assert kept["verified_claims"]           # model confirmed → surfaced
    assert dropped["verified_claims"] == []  # model rejected → dropped
    assert unconfigured["verified_claims"] == []  # no provider → fail closed


def test_growth_endpoint_verifies_claims_with_selected_provider(client, monkeypatch):
    from llm import client as llm_client
    from memory import operations

    conn = db.get_or_create_db()
    _high_impact_seed(conn)
    conn.close()

    async def fake_call(_prompt, *, temperature, max_tokens):
        return "yes"

    monkeypatch.setattr(llm_client, "provider_configured", lambda: True)
    monkeypatch.setattr(operations, "_llama_server_call", fake_call)

    body = client.get("/insights/growth", params={
        "a_from": "2026-06-01", "a_to": "2026-06-05",
        "b_from": "2026-06-08", "b_to": "2026-06-12",
    }).json()
    assert body["verified_claims"], "a configured provider that confirms should surface claims"


def test_growth_endpoint_drops_claims_when_no_provider(client, monkeypatch):
    from llm import client as llm_client

    conn = db.get_or_create_db()
    _high_impact_seed(conn)
    conn.close()

    monkeypatch.setattr(llm_client, "provider_configured", lambda: False)

    body = client.get("/insights/growth", params={
        "a_from": "2026-06-01", "a_to": "2026-06-05",
        "b_from": "2026-06-08", "b_to": "2026-06-12",
    }).json()
    assert body["verified_claims"] == []
