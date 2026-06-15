import { useEffect, useState } from "react";

// Where the FastAPI backend listens (see backend/app.py BACKEND_PORT).
const BACKEND = "http://127.0.0.1:8000";
const POLL_MS = 3000;

type Health = {
  status: string;
  model_present: boolean;
  net_guard: boolean;
};

type Conn = "connecting" | "online" | "offline";

/**
 * Phase 0 shell screen: a single status dot that reflects whether the backend
 * is answering /health. Green = backend up, red = unreachable. This is the
 * whole UI for now — the real app shell arrives in Phase 3.
 */
function App() {
  const [conn, setConn] = useState<Conn>("connecting");
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`${BACKEND}/health`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = (await res.json()) as Health;
        if (!cancelled) {
          setHealth(body);
          setConn("online");
        }
      } catch {
        if (!cancelled) {
          setConn("offline");
          setHealth(null);
        }
      }
    }

    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const label =
    conn === "online"
      ? "Backend connected"
      : conn === "offline"
        ? "Backend unreachable"
        : "Connecting…";

  return (
    <main className="shell">
      <div className="card">
        <h1>Eva</h1>
        <p className="subtitle">Offline journaling companion</p>

        <div className="status">
          <span className={`dot dot--${conn}`} aria-hidden="true" />
          <span className="status-label">{label}</span>
        </div>

        {health && (
          <dl className="health">
            <div>
              <dt>Status</dt>
              <dd>{health.status}</dd>
            </div>
            <div>
              <dt>Model present</dt>
              <dd>{health.model_present ? "yes" : "no (Phase 1)"}</dd>
            </div>
            <div>
              <dt>Network guard</dt>
              <dd>{health.net_guard ? "active ✓" : "OFF"}</dd>
            </div>
          </dl>
        )}
      </div>
    </main>
  );
}

export default App;
