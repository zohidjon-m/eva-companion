/**
 * Growth-report API — thin fetch wrapper over GET /insights/growth.
 *
 * The report is DESCRIPTIVE by contract (System Design §11/§12): it states what
 * was written across two periods — entry counts, average noted mood, theme shifts
 * — and closes with a reflective question. It is never a verdict, so the UI just
 * renders the prose the backend computed; it adds no judgment of its own.
 */

const BASE = "http://127.0.0.1:8000";

export type PeriodSummary = {
  from: string;
  to: string;
  entry_count: number;
  /** Average noted mood (−5..+5), or null when no day in the window had a mood. */
  avg_mood: number | null;
  top_themes: { theme: string; count: number }[];
  open_loops: OpenLoopStats;
  behaviors: BehaviorStats;
};

export type EvidenceBucket = { count: number; entries: string[] };

export type OpenLoopStats = {
  open: EvidenceBucket;
  updated: EvidenceBucket;
  resolved: EvidenceBucket;
  total: number;
  resolution_rate: number | null;
};

export type BehaviorStats = {
  aligned: EvidenceBucket;
  contradicting: EvidenceBucket;
  unmatched: EvidenceBucket;
};

export type GrowthReport = {
  empty: false;
  include_seeded: boolean;
  period_a: PeriodSummary;
  period_b: PeriodSummary;
  mood_delta: { a_avg: number | null; b_avg: number | null; change: number | null; description: string };
  theme_shifts: { emerged: string[]; faded: string[]; continued: string[] };
  open_loop_delta: {
    period_a: OpenLoopStats;
    period_b: OpenLoopStats;
    resolution_rate_change: number | null;
  };
  behavior_delta: {
    period_a: BehaviorStats;
    period_b: BehaviorStats;
    change: { aligned: number; contradicting: number; unmatched: number };
  };
  verified_claims: { claim: string; entries: string[] }[];
  /** Observational sentences — descriptive, never a judgment. */
  narrative: string[];
  /** An open question; the user is the interpreter. */
  closing_question: string;
  is_descriptive: true;
};

export type GrowthEmpty = { empty: true; include_seeded: boolean };

export type GrowthResponse = GrowthReport | GrowthEmpty;

/**
 * Fetch the growth report. With no explicit windows the backend auto-splits the
 * available history at its midpoint. `includeSeeded` lifts the live-only filter.
 * Throws on a transport/HTTP error so the hook can surface it.
 */
export async function fetchGrowth(includeSeeded: boolean): Promise<GrowthResponse> {
  const params = new URLSearchParams();
  if (includeSeeded) params.set("include_seeded", "true");
  const resp = await fetch(`${BASE}/insights/growth?${params.toString()}`);
  if (!resp.ok) throw new Error(`GET /insights/growth -> ${resp.status}`);
  return (await resp.json()) as GrowthResponse;
}
