/**
 * Settings API — thin fetch wrappers over GET/PATCH /settings and the Phase-10
 * privacy/vault helpers.
 *
 * The backend is the single source of truth for the current values, the valid
 * choices (`options`), the numeric bounds (`ranges`), and where the vault lives
 * (`vault_path`), so the UI never hard-codes any of them. Model status and voice
 * presence come from /health (the shared poll), not from here.
 */

const BASE = "http://127.0.0.1:8000";

export type Settings = {
  whisper_model_size: string;
  voice_enabled: boolean;
  voice_speed: number;
};

export type SettingsOptions = {
  whisper_model_size: string[];
};

export type SettingsRanges = {
  voice_speed: { min: number; max: number; step: number };
};

export type SettingsResponse = {
  settings: Settings;
  options: SettingsOptions;
  ranges: SettingsRanges;
  vault_path: string;
};

/** Fetch the current settings plus the choices, ranges, and vault path. */
export async function fetchSettings(): Promise<SettingsResponse> {
  const resp = await fetch(`${BASE}/settings`);
  if (!resp.ok) throw new Error(`GET /settings -> ${resp.status}`);
  return (await resp.json()) as SettingsResponse;
}

/** Apply a partial settings update; resolves with the full new settings bundle. */
export async function patchSettings(
  patch: Partial<Settings>,
): Promise<SettingsResponse> {
  const resp = await fetch(`${BASE}/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!resp.ok) throw new Error(`PATCH /settings -> ${resp.status}`);
  return (await resp.json()) as SettingsResponse;
}

export type PrivacyAudit = {
  verdict: string;
  installed: boolean;
  allow_host: string | null;
  allow_ips: string[];
  violations: number;
  last_blocked: string | null;
};

/** Run the on-demand privacy audit (reads the live outbound-guard state). */
export async function runPrivacyAudit(): Promise<PrivacyAudit> {
  const resp = await fetch(`${BASE}/privacy/audit`);
  if (!resp.ok) throw new Error(`GET /privacy/audit -> ${resp.status}`);
  return (await resp.json()) as PrivacyAudit;
}

/** Ask the backend to open the vault folder in the OS file manager. */
export async function revealVault(): Promise<{ opened: boolean; path: string }> {
  const resp = await fetch(`${BASE}/vault/reveal`, { method: "POST" });
  if (!resp.ok) throw new Error(`POST /vault/reveal -> ${resp.status}`);
  return (await resp.json()) as { opened: boolean; path: string };
}
