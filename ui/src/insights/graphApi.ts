/**
 * Knowledge-graph API — thin fetch wrapper over GET /insights/graph (Phase 14).
 *
 * The payload conforms to EVA_MEMORY_ARCHITECTURE §7.4 exactly: typed nodes with
 * their evidence entry-ids, and typed edges. Hypothesis edges carry
 * `is_hypothesis: true` and a human-readable `label`; the UI renders those dashed
 * with a confirm/dismiss affordance, never as a plain edge.
 */

const BASE = "http://127.0.0.1:8000";

/** §7.4 node types. */
export type NodeType = "theme" | "person" | "place" | "goal" | "problem" | "emotion";
/** §7.4 edge types. */
export type EdgeType = "co_occurrence" | "temporal" | "similarity" | "hypothesis";

export type GraphNode = {
  id: string;
  label: string;
  type: NodeType;
  entry_count: number;
  /** Evidence: the entry-ids this concept was drawn from. */
  entries: string[];
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  type: EdgeType;
  /** 0..1 association strength. */
  weight: number;
  /** True only for model-proposed links — rendered dashed with confirm/dismiss. */
  is_hypothesis: boolean;
  /** Human-readable label (e.g. "may lead to"); null for ordinary edges. */
  label: string | null;
  entries: string[];
};

export type GraphPayload = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

/**
 * Fetch the knowledge graph. `includeSeeded` lifts the default live-only filter so
 * the demo graph (scripts/seed_demo.py) shows. Throws on a transport/HTTP error.
 */
export async function fetchGraph(includeSeeded: boolean): Promise<GraphPayload> {
  const params = new URLSearchParams();
  if (includeSeeded) params.set("include_seeded", "true");
  const resp = await fetch(`${BASE}/insights/graph?${params.toString()}`);
  if (!resp.ok) throw new Error(`GET /insights/graph -> ${resp.status}`);
  return (await resp.json()) as GraphPayload;
}

/** One evidence entry, resolved from its id to a date + reflection for the panel. */
export type EvidenceEntry = { entry_id: string; date: string; summary: string | null };

/**
 * Build an entry-id → {date, summary} lookup so the evidence panel can show real
 * dates and reflections instead of raw ids. Reuses the mood endpoint (which
 * already returns every entry's date + summary) over a wide window, so no new
 * backend surface is needed. Returns an empty map on any error — the panel then
 * falls back to showing just the entry count.
 */
export async function fetchEvidenceLookup(
  includeSeeded: boolean,
): Promise<Map<string, EvidenceEntry>> {
  const params = new URLSearchParams({ from: "1970-01-01", to: "2999-12-31" });
  if (includeSeeded) params.set("include_seeded", "true");
  try {
    const resp = await fetch(`${BASE}/insights/mood?${params.toString()}`);
    if (!resp.ok) return new Map();
    const data = (await resp.json()) as {
      points: { entry_id: string; date: string; summary: string | null }[];
    };
    return new Map(
      data.points.map((p) => [p.entry_id, { entry_id: p.entry_id, date: p.date, summary: p.summary }]),
    );
  } catch {
    return new Map();
  }
}
