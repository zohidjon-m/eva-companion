"""R8 — the consolidation scheduler: defer, complete-days-only, and catch-up.

The scheduler owns *when*, not *what*, so these tests inject the clock, the
chat-active signal, and stub the cadences — asserting the guarantees the scheduler
owns (EVA_SYSTEM_DESIGN §8): a job never starts while a chat turn is active; only
**complete** days/weeks (through yesterday) are processed, so a morning run can't
strand the day's later entries; a period already done is not re-run; and windows
missed while the app was closed are caught up.
"""

from __future__ import annotations

import asyncio

import pytest

import scheduler as sched


@pytest.fixture()
def vault_env(tmp_path, monkeypatch):
    """A temp vault so the scheduler's state file is isolated per test."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    return tmp_path


@pytest.fixture()
def stub_cadences(monkeypatch):
    """Replace the real cadences with counters; return the call log."""
    log = {"nightly": [], "weekly": []}

    async def fake_nightly(day, *, call_model=None):
        log["nightly"].append(day)

    async def fake_weekly(week_end, *, call_model=None):
        log["weekly"].append(week_end)

    monkeypatch.setattr(sched.consolidate, "run_nightly", fake_nightly)
    monkeypatch.setattr(sched.consolidate, "run_weekly", fake_weekly)
    return log


def test_tick_defers_every_cadence_while_chat_is_active(vault_env, stub_cadences):
    active = {"chatting": True}
    s = sched.ConsolidationScheduler(
        now=lambda: "2026-07-06", is_chat_active=lambda: active["chatting"]
    )

    # Chat active → the tick defers and nothing runs.
    assert asyncio.run(s.tick()) is False
    assert stub_cadences["nightly"] == [] and stub_cadences["weekly"] == []

    # Chat idle → the due cadences fire for the last COMPLETE day (yesterday).
    active["chatting"] = False
    assert asyncio.run(s.tick()) is True
    assert stub_cadences["nightly"] == ["2026-07-05"]
    assert stub_cadences["weekly"] == ["2026-07-05"]


def test_today_is_never_processed_prematurely(vault_env, stub_cadences):
    # Running in the morning must not mark today done — today is left for tomorrow.
    s = sched.ConsolidationScheduler(now=lambda: "2026-07-06", is_chat_active=lambda: False)
    asyncio.run(s.tick())
    assert "2026-07-06" not in stub_cadences["nightly"]     # today untouched
    assert sched.load_state()["last_nightly"] == "2026-07-05"


def test_tick_does_not_double_fire_within_the_same_period(vault_env, stub_cadences):
    s = sched.ConsolidationScheduler(now=lambda: "2026-07-06", is_chat_active=lambda: False)

    asyncio.run(s.tick())        # first run: yesterday's nightly + weekly
    asyncio.run(s.tick())        # same day again: nothing new is due

    assert stub_cadences["nightly"] == ["2026-07-05"]
    assert stub_cadences["weekly"] == ["2026-07-05"]


def test_nightly_fires_each_new_day_but_weekly_waits_a_period(vault_env, stub_cadences):
    day = {"today": "2026-07-06"}
    s = sched.ConsolidationScheduler(now=lambda: day["today"], is_chat_active=lambda: False)

    asyncio.run(s.tick())        # 07-06: nightly 07-05, weekly 07-05
    day["today"] = "2026-07-07"
    asyncio.run(s.tick())        # 07-07: nightly 07-06; weekly not yet due

    assert stub_cadences["nightly"] == ["2026-07-05", "2026-07-06"]
    assert stub_cadences["weekly"] == ["2026-07-05"]


def test_weekly_becomes_due_after_the_period_elapses(vault_env, stub_cadences):
    day = {"today": "2026-07-06"}
    s = sched.ConsolidationScheduler(now=lambda: day["today"], is_chat_active=lambda: False)

    asyncio.run(s.tick())        # weekly 07-05
    day["today"] = "2026-07-13"  # a full period later → through = 07-12
    asyncio.run(s.tick())

    assert stub_cadences["weekly"] == ["2026-07-05", "2026-07-12"]


def test_missed_days_are_caught_up_in_order(vault_env, stub_cadences):
    # The app was last active on 07-01; it reopens on 07-06.
    sched.save_state({"last_nightly": "2026-07-01", "last_weekly": "2026-07-01"})
    s = sched.ConsolidationScheduler(now=lambda: "2026-07-06", is_chat_active=lambda: False)

    asyncio.run(s.tick())
    # Every complete day since the marker (02..05) is caught up, in order.
    assert stub_cadences["nightly"] == ["2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"]


def test_long_backlog_is_paced_oldest_first_not_abandoned(vault_env, stub_cadences):
    # Closed far longer than the per-tick cap: the OLDEST windows run first and the
    # marker advances incrementally, so nothing is silently skipped.
    from datetime import date, timedelta

    start = "2026-01-01"
    today = "2026-06-01"                      # ~150 days later, well over the 31-day cap
    sched.save_state({"last_nightly": start})
    s = sched.ConsolidationScheduler(now=lambda: today, is_chat_active=lambda: False)

    asyncio.run(s.tick())
    batch1 = list(stub_cadences["nightly"])                     # snapshot, not a live ref
    assert len(batch1) == sched.MAX_BACKFILL_DAYS               # paced, not a burst
    assert batch1[0] == "2026-01-02"                            # oldest first
    assert sched.load_state()["last_nightly"] == batch1[-1]

    asyncio.run(s.tick())                                       # next tick resumes
    batch2 = stub_cadences["nightly"][len(batch1):]
    assert batch2[0] == (date.fromisoformat(batch1[-1]) + timedelta(days=1)).isoformat()


def test_chat_active_defaults_to_the_client_signal(vault_env, monkeypatch):
    """When no signal is injected, the scheduler reads llm.client.chat_active."""
    from llm import client

    # Patch before constructing: the scheduler binds the signal at init time.
    monkeypatch.setattr(client, "chat_active", lambda: True)
    s = sched.ConsolidationScheduler(now=lambda: "2026-07-06")
    # Chat "active" via the real signal → tick defers (returns False, runs nothing).
    assert asyncio.run(s.tick()) is False
