/**
 * Settings API — thin fetch wrappers over GET/PATCH /settings.
 *
 * The backend is the single source of truth for both the current values and the
 * valid choices (the `options` block), so the UI never hard-codes the whisper
 * size list. Phase 8 wires one setting; Phase 10 extends the same store.
 */

const BASE = "http://127.0.0.1:8000";

export type Settings = {
  whisper_model_size: string;
};

export type SettingsOptions = {
  whisper_model_size: string[];
};

export type SettingsResponse = {
  settings: Settings;
  options: SettingsOptions;
};

/** Fetch the current settings and the valid choices for each closed-set knob. */
export async function fetchSettings(): Promise<SettingsResponse> {
  const resp = await fetch(`${BASE}/settings`);
  if (!resp.ok) throw new Error(`GET /settings -> ${resp.status}`);
  return (await resp.json()) as SettingsResponse;
}

/** Apply a partial settings update; resolves with the full new settings. */
export async function patchSettings(patch: Partial<Settings>): Promise<SettingsResponse> {
  const resp = await fetch(`${BASE}/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!resp.ok) throw new Error(`PATCH /settings -> ${resp.status}`);
  return (await resp.json()) as SettingsResponse;
}
