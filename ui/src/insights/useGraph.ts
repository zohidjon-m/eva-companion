import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchEvidenceLookup,
  fetchGraph,
  type EvidenceEntry,
  type GraphPayload,
} from "./graphApi";

/**
 * useGraph — the data behind the knowledge-graph view, kept out of the component.
 *
 * Owns the demo toggle (live-only by default, like the mood chart), the fetched
 * §7.4 payload, and an entry-id → {date, summary} lookup so the evidence panel can
 * show real dates and reflections rather than raw ids. Loading/loaded/error let
 * the view show the right state, including a tasteful empty state on a fresh vault.
 */

export type UseGraph = {
  graph: GraphPayload;
  evidence: Map<string, EvidenceEntry>;
  includeSeeded: boolean;
  loaded: boolean;
  error: boolean;
  setIncludeSeeded: (on: boolean) => void;
};

const EMPTY: GraphPayload = { nodes: [], edges: [] };

export function useGraph(): UseGraph {
  const [includeSeeded, setIncludeSeeded] = useState(false);
  const [graph, setGraph] = useState<GraphPayload>(EMPTY);
  const [evidence, setEvidence] = useState<Map<string, EvidenceEntry>>(new Map());
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setError(false);
    try {
      const [g, ev] = await Promise.all([
        fetchGraph(includeSeeded),
        fetchEvidenceLookup(includeSeeded),
      ]);
      if (mounted.current) {
        setGraph(g);
        setEvidence(ev);
      }
    } catch {
      if (mounted.current) {
        setError(true);
        setGraph(EMPTY);
      }
    } finally {
      if (mounted.current) setLoaded(true);
    }
  }, [includeSeeded]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { graph, evidence, includeSeeded, loaded, error, setIncludeSeeded };
}
