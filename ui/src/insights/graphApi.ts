/**
 * Knowledge-graph API: thin fetch wrapper over GET /insights/graph.
 *
 * The payload carries typed nodes with evidence entry IDs and typed edges.
 * Hypothesis edges carry `is_hypothesis: true` and a human-readable `label`; the
 * UI renders those dashed with a confirm/dismiss affordance.
 */

const BASE = "http://127.0.0.1:8000";

export type NodeType = "theme" | "person" | "place" | "goal" | "problem" | "emotion";
export type EdgeType = "co_occurrence" | "temporal" | "similarity" | "hypothesis";

export type GraphNode = {
  id: string;
  label: string;
  type: NodeType;
  entry_count: number;
  entries: string[];
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  type: EdgeType;
  weight: number;
  is_hypothesis: boolean;
  label: string | null;
  entries: string[];
};

export type GraphPayload = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

/**
 * Fetch the knowledge graph. `includeSeeded` includes seeded development rows.
 */
export async function fetchGraph(includeSeeded: boolean): Promise<GraphPayload> {
  const params = new URLSearchParams();
  if (includeSeeded) params.set("include_seeded", "true");
  const resp = await fetch(`${BASE}/insights/graph?${params.toString()}`);
  if (!resp.ok) throw new Error(`GET /insights/graph -> ${resp.status}`);
  return (await resp.json()) as GraphPayload;
}

export type EvidenceEntry = { entry_id: string; date: string; summary: string | null };

/**
 * Build an entry-id lookup for the evidence panel using the mood endpoint.
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
