/**
 * Insights API — thin fetch wrapper over the Phase-12 mood endpoint.
 *
 * Mirrors the journal/library API style: tiny, dependency-free, talks to the
 * backend on loopback. The one call here reads the mood time-series; the backend
 * does the SQL (no model), and returns points oldest-first.
 */

const BASE = "http://127.0.0.1:8000";

/** One mood point — one entry's reading on the chart. */
export type MoodPoint = {
  entry_id: string;
  date: string; // YYYY-MM-DD
  /** −5..+5, or null. A null mood is a GAP in the line — never plotted as zero. */
  mood: number | null;
  emotions: { name: string; intensity: number }[];
  /** The entry's reflection, for the hover tooltip (null if none was stored). */
  summary: string | null;
  is_seeded: boolean;
};

export type MoodResponse = {
  from: string | null;
  to: string | null;
  include_seeded: boolean;
  points: MoodPoint[];
};

/**
 * Fetch the mood series within an inclusive day range. `includeSeeded` lifts the
 * default live-only filter so the demo chart can show the backdated seed month
 * (scripts/seed_demo.py). Throws on a transport/HTTP error so the hook surfaces it.
 */
export async function fetchMood(opts: {
  from: string;
  to: string;
  includeSeeded: boolean;
}): Promise<MoodResponse> {
  const params = new URLSearchParams({
    from: opts.from,
    to: opts.to,
  });
  if (opts.includeSeeded) params.set("include_seeded", "true");
  const resp = await fetch(`${BASE}/insights/mood?${params.toString()}`);
  if (!resp.ok) throw new Error(`GET /insights/mood -> ${resp.status}`);
  return (await resp.json()) as MoodResponse;
}
