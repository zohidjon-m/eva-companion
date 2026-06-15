import { useEffect, useState } from "react";

/**
 * useHealth — polls the FastAPI backend's /health so the shell can show the
 * connection state and drive the "Offline ✓" privacy badge.
 *
 * "Offline" here means two different good things that we keep distinct:
 *   - conn:      is the local backend process reachable? (a liveness dot)
 *   - netGuard:  is the outbound-network kill-switch active? (the privacy promise)
 * The badge in the top bar reads netGuard; the dot reads conn. Carried over
 * from the Phase 0 status screen, now lifted into the app shell.
 */

const BACKEND = "http://127.0.0.1:8000";
const POLL_MS = 3000;

export type Conn = "connecting" | "online" | "offline";

type HealthBody = {
  status: string;
  model_present: boolean;
  net_guard: boolean;
};

export type Health = {
  conn: Conn;
  modelPresent: boolean;
  /** Outbound network guard active (defaults true — the app's resting state). */
  netGuard: boolean;
};

export function useHealth(): Health {
  const [conn, setConn] = useState<Conn>("connecting");
  const [modelPresent, setModelPresent] = useState(false);
  const [netGuard, setNetGuard] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`${BACKEND}/health`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = (await res.json()) as HealthBody;
        if (cancelled) return;
        setConn("online");
        setModelPresent(body.model_present);
        setNetGuard(body.net_guard);
      } catch {
        if (cancelled) return;
        setConn("offline");
        // The privacy guard is a backend setting; if we can't read it we keep
        // showing the resting "guarded" state rather than alarming the user.
      }
    }

    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return { conn, modelPresent, netGuard };
}
