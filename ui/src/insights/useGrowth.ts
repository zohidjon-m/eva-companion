import { useCallback, useEffect, useRef, useState } from "react";
import { fetchGrowth, type GrowthResponse } from "./growthApi";

/**
 * useGrowth — the data behind the growth report, kept out of the component.
 *
 * Reads only real entries (the demo toggle is gone). The report is descriptive by
 * contract; this hook adds no interpretation, only fetch/loading/error state.
 */

export type UseGrowth = {
  report: GrowthResponse | null;
  loaded: boolean;
  error: boolean;
};

export function useGrowth(): UseGrowth {
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
      const data = await fetchGrowth(false);
      if (mounted.current) setReport(data);
    } catch {
      if (mounted.current) {
        setError(true);
        setReport(null);
      }
    } finally {
      if (mounted.current) setLoaded(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { report, loaded, error };
}
