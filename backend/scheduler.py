"""Consolidation scheduler — fire the R8 write loop when the app is idle.

EVA_SYSTEM_DESIGN §8: the heavy work (building L3/L4) happens where latency does
not matter, and **never contends with a real-time chat turn**. This scheduler is
the seam that enforces *when*: an asyncio task that wakes on a poll interval and,
if no chat turn is active and a cadence is due, runs :func:`memory.consolidate`.

It is deliberately asyncio-native rather than a third-party scheduler: the backend
already supervises background work this exact way (``LlamaServer.supervise`` is an
``asyncio.Task`` started from the app lifespan), and the model-access serialization
we care about already lives in :mod:`llm.client` (the priority lock). So this file
only needs to answer two questions each tick — *is it due?* and *is chat idle?* —
and then call the cadence. Two guarantees:

* **Defer while chatting.** Before starting a job the scheduler checks
  :func:`llm.client.chat_active`; if a turn is pending/in-flight it skips the tick
  and retries next poll. This is belt-and-suspenders over the priority lock (which
  already makes any background model call yield to chat) — it avoids even *starting*
  a multi-step job mid-conversation.
* **No double-fire.** ``last_nightly`` / ``last_weekly`` markers in a small state
  file mean a restart (or a fast poll) never re-runs a cadence already done for the
  period, and never skips one that is due.

One job runs at a time (the tick is sequential and awaited), so two consolidations
never overlap. A manual trigger for tests lives at ``POST /consolidate`` in the app.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from memory import consolidate, vault_dir

log = logging.getLogger("eva.scheduler")

STATE_FILENAME = "consolidation_state.json"

# How often to wake and check whether a cadence is due. Cheap: a check is a clock
# read plus a chat-active flag, so a short interval costs almost nothing and keeps
# the "run when idle after midnight" behaviour responsive.
DEFAULT_POLL_SECONDS = 300.0
# Weekly cadence tiles the calendar into 7-day windows.
WEEKLY_PERIOD_DAYS = 7
# Bound how many windows one tick processes, so reopening the app after a long
# absence doesn't fire hundreds of jobs in a single burst. This paces catch-up — it
# does NOT drop windows: the oldest due windows run first and the marker advances
# incrementally, so the next tick resumes exactly where this one stopped until the
# backlog is fully cleared.
MAX_BACKFILL_DAYS = 31
MAX_BACKFILL_WEEKS = 8


def _state_path() -> Path:
    return vault_dir() / STATE_FILENAME


def load_state() -> dict:
    """Read the scheduler's ``{last_nightly, last_weekly}`` markers (empty if none)."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("scheduler: could not read %s (%s); starting fresh", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict) -> None:
    """Persist the scheduler markers, creating the vault dir if needed."""
    vdir = vault_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _prev_day(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def due_nightly_days(last_nightly: str | None, through: str) -> list[str]:
    """The complete days needing a nightly, in order, ending at ``through`` (yesterday).

    Only *complete* days are returned — never ``today`` — so a nightly run started in
    the morning can't mark the day done and strand its later entries; today is picked
    up tomorrow. On the first run (``last_nightly`` is ``None``) only the single most
    recent complete day runs; otherwise every missed day since the marker is caught
    up, **oldest first**, at most :data:`MAX_BACKFILL_DAYS` per tick so a long backlog
    is paced across ticks rather than abandoned.
    """
    try:
        end = date.fromisoformat(through)
    except ValueError:
        return []
    if not last_nightly:
        return [through]
    try:
        start = date.fromisoformat(last_nightly) + timedelta(days=1)
    except ValueError:
        return [through]
    if start > end:
        return []
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    return days[:MAX_BACKFILL_DAYS]


def due_weekly_ends(last_weekly: str | None, through: str) -> list[str]:
    """The 7-day window ends needing a weekly, in order, up to ``through`` (yesterday).

    Windows tile the calendar in :data:`WEEKLY_PERIOD_DAYS` steps from the last one
    run, so missed weeks are caught up **oldest first** rather than collapsed into one
    trailing window, at most :data:`MAX_BACKFILL_WEEKS` per tick (the rest resume next
    tick). First run does the single window ending at ``through``.
    """
    try:
        end = date.fromisoformat(through)
    except ValueError:
        return []
    if not last_weekly:
        return [through]
    try:
        cur = date.fromisoformat(last_weekly)
    except ValueError:
        return [through]
    ends: list[str] = []
    while (end - cur).days >= WEEKLY_PERIOD_DAYS:
        cur = cur + timedelta(days=WEEKLY_PERIOD_DAYS)
        ends.append(cur.isoformat())
    return ends[:MAX_BACKFILL_WEEKS]


class ConsolidationScheduler:
    """Owns the idle-time consolidation loop (started/stopped from the app lifespan).

    Dependencies are injected so the tick is unit-testable without wall-clock time,
    a real model, or a live chat: ``now`` supplies today's ISO date, ``is_chat_active``
    is the defer signal, and ``call_model`` is threaded into the cadences.
    """

    def __init__(
        self,
        *,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        now: Callable[[], str] | None = None,
        is_chat_active: Callable[[], bool] | None = None,
        call_model=None,
    ) -> None:
        self._poll = poll_seconds
        self._now = now or (lambda: date.today().isoformat())
        self._call_model = call_model
        self._task: asyncio.Task | None = None
        self._stopping = False
        if is_chat_active is None:
            from llm import client
            is_chat_active = client.chat_active
        self._is_chat_active = is_chat_active

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        """Launch the poll loop as a background task (no-op if already running)."""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run())
        log.info("consolidation scheduler started (poll every %.0fs)", self._poll)

    async def stop(self) -> None:
        """Stop the loop and wait for the current tick to finish."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad tick must not kill the loop
                log.exception("consolidation tick failed; will retry next poll")
            try:
                await asyncio.sleep(self._poll)
            except asyncio.CancelledError:
                raise

    # -- the decision --------------------------------------------------------
    async def tick(self) -> bool:
        """Run any due cadences if chat is idle, catching up missed windows.

        Deferring while a chat turn is active is the whole point: the check happens
        *before* any job starts, and a deferred tick simply retries on the next poll.
        Only **complete** days/weeks (through yesterday) are processed — today is left
        for tomorrow so a morning run can't strand the day's later entries — and every
        window missed since the last marker is caught up in order. A marker is written
        only after its window completes, so a crash mid-job re-runs that window next
        time rather than skipping it. Returns True if any job ran.
        """
        if self._is_chat_active():
            log.debug("scheduler: chat active; deferring consolidation")
            return False

        through = _prev_day(self._now())   # yesterday: the last complete day
        state = load_state()
        ran = False

        for day in due_nightly_days(state.get("last_nightly"), through):
            log.info("scheduler: running nightly consolidation for %s", day)
            await consolidate.run_nightly(day, call_model=self._call_model)
            state["last_nightly"] = day
            save_state(state)
            ran = True

        for week_end in due_weekly_ends(state.get("last_weekly"), through):
            log.info("scheduler: running weekly consolidation ending %s", week_end)
            await consolidate.run_weekly(week_end, call_model=self._call_model)
            state["last_weekly"] = week_end
            save_state(state)
            ran = True

        return ran
