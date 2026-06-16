"""L4 growth report — a descriptive period-vs-period comparison (Phase 14).

# DEMO-STUB: replaced by the real L4 growth analytics
# ─────────────────────────────────────────────────────────────────────────────
# This is the seam ``GET /insights/growth`` reads. It compares two date windows
# over the data Eva already has — entry counts, average noted mood, and which
# themes appear in each — and renders the result as plain observation.
#
# The one hard rule (System Design §11, §12 risk row "Growth analytics read as a
# harmful verdict"): the report is DESCRIPTIVE, never a verdict. It tells you what
# you wrote, not whether you are doing well or badly. So:
#   * mood is reported as the average you logged, with a neutral "higher / lower /
#     about the same" framing — a description of the numbers, not praise or alarm;
#   * theme changes are listed as "appears more recently / earlier / in both",
#     never "you've grown" / "you've slipped";
#   * it closes with an open, reflective question — the user is the interpreter.
#
# Pure computation (no model): the model never narrates a judgment here. The real
# L4 will compute richer deltas, but it must keep this descriptive contract.
# ─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging

from . import db

log = logging.getLogger("eva.memory.growth")

# How many themes to surface per period (the most frequent ones).
_TOP_THEMES = 6
# A mood-average change smaller than this reads as "about the same", not a shift.
_MOOD_EPSILON = 0.5


def _period_rows(conn, date_from: str, date_to: str, include_seeded: bool):
    """Mood points (joined to their themes) within an inclusive day window."""
    return db.mood_series_range(
        conn, date_from=date_from, date_to=date_to, include_seeded=include_seeded
    )


def _themes_for(conn, date_from: str, date_to: str, include_seeded: bool) -> list[str]:
    """Flat list of every theme token across the window's done extractions."""
    clauses = ["x.extraction_status = 'done'", "e.date >= ?", "e.date <= ?"]
    params: list = [date_from, date_to]
    if not include_seeded:
        clauses.append("e.is_seeded = 0")
    rows = conn.execute(
        f"""
        SELECT x.themes AS themes
        FROM extractions x JOIN entries e ON e.id = x.entry_id
        WHERE {' AND '.join(clauses)}
        """,
        params,
    ).fetchall()
    themes: list[str] = []
    for r in rows:
        if not r["themes"]:
            continue
        try:
            value = json.loads(r["themes"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, list):
            themes.extend(str(t).strip().lower() for t in value if str(t).strip())
    return themes


def _period_summary(conn, date_from: str, date_to: str, include_seeded: bool) -> dict:
    """Descriptive stats for one window: counts, average mood, top themes."""
    rows = _period_rows(conn, date_from, date_to, include_seeded)
    moods = [r["mood"] for r in rows if r["mood"] is not None]
    avg = round(sum(moods) / len(moods), 1) if moods else None

    themes = _themes_for(conn, date_from, date_to, include_seeded)
    counts: dict[str, int] = {}
    for t in themes:
        counts[t] = counts.get(t, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_THEMES]

    return {
        "from": date_from,
        "to": date_to,
        "entry_count": len(rows),
        "avg_mood": avg,
        "top_themes": [{"theme": t, "count": c} for t, c in top],
    }


def _data_range(conn, include_seeded: bool) -> tuple[str, str] | None:
    """The (min, max) entry date over the selected dataset, or None if empty."""
    where = "" if include_seeded else "WHERE is_seeded = 0"
    row = conn.execute(
        f"SELECT MIN(date) AS lo, MAX(date) AS hi FROM mood_series {where}"
    ).fetchone()
    if not row or row["lo"] is None:
        return None
    return row["lo"], row["hi"]


def _midpoint(lo: str, hi: str) -> str:
    """The calendar midpoint date between two YYYY-MM-DD bounds (inclusive split)."""
    from datetime import date, timedelta

    d_lo = date.fromisoformat(lo)
    d_hi = date.fromisoformat(hi)
    mid = d_lo + (d_hi - d_lo) // 2
    return mid.isoformat()


def _mood_phrase(a_avg: float | None, b_avg: float | None) -> tuple[float | None, str]:
    """A neutral (change, description) for the mood averages — never a verdict.

    ``change`` is ``b_avg - a_avg`` (or None if either period has no mood). The
    description states the direction of the *number* ("higher / lower / about the
    same on average"), explicitly framed as a description of what was logged.
    """
    if a_avg is None or b_avg is None:
        return None, (
            "There isn't a clear mood average in both stretches to compare — "
            "some days were left without a noted mood."
        )
    change = round(b_avg - a_avg, 1)
    if abs(change) < _MOOD_EPSILON:
        return change, (
            f"The average mood you noted was about the same: {a_avg:+.1f} earlier "
            f"and {b_avg:+.1f} more recently."
        )
    direction = "higher" if change > 0 else "lower"
    return change, (
        f"The average mood you noted was {a_avg:+.1f} earlier and {b_avg:+.1f} more "
        f"recently — {abs(change):.1f} {direction} on average. This is a description "
        f"of what you wrote, not a judgment."
    )


def compare_periods(
    conn,
    *,
    a_from: str,
    a_to: str,
    b_from: str,
    b_to: str,
    include_seeded: bool = False,
) -> dict:
    """Build the descriptive growth report comparing window A (earlier) to B (later).

    Returns the §11 growth shape: the two period summaries, a neutral mood delta,
    the theme shifts (emerged / faded / continued), an observational narrative, and
    a reflective closing question. Everything is descriptive — there is no verdict.
    """
    pa = _period_summary(conn, a_from, a_to, include_seeded)
    pb = _period_summary(conn, b_from, b_to, include_seeded)

    a_themes = {t["theme"] for t in pa["top_themes"]}
    b_themes = {t["theme"] for t in pb["top_themes"]}
    emerged = sorted(b_themes - a_themes)
    faded = sorted(a_themes - b_themes)
    continued = sorted(a_themes & b_themes)

    change, mood_desc = _mood_phrase(pa["avg_mood"], pb["avg_mood"])

    narrative: list[str] = [
        f"You wrote {pa['entry_count']} "
        f"{'entry' if pa['entry_count'] == 1 else 'entries'} in the earlier stretch "
        f"({pa['from']} to {pa['to']}) and {pb['entry_count']} "
        f"{'entry' if pb['entry_count'] == 1 else 'entries'} in the more recent one "
        f"({pb['from']} to {pb['to']}).",
        mood_desc,
    ]
    if continued:
        narrative.append(f"Running through both stretches: {_join(continued)}.")
    if emerged:
        narrative.append(f"Appearing more recently, but not in the earlier stretch: {_join(emerged)}.")
    if faded:
        narrative.append(f"Present earlier, but not in the more recent stretch: {_join(faded)}.")
    if not (continued or emerged or faded):
        narrative.append("There aren't enough themes yet to compare across the two stretches.")

    return {
        "period_a": pa,
        "period_b": pb,
        "mood_delta": {"a_avg": pa["avg_mood"], "b_avg": pb["avg_mood"], "change": change, "description": mood_desc},
        "theme_shifts": {"emerged": emerged, "faded": faded, "continued": continued},
        "narrative": narrative,
        "closing_question": _closing_question(emerged, faded),
        # A flag the UI can lean on to keep the framing honest in the heading.
        "is_descriptive": True,
    }


def auto_compare(conn, *, include_seeded: bool = False) -> dict | None:
    """Compare the two halves of the available history, or None if there's none.

    Used when the caller doesn't pass explicit windows: the dataset's date range is
    split at its midpoint into an earlier and a more-recent half. Returns None when
    the vault has no mood data at all, so the endpoint can show an empty state.
    """
    rng = _data_range(conn, include_seeded)
    if rng is None:
        return None
    lo, hi = rng
    if lo == hi:
        return None  # a single day can't be split into two stretches to compare
    mid = _midpoint(lo, hi)
    from datetime import date, timedelta

    # Earlier half ends the day before the midpoint; recent half starts at it.
    before_mid = (date.fromisoformat(mid) - timedelta(days=1)).isoformat()
    return compare_periods(
        conn, a_from=lo, a_to=before_mid, b_from=mid, b_to=hi, include_seeded=include_seeded
    )


def _closing_question(emerged: list[str], faded: list[str]) -> str:
    """A reflective, open question — the user interprets; the report doesn't.

    Deterministic (no randomness so the report is stable), but tailored to what the
    comparison surfaced so it doesn't read as boilerplate.
    """
    if emerged:
        return (
            f"What do you make of {_join(emerged)} showing up more lately — "
            "is that where your attention has been?"
        )
    if faded:
        return (
            f"You wrote about {_join(faded)} earlier and less so recently — "
            "does that match how things feel to you?"
        )
    return "Looking at these two stretches side by side, what feels different to you?"


def _join(items: list[str]) -> str:
    """Join a short list into readable prose: ``a``, ``a and b``, ``a, b and c``."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])} and {items[-1]}"
