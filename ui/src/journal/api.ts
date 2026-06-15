/**
 * Journal API — thin fetch wrappers over the Phase-5 backend endpoints.
 *
 * Kept tiny and dependency-free (the app ships offline; no fetch library). The
 * backend lives on loopback alongside the chat WebSocket. Each call returns a
 * plain typed value or throws on a transport/HTTP error so the hook can decide
 * how to surface it.
 */

const BASE = "http://127.0.0.1:8000";

/** One day in the browse list. */
export type JournalDay = {
  date: string; // YYYY-MM-DD
  count: number;
  preview: string;
};

/** One journal turn within a day's read-only view. */
export type DayEntry = {
  id: string | null;
  time: string; // HH:MM:SS
  text: string;
};

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`POST ${path} -> ${resp.status}`);
  return (await resp.json()) as T;
}

/** Save a journal entry. Resolves once it is durably stored server-side. */
export function saveJournal(text: string): Promise<{ id: string; date: string }> {
  return postJSON("/journal", { text });
}

/**
 * Ask Eva for one acknowledgment line for a saved entry. Resolves to the line,
 * or `null` when the model is offline / the call failed (a soft, expected case).
 */
export async function fetchAck(entryId: string): Promise<string | null> {
  const { acknowledgment } = await postJSON<{ acknowledgment: string | null }>(
    "/journal/acknowledge",
    { entry_id: entryId },
  );
  return acknowledgment;
}

/** Fetch the browse list of past journal days, newest first. */
export async function fetchDays(): Promise<JournalDay[]> {
  const resp = await fetch(`${BASE}/journal/days`);
  if (!resp.ok) throw new Error(`GET /journal/days -> ${resp.status}`);
  const { days } = (await resp.json()) as { days: JournalDay[] };
  return days;
}

/**
 * Fetch one day's journal entries for the read-only day view. A day with no
 * journal entries (404) resolves to an empty array rather than throwing, so the
 * caller can treat "no entries yet" uniformly.
 */
export async function fetchDay(date: string): Promise<DayEntry[]> {
  const resp = await fetch(`${BASE}/journal/day/${date}`);
  if (resp.status === 404) return [];
  if (!resp.ok) throw new Error(`GET /journal/day/${date} -> ${resp.status}`);
  const { entries } = (await resp.json()) as { entries: DayEntry[] };
  return entries;
}
