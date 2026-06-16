import { useCallback, useEffect, useRef, useState } from "react";
import { fetchGrowth, type GrowthResponse } from "./growthApi";

/**
 * useGrowth — the data behind the growth report, kept out of the component.
 *
 * Owns the demo toggle (live-only by default, like the other Insights blocks) and
 * the fetched report. The report itself is descriptive by contract; this hook adds
 * no interpretation, it only manages fetch/loading/error state.
 */

export type UseGrowth = {
  report: GrowthResponse | null;
  includeSeeded: boolean;
  loaded: boolean;
  error: boolean;
  setIncludeSeeded: (on: boolean) => void;
};

export function useGrowth(): UseGrowth {
  const [includeSeeded, setIncludeSeeded] = useState(false);
  const [report, setReport] = useState<GrowthResponse | null>(null);
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
      const data = await fetchGrowth(includeSeeded);
      if (mounted.current) setReport(data);
    } catch {
      if (mounted.current) {
        setError(true);
        setReport(null);
      }
    } finally {
      if (mounted.current) setLoaded(true);
    }
  }, [includeSeeded]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { report, includeSeeded, loaded, error, setIncludeSeeded };
}
