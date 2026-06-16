import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

/**
 * Health — the single backend liveness/readiness poll, shared app-wide.
 *
 * Phase 0 introduced this as a hook; Phase 10 lifts it into a context so the top
 * bar, the first-run setup screen, and the Settings screen all read one poll
 * instead of each starting their own. The shape it exposes:
 *
 *   - conn:        is the local backend process reachable? (the liveness dot)
 *   - modelPresent: is the Gemma GGUF on disk? (gates chat; drives first-run)
 *   - model:       expected path / endpoint / download hint, for the setup screen
 *                  and the Settings "model status" row.
 *   - modelServerRunning: is the supervised llama-server subprocess alive?
 *   - voices:      are the STT / TTS weights already cached? (first-run live ✓)
 *   - netGuard:    is the outbound kill-switch active? (the privacy promise)
 *   - netGuardViolations: how many outbound calls were blocked this run. > 0 turns
 *                  the Offline badge warning-red — the promise visibly enforced.
 */

const BACKEND = "http://127.0.0.1:8000";
const POLL_MS = 3000;

export type Conn = "connecting" | "online" | "offline";

export type ModelInfo = {
  present: boolean;
  path: string;
  endpoint: string;
  hint: string | null;
};

type HealthBody = {
  status: string;
  model_present: boolean;
  model?: { model_present: boolean; model_path: string; endpoint: string; hint?: string };
  model_server_running?: boolean;
  net_guard: boolean;
  net_guard_detail?: { violations?: number };
  voices?: { stt: boolean; tts: boolean };
};

export type Health = {
  conn: Conn;
  modelPresent: boolean;
  model: ModelInfo;
  modelServerRunning: boolean;
  voices: { stt: boolean; tts: boolean };
  /** Outbound network guard active (defaults true — the app's resting state). */
  netGuard: boolean;
  /** Outbound calls blocked this run; 0 is the healthy resting state. */
  netGuardViolations: number;
};

const RESTING: Health = {
  conn: "connecting",
  modelPresent: false,
  model: { present: false, path: "", endpoint: "", hint: null },
  modelServerRunning: false,
  voices: { stt: false, tts: false },
  netGuard: true,
  netGuardViolations: 0,
};

function useHealthPoll(): Health {
  const [health, setHealth] = useState<Health>(RESTING);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`${BACKEND}/health`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = (await res.json()) as HealthBody;
        if (cancelled) return;
        setHealth({
          conn: "online",
          modelPresent: body.model_present,
          model: {
            present: body.model?.model_present ?? body.model_present,
            path: body.model?.model_path ?? "",
            endpoint: body.model?.endpoint ?? "",
            hint: body.model?.hint ?? null,
          },
          modelServerRunning: body.model_server_running ?? false,
          voices: { stt: body.voices?.stt ?? false, tts: body.voices?.tts ?? false },
          netGuard: body.net_guard,
          netGuardViolations: body.net_guard_detail?.violations ?? 0,
        });
      } catch {
        if (cancelled) return;
        // Keep the last-known good values but flip the connection dot; the privacy
        // guard stays shown as "guarded" rather than alarming on a transient drop.
        setHealth((h) => ({ ...h, conn: "offline" }));
      }
    }

    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return health;
}

const HealthContext = createContext<Health | null>(null);

/** Runs the single health poll and shares it with the tree below. */
export function HealthProvider({ children }: { children: ReactNode }) {
  const health = useHealthPoll();
  return createElement(HealthContext.Provider, { value: health }, children);
}

/** Read the shared backend health. Must be used inside a <HealthProvider>. */
export function useHealth(): Health {
  const ctx = useContext(HealthContext);
  if (!ctx) throw new Error("useHealth must be used within a HealthProvider");
  return ctx;
}
