import { useCallback, useEffect, useRef, useState } from "react";
import { fetchMood, type MoodPoint } from "./api";

/**
 * useInsightsMood — the state behind the mood chart, kept here so the screen
 * stays presentational.
 *
 * It owns three controls and the fetched data:
 *   - `range` (7 or 30 days) → the window the chart spans, sent as from/to bounds.
 *   - `includeSeeded` → the demo toggle; lifts the backend's live-only filter so
 *     the seeded month (scripts/seed_demo.py) shows. Off by default: real usage
 *     sees only real entries.
 *   - the points, plus loading/loaded/error so the screen can show the right state.
 *
 * The window is computed from the local "today" (not UTC, so the day never shifts
 * under a timezone) and refetched whenever a control changes.
 */

export type MoodRange = 7 | 30;

export type UseInsightsMood = {
  points: MoodPoint[];
  range: MoodRange;
  includeSeeded: boolean;
  from: string;
  to: string;
  loading: boolean;
  loaded: boolean;
  error: boolean;
  setRange: (r: MoodRange) => void;
  setIncludeSeeded: (on: boolean) => void;
  refresh: () => void;
};

/** Format a Date as a local YYYY-MM-DD (avoids the UTC day-shift of toISOString). */
function isoDay(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** The inclusive [from, to] window for a range ending today (local). */
function windowFor(range: MoodRange): { from: string; to: string } {
  const today = new Date();
  const start = new Date(today);
  start.setDate(today.getDate() - (range - 1));
  return { from: isoDay(start), to: isoDay(today) };
}

export function useInsightsMood(): UseInsightsMood {
  const [range, setRange] = useState<MoodRange>(30);
  const [includeSeeded, setIncludeSeeded] = useState(false);
  const [points, setPoints] = useState<MoodPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  const { from, to } = windowFor(range);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const data = await fetchMood({ from, to, includeSeeded });
      if (mounted.current) setPoints(data.points);
    } catch {
      if (mounted.current) {
        setError(true);
        setPoints([]);
      }
    } finally {
      if (mounted.current) {
        setLoading(false);
        setLoaded(true);
      }
    }
  }, [from, to, includeSeeded]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    points,
    range,
    includeSeeded,
    from,
    to,
    loading,
    loaded,
    error,
    setRange,
    setIncludeSeeded,
    refresh,
  };
}
