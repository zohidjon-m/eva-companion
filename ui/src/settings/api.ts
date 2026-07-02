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
  ai_provider_id: string;
  ai_mode: "local" | "online";
  api_base_url: string;
  api_model: string;
  local_endpoint: string;
  local_model_path: string;
  local_runtime: string;
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

export type ProviderMeta = {
  provider_id: string;
  display_name: string;
  mode: "local" | "online";
  requires_api_key: boolean;
  supports_streaming: boolean;
  supports_system_messages: boolean;
  supports_json_mode: boolean;
  supports_model_listing: boolean;
  max_context_tokens: number;
  privacy_label: string;
};

export type AiConfig = Pick<
  Settings,
  | "ai_provider_id"
  | "ai_mode"
  | "api_base_url"
  | "api_model"
  | "local_endpoint"
  | "local_model_path"
  | "local_runtime"
> & {
  requires_api_key: boolean;
  has_session_secret: boolean;
  configured: boolean;
};

export type ProviderStatus = {
  provider_id: string;
  configured: boolean;
  reachable: boolean;
  message: string;
  error: string | null;
};

export type LocalDiscovery = {
  label: string;
  base_url: string;
  models: { id: string; label: string }[];
};

export type DownloadStatus = {
  state: string;
  path: string;
  bytes_downloaded: number;
  total_bytes: number | null;
  error: string | null;
  model_present: boolean;
};

/** Return all available AI provider adapters. */
export async function fetchProviders(): Promise<ProviderMeta[]> {
  const resp = await fetch(`${BASE}/ai/providers`);
  if (!resp.ok) throw new Error(`GET /ai/providers -> ${resp.status}`);
  return ((await resp.json()) as { providers: ProviderMeta[] }).providers;
}

/** Return AI provider config without secrets. */
export async function fetchAiConfig(): Promise<AiConfig> {
  const resp = await fetch(`${BASE}/ai/config`);
  if (!resp.ok) throw new Error(`GET /ai/config -> ${resp.status}`);
  return ((await resp.json()) as { config: AiConfig }).config;
}

/** Persist non-secret AI provider config. */
export async function patchAiConfig(patch: Partial<Settings>): Promise<AiConfig> {
  const resp = await fetch(`${BASE}/ai/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!resp.ok) throw new Error(`PATCH /ai/config -> ${resp.status}`);
  return ((await resp.json()) as { config: AiConfig }).config;
}

/** Send an API key to the backend for this process; the backend does not persist it. */
export async function setSessionSecret(providerId: string, apiKey: string): Promise<AiConfig> {
  const resp = await fetch(`${BASE}/ai/secret/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider_id: providerId, api_key: apiKey }),
  });
  if (!resp.ok) throw new Error(`POST /ai/secret/session -> ${resp.status}`);
  return ((await resp.json()) as { config: AiConfig }).config;
}

/** Test the selected AI provider. */
export async function testAiProvider(): Promise<ProviderStatus> {
  const resp = await fetch(`${BASE}/ai/test`, { method: "POST" });
  if (!resp.ok) throw new Error(`POST /ai/test -> ${resp.status}`);
  return ((await resp.json()) as { status: ProviderStatus }).status;
}

/** Probe local OpenAI-compatible servers such as llama.cpp, Ollama, and LM Studio. */
export async function discoverLocalAi(): Promise<LocalDiscovery[]> {
  const resp = await fetch(`${BASE}/ai/local/discover`);
  if (!resp.ok) throw new Error(`GET /ai/local/discover -> ${resp.status}`);
  return ((await resp.json()) as { providers: LocalDiscovery[] }).providers;
}

/** Start the app-managed Gemma model download. */
export async function startModelDownload(force = false): Promise<DownloadStatus> {
  const resp = await fetch(`${BASE}/ai/local/download/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
  if (!resp.ok) throw new Error(`POST /ai/local/download/start -> ${resp.status}`);
  return ((await resp.json()) as { download: DownloadStatus }).download;
}

/** Poll the app-managed model download state. */
export async function fetchModelDownloadStatus(): Promise<DownloadStatus> {
  const resp = await fetch(`${BASE}/ai/local/download/status`);
  if (!resp.ok) throw new Error(`GET /ai/local/download/status -> ${resp.status}`);
  return ((await resp.json()) as { download: DownloadStatus }).download;
}
