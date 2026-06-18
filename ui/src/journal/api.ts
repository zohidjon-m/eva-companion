/**
 * Journal API — thin fetch wrappers over the Phase-5 backend endpoints.
 *
 * Kept tiny and dependency-free (the app ships offline; no fetch library). The
 * backend lives on loopback alongside the chat WebSocket. Each call returns a
 * plain typed value or throws on a transport/HTTP error so the hook can decide
 * how to surface it.
 */

const BASE = "http://127.0.0.1:8000";

/** Where journal images are served from (loopback, offline). */
const MEDIA_URL_BASE = `${BASE}/journal/`; // + "media/<file>"

/**
 * Expand vault-relative image paths in stored Markdown into loadable loopback
 * URLs, so the editor and read views can display the photo. Storage keeps the
 * path relative (``media/<file>``) so the Markdown stays portable; the absolute
 * URL only ever exists in the running UI.
 */
export function toDisplayMarkdown(md: string): string {
  return md.replace(/(!\[[^\]]*\]\()media\//g, `$1${MEDIA_URL_BASE}media/`);
}

/** Inverse of {@link toDisplayMarkdown}: shrink loopback image URLs back to the
 *  vault-relative form before the Markdown is saved as the L0 source of truth. */
export function toStorageMarkdown(md: string): string {
  return md.split(`${MEDIA_URL_BASE}media/`).join("media/");
}

/** Upload one image for a journal entry; returns its relative path + display URL. */
export async function uploadMedia(file: File): Promise<{ path: string; url: string }> {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch(`${BASE}/journal/media`, { method: "POST", body: form });
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      detail = ((await resp.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* non-JSON error */
    }
    throw new Error(detail);
  }
  const { path } = (await resp.json()) as { path: string };
  return { path, url: `${MEDIA_URL_BASE}${path}` };
}

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

/** One journal post in the flat history index (newest first). */
export type JournalEntry = {
  id: string; // entry id
  date: string; // YYYY-MM-DD
  created_at: string; // ISO-8601 (YYYY-MM-DDTHH:MM:SS)
  preview: string;
  word_count: number;
};

/** One full journal post for the read-only entry view. */
export type JournalEntryFull = {
  id: string;
  date: string;
  created_at: string;
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

/** Fetch the flat history of individual journal posts, newest first. */
export async function fetchEntries(): Promise<JournalEntry[]> {
  const resp = await fetch(`${BASE}/journal/entries`);
  if (!resp.ok) throw new Error(`GET /journal/entries -> ${resp.status}`);
  const { entries } = (await resp.json()) as { entries: JournalEntry[] };
  return entries;
}

/**
 * Save an edit to an existing journal post: rewrites its Markdown on disk and
 * re-derives the index/insights server-side. Resolves to the updated post.
 */
export function updateJournal(id: string, text: string): Promise<JournalEntryFull> {
  return postJSON(`/journal/entry/${id}`, { text });
}

/** Fetch one full journal post by id for the read-only view (404 → null). */
export async function fetchEntry(id: string): Promise<JournalEntryFull | null> {
  const resp = await fetch(`${BASE}/journal/entry/${id}`);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`GET /journal/entry/${id} -> ${resp.status}`);
  return (await resp.json()) as JournalEntryFull;
}
