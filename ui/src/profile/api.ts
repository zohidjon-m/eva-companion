/**
 * Profile API — thin fetch wrappers over the Phase-13 profile endpoints.
 *
 * Mirrors the journal/insights API style: tiny, dependency-free, talks to the
 * backend on loopback. The backend renders profile.md from the structured
 * profile.json (the L3 seam); the screen only ever sees the Markdown. Saving
 * sends the edited Markdown back and gets the canonical re-rendering plus any
 * warnings about sections that couldn't be applied (the lenient §7.2 sync).
 */

const BASE = "http://127.0.0.1:8000";

/** The profile as the screen sees it: a Markdown rendering, or absent. */
export type ProfileDoc = {
  /** False for a fresh vault or a deleted profile.json — the empty state. */
  present: boolean;
  /** The profile.md text to render; null when `present` is false. */
  markdown: string | null;
};

/** The result of saving an edit: the re-rendered doc plus lenient-parse warnings. */
export type ProfileSaveResult = ProfileDoc & {
  /** Human-readable notes for any section that couldn't be applied (left as-is). */
  warnings: string[];
};

/** Fetch the current profile rendering (or the absent state). */
export async function fetchProfile(): Promise<ProfileDoc> {
  const resp = await fetch(`${BASE}/profile`);
  if (!resp.ok) throw new Error(`GET /profile -> ${resp.status}`);
  return (await resp.json()) as ProfileDoc;
}

/**
 * Save an edited profile.md. Resolves to the canonical re-rendering and any
 * warnings. A 404 (no profile to edit) throws so the hook can surface it.
 */
export async function saveProfile(markdown: string): Promise<ProfileSaveResult> {
  const resp = await fetch(`${BASE}/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown }),
  });
  if (!resp.ok) throw new Error(`PUT /profile -> ${resp.status}`);
  return (await resp.json()) as ProfileSaveResult;
}
