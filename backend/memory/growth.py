"""L4 growth analytics computed from real extraction fields.

R10 keeps growth reporting descriptive. Code computes period deltas for mood,
themes, open loops, and behavior-vs-goal counts; high-impact statements are only
returned when an injected verifier confirms the cited evidence supports them.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Callable

from . import db, verification

log = logging.getLogger("eva.memory.growth")

_TOP_THEMES = 6
_MOOD_EPSILON = 0.5
_MATCH_RATIO = 0.5
_WORD_RE = re.compile(r"[a-z0-9']+")
_STOPWORDS = frozenset(
    """
    a an and are as at be but by did do does doing for from had has have i if in
    into is it its me my of on or our so that the their them then they this to up
    was we were what when which who why will with you your
    """.split()
)
_CONTRADICTING_TERMS = frozenset(
    """
    avoid avoided avoiding broke failed ignored missed quit skipped skipping stopped
    procrastinated delayed cancelled canceled
    """.split()
)
_CONTRADICTING_PHRASES = ("did not", "didn't", "put off", "fell behind")

SyncVerifier = Callable[[str, list[str]], bool | None]


def _json_list(raw: str | None) -> list:
    """Decode a JSON-list TEXT column, tolerating NULL and malformed values."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _tokens(text: str) -> set[str]:
    """Content-word token set used for deterministic behavior-goal matching."""
    return {t for t in _WORD_RE.findall(str(text).lower()) if t not in _STOPWORDS}


def _overlap(a: set[str], b: set[str]) -> float:
    """Return overlap coefficient for short journal phrases."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _texts(value: list, *keys: str) -> list[str]:
    """Flatten mixed string/object L1 arrays into clean text values."""
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = next((str(item.get(k) or "").strip() for k in keys if item.get(k)), "")
        else:
            text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _entry(row) -> dict:
    """Parse one L1 row into the fields growth analytics count over."""
    loops = []
    for item in _json_list(row["open_loops"]):
        if isinstance(item, dict):
            description = str(item.get("description") or "").strip()
            status = str(item.get("status") or "open").strip().lower()
        else:
            description = str(item or "").strip()
            status = "open"
        if description:
            loops.append({
                "description": description,
                "status": status if status in {"open", "updated", "resolved"} else "open",
            })
    return {
        "entry_id": row["entry_id"],
        "date": row["date"],
        "mood": row["mood"],
        "summary": row["summary"] or row["text"] or "",
        "themes": _texts(_json_list(row["themes"])),
        "goals": _texts(_json_list(row["stated_goals"]), "text"),
        "behaviors": _texts(_json_list(row["behaviors"]), "text"),
        "open_loops": loops,
    }


def _entries_for_period(conn, date_from: str, date_to: str, include_seeded: bool) -> list[dict]:
    """Load parsed done extractions for one inclusive period."""
    rows = db.entries_for_consolidation(
        conn,
        date_from=date_from,
        date_to=date_to,
        include_seeded=include_seeded,
    )
    return [_entry(row) for row in rows]


def _theme_counts(entries: list[dict]) -> list[dict]:
    """Count themes across a period and return the top theme rows."""
    counts: dict[str, int] = {}
    for entry in entries:
        for theme in entry["themes"]:
            key = theme.strip().lower()
            if key:
                counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_THEMES]
    return [{"theme": theme, "count": count} for theme, count in top]


def _open_loop_counts(entries: list[dict]) -> dict:
    """Count open-loop statuses and carry evidence entry IDs for each bucket."""
    counts = {
        "open": {"count": 0, "entries": []},
        "updated": {"count": 0, "entries": []},
        "resolved": {"count": 0, "entries": []},
    }
    for entry in entries:
        for loop in entry["open_loops"]:
            status = loop["status"]
            counts[status]["count"] += 1
            if entry["entry_id"] not in counts[status]["entries"]:
                counts[status]["entries"].append(entry["entry_id"])
    total = sum(bucket["count"] for bucket in counts.values())
    resolved = counts["resolved"]["count"]
    return {
        **counts,
        "total": total,
        "resolution_rate": round(resolved / total, 2) if total else None,
    }


def _is_contradicting_behavior(text: str) -> bool:
    """Return whether behavior wording deterministically reads against a goal."""
    lower = str(text).lower()
    if any(phrase in lower for phrase in _CONTRADICTING_PHRASES):
        return True
    return bool(_tokens(lower) & _CONTRADICTING_TERMS)


def _behavior_counts(entries: list[dict]) -> dict:
    """Count aligned, contradicting, and unmatched behaviors with evidence IDs."""
    goals = [(goal, _tokens(goal)) for entry in entries for goal in entry["goals"]]
    buckets = {
        "aligned": {"count": 0, "entries": []},
        "contradicting": {"count": 0, "entries": []},
        "unmatched": {"count": 0, "entries": []},
    }
    for entry in entries:
        for behavior in entry["behaviors"]:
            btoks = _tokens(behavior)
            matched = any(_overlap(btoks, gtoks) >= _MATCH_RATIO for _, gtoks in goals)
            bucket = "unmatched"
            if matched:
                bucket = "contradicting" if _is_contradicting_behavior(behavior) else "aligned"
            buckets[bucket]["count"] += 1
            if entry["entry_id"] not in buckets[bucket]["entries"]:
                buckets[bucket]["entries"].append(entry["entry_id"])
    return buckets


def _period_summary(conn, date_from: str, date_to: str, include_seeded: bool) -> dict:
    """Compute all descriptive stats for one growth comparison period."""
    entries = _entries_for_period(conn, date_from, date_to, include_seeded)
    moods = [
        e["mood"] for e in entries
        if isinstance(e["mood"], int) and not isinstance(e["mood"], bool)
    ]
    return {
        "from": date_from,
        "to": date_to,
        "entry_count": len(entries),
        "avg_mood": round(sum(moods) / len(moods), 1) if moods else None,
        "top_themes": _theme_counts(entries),
        "open_loops": _open_loop_counts(entries),
        "behaviors": _behavior_counts(entries),
        "_entries": entries,
    }


def _data_range(conn, include_seeded: bool) -> tuple[str, str] | None:
    """Return the available mood-series date range, or None when empty."""
    where = "" if include_seeded else "WHERE is_seeded = 0"
    row = conn.execute(
        f"SELECT MIN(date) AS lo, MAX(date) AS hi FROM mood_series {where}"
    ).fetchone()
    if not row or row["lo"] is None:
        return None
    return row["lo"], row["hi"]


def _midpoint(lo: str, hi: str) -> str:
    """Return the midpoint ISO date between two inclusive ISO day bounds."""
    d_lo = date.fromisoformat(lo)
    d_hi = date.fromisoformat(hi)
    return (d_lo + (d_hi - d_lo) // 2).isoformat()


def _mood_phrase(a_avg: float | None, b_avg: float | None) -> tuple[float | None, str]:
    """Return neutral mood change text without turning the number into a verdict."""
    if a_avg is None or b_avg is None:
        return None, (
            "There is not a clear mood average in both stretches to compare; "
            "some entries did not carry a noted mood."
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
        f"recently: {abs(change):.1f} {direction} on average. This describes the "
        f"numbers in the entries, not your character."
    )


def compare_periods(
    conn,
    *,
    a_from: str,
    a_to: str,
    b_from: str,
    b_to: str,
    include_seeded: bool = False,
    verifier: SyncVerifier | None = None,
) -> dict:
    """Build the R10 descriptive growth report comparing two windows."""
    pa = _period_summary(conn, a_from, a_to, include_seeded)
    pb = _period_summary(conn, b_from, b_to, include_seeded)

    a_themes = {t["theme"] for t in pa["top_themes"]}
    b_themes = {t["theme"] for t in pb["top_themes"]}
    emerged = sorted(b_themes - a_themes)
    faded = sorted(a_themes - b_themes)
    continued = sorted(a_themes & b_themes)

    change, mood_desc = _mood_phrase(pa["avg_mood"], pb["avg_mood"])
    open_loop_delta = _open_loop_delta(pa, pb)
    behavior_delta = _behavior_delta(pa, pb)
    verified_claims = _verified_claims(pa, pb, verifier)

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
        narrative.append("There are not enough themes yet to compare across the two stretches.")

    return {
        "period_a": _public_period(pa),
        "period_b": _public_period(pb),
        "mood_delta": {
            "a_avg": pa["avg_mood"],
            "b_avg": pb["avg_mood"],
            "change": change,
            "description": mood_desc,
        },
        "theme_shifts": {"emerged": emerged, "faded": faded, "continued": continued},
        "open_loop_delta": open_loop_delta,
        "behavior_delta": behavior_delta,
        "verified_claims": verified_claims,
        "narrative": narrative,
        "closing_question": _closing_question(emerged, faded),
        "is_descriptive": True,
    }


def period_delta(
    conn,
    *,
    a_from: str,
    a_to: str,
    b_from: str,
    b_to: str,
    include_seeded: bool = False,
    verifier: SyncVerifier | None = None,
) -> dict:
    """Alias for R10 callers that name the analytics operation directly."""
    return compare_periods(
        conn,
        a_from=a_from,
        a_to=a_to,
        b_from=b_from,
        b_to=b_to,
        include_seeded=include_seeded,
        verifier=verifier,
    )


def auto_compare(conn, *, include_seeded: bool = False) -> dict | None:
    """Compare two halves of available history, or return None for short history."""
    rng = _data_range(conn, include_seeded)
    if rng is None:
        return None
    lo, hi = rng
    if lo == hi:
        return None
    mid = _midpoint(lo, hi)
    before_mid = (date.fromisoformat(mid) - timedelta(days=1)).isoformat()
    return compare_periods(
        conn,
        a_from=lo,
        a_to=before_mid,
        b_from=mid,
        b_to=hi,
        include_seeded=include_seeded,
    )


def _public_period(period: dict) -> dict:
    """Drop private parsed entries from a period summary before returning JSON."""
    return {k: v for k, v in period.items() if not k.startswith("_")}


def _open_loop_delta(pa: dict, pb: dict) -> dict:
    """Return period open-loop stats and the resolution-rate change."""
    a_rate = pa["open_loops"]["resolution_rate"]
    b_rate = pb["open_loops"]["resolution_rate"]
    change = None
    if a_rate is not None and b_rate is not None:
        change = round(b_rate - a_rate, 2)
    return {
        "period_a": pa["open_loops"],
        "period_b": pb["open_loops"],
        "resolution_rate_change": change,
    }


def _behavior_delta(pa: dict, pb: dict) -> dict:
    """Return period behavior buckets and count deltas for each bucket."""
    changes = {}
    for key in ("aligned", "contradicting", "unmatched"):
        changes[key] = pb["behaviors"][key]["count"] - pa["behaviors"][key]["count"]
    return {
        "period_a": pa["behaviors"],
        "period_b": pb["behaviors"],
        "change": changes,
    }


def _verified_claims(pa: dict, pb: dict, verifier_fn: SyncVerifier | None) -> list[dict]:
    """Return high-impact claims only when an injected verifier supports them."""
    candidates: list[tuple[str, list[str], list[str]]] = []
    b_resolved = pb["open_loops"]["resolved"]
    if b_resolved["count"] > pa["open_loops"]["resolved"]["count"]:
        entries = b_resolved["entries"]
        candidates.append((
            "More open loops were marked resolved in the recent stretch.",
            entries,
            _summaries_for(pb["_entries"], entries),
        ))

    b_contra = pb["behaviors"]["contradicting"]
    if b_contra["count"]:
        entries = b_contra["entries"]
        candidates.append((
            "The recent stretch includes behaviors counted against stated goals.",
            entries,
            _summaries_for(pb["_entries"], entries),
        ))

    out: list[dict] = []
    for claim, entry_ids, evidence in candidates:
        if verification.verify_claim_with_callable(claim, evidence, verifier_fn) is True:
            out.append({"claim": claim, "entries": entry_ids})
    return out


def _summaries_for(entries: list[dict], entry_ids: list[str]) -> list[str]:
    """Return evidence summaries for the requested entry IDs."""
    wanted = set(entry_ids)
    return [e["summary"] for e in entries if e["entry_id"] in wanted and e["summary"]]


def _closing_question(emerged: list[str], faded: list[str]) -> str:
    """Return an open reflective question that leaves interpretation to the user."""
    if emerged:
        return (
            f"What do you make of {_join(emerged)} showing up more lately; "
            "is that where your attention has been?"
        )
    if faded:
        return (
            f"You wrote about {_join(faded)} earlier and less so recently; "
            "does that match how things feel to you?"
        )
    return "Looking at these two stretches side by side, what feels different to you?"


def _join(items: list[str]) -> str:
    """Join a short list into readable prose."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])} and {items[-1]}"
