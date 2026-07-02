import { useEffect, useState } from "react";
import { Badge, Button, Icon } from "../components";
import { useHealth } from "../useHealth";
import { useVoice } from "../voice/VoiceContext";
import {
  patchAiConfig,
  runPrivacyAudit,
  revealVault,
  setSessionSecret,
  testAiProvider,
  type PrivacyAudit,
} from "./api";
import { useSettings } from "./useSettings";

/**
 * SettingsScreen — Phase 10 turns Settings into the real configuration surface
 * promised by the system design (§9): one place for voice, privacy, the vault
 * location, and model status. Each control is wired live:
 *
 *   - Voice: on/off (shared with the top-bar toggle, persisted), speaking speed,
 *     and the speech-recognition (whisper) size from Phase 8.
 *   - Privacy: the live outbound-guard verdict + an on-demand audit.
 *   - Your data: where the vault lives, with "reveal in file manager".
 *   - Model: the language-model status read from /health.
 *
 * Values, choices, and ranges all come from the backend (the single source of
 * truth); the screen never hard-codes them.
 */

/** A friendly label + one-line rationale for each whisper size. */
const SIZE_INFO: Record<string, { label: string; note: string }> = {
  "base.en": {
    label: "Base (English) — faster",
    note: "The default. Small and quick; accurate for clear English speech.",
  },
  "small.en": {
    label: "Small (English) — more accurate",
    note: "Slower and a little heavier, but better on strong accents or noisy rooms.",
  },
};

export function SettingsScreen() {
  const {
    settings,
    options,
    ranges,
    vaultPath,
    loading,
    error,
    saving,
    savedTick,
    setWhisperSize,
    setVoiceSpeed,
  } = useSettings();
  const health = useHealth();
  const voice = useVoice();

  const sizes = options?.whisper_model_size ?? [];
  const current = settings?.whisper_model_size ?? "base.en";
  const sizeInfo = SIZE_INFO[current];
  const speedRange = ranges?.voice_speed;
  const [aiProvider, setAiProvider] = useState("local_llamacpp");
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [apiModel, setApiModel] = useState("");
  const [localEndpoint, setLocalEndpoint] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [aiSaving, setAiSaving] = useState(false);
  const [aiMessage, setAiMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!settings) return;
    setAiProvider(settings.ai_provider_id);
    setApiBaseUrl(settings.api_base_url);
    setApiModel(settings.api_model);
    setLocalEndpoint(settings.local_endpoint);
  }, [settings]);

  const saveAi = async () => {
    setAiSaving(true);
    setAiMessage(null);
    try {
      await patchAiConfig({
        ai_provider_id: aiProvider,
        api_base_url: apiBaseUrl,
        api_model: apiModel,
        local_endpoint: localEndpoint,
        local_runtime: aiProvider === "local_openai_compatible" ? "openai_compatible" : "llamacpp",
      });
      if (apiKey) await setSessionSecret(aiProvider, apiKey);
      const status = await testAiProvider();
      setAiMessage(status.message);
    } catch {
      setAiMessage("Could not save or test that AI provider.");
    } finally {
      setAiSaving(false);
    }
  };

  return (
    <div className="settings">
      {/* ── Voice ──────────────────────────────────────────────────────── */}
      <Group
        icon="speaker"
        title="Voice"
        sub="Hear Eva speak and talk back to her — all synthesized and transcribed on this device."
      >
        <Row
          label="Eva's voice"
          hint="Read her replies aloud as she writes them. Turn this off any time to go back to text."
        >
          <Switch
            on={voice.enabled}
            onToggle={voice.toggle}
            labelOn="On"
            labelOff="Off"
          />
        </Row>

        <Row
          label="Speaking speed"
          hint={
            speedRange
              ? `How fast Eva talks. ${settings?.voice_speed?.toFixed(2)}× (1.00 is natural).`
              : "How fast Eva talks."
          }
        >
          {speedRange && settings ? (
            <input
              type="range"
              className="settings__slider"
              min={speedRange.min}
              max={speedRange.max}
              step={speedRange.step}
              value={settings.voice_speed}
              disabled={loading || saving}
              onChange={(e) => setVoiceSpeed(Number(e.target.value))}
              aria-label="Speaking speed"
            />
          ) : (
            <span className="settings__muted">—</span>
          )}
        </Row>

        <Row
          label="Speech recognition model"
          hint={
            loading
              ? "Loading…"
              : sizeInfo?.note ?? "The model Eva uses to turn your voice into text."
          }
        >
          <div className="settings__select-wrap">
            <select
              id="whisper-size"
              className="settings__select"
              value={current}
              disabled={loading || saving || sizes.length === 0}
              onChange={(e) => setWhisperSize(e.target.value)}
            >
              {sizes.map((s) => (
                <option key={s} value={s}>
                  {SIZE_INFO[s]?.label ?? s}
                </option>
              ))}
            </select>
            <span className="settings__select-chevron" aria-hidden="true">
              <Icon name="chevron-down" size={16} />
            </span>
          </div>
        </Row>

        <p className="settings__status settings__status--row" role="status">
          {saving ? "Saving…" : savedTick > 0 ? "Saved" : ""}
        </p>
        {error && <p className="settings__error">{error}</p>}
      </Group>

      <Group
        icon="sparkle"
        title="AI provider"
        sub="Choose local AI for privacy, or connect your own online provider."
      >
        <Row label="Provider" hint="Online providers may receive prompts and journal-derived context.">
          <div className="settings__select-wrap">
            <select
              className="settings__select"
              value={aiProvider}
              disabled={loading || aiSaving}
              onChange={(e) => setAiProvider(e.target.value)}
            >
              <option value="local_llamacpp">Local Gemma with llama.cpp</option>
              <option value="local_openai_compatible">Existing local OpenAI-compatible</option>
              <option value="openai_compatible_api">Online OpenAI-compatible</option>
              <option value="anthropic">Anthropic</option>
              <option value="gemini">Gemini</option>
            </select>
            <span className="settings__select-chevron" aria-hidden="true">
              <Icon name="chevron-down" size={16} />
            </span>
          </div>
        </Row>

        {aiProvider === "local_openai_compatible" && (
          <Row label="Local endpoint" hint="Loopback /v1 URL from Ollama, LM Studio, or llama.cpp.">
            <input
              className="settings__text-input"
              value={localEndpoint}
              onChange={(e) => setLocalEndpoint(e.target.value)}
              placeholder="http://127.0.0.1:1234/v1"
            />
          </Row>
        )}

        {aiProvider === "openai_compatible_api" && (
          <Row label="API base URL" hint="The provider's OpenAI-compatible /v1 URL.">
            <input
              className="settings__text-input"
              value={apiBaseUrl}
              onChange={(e) => setApiBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
            />
          </Row>
        )}

        {aiProvider !== "local_llamacpp" && (
          <Row label="Model" hint="The model name Eva should request from this provider.">
            <input
              className="settings__text-input"
              value={apiModel}
              onChange={(e) => setApiModel(e.target.value)}
              placeholder="model name"
            />
          </Row>
        )}

        {aiProvider !== "local_llamacpp" && aiProvider !== "local_openai_compatible" && (
          <Row label="API key" hint="Sent to the backend for this session; never written to settings.json.">
            <input
              className="settings__text-input"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              type="password"
              placeholder="Paste API key"
            />
          </Row>
        )}

        <div className="settings__audit">
          <Button variant="secondary" size="sm" onClick={saveAi} disabled={aiSaving}>
            {aiSaving ? "Testing..." : "Save and test AI"}
          </Button>
          {aiMessage && <p className="settings__audit-verdict">{aiMessage}</p>}
        </div>
      </Group>

      {/* ── Privacy ────────────────────────────────────────────────────── */}
      <PrivacyGroup health={health} />

      {/* ── Your data ──────────────────────────────────────────────────── */}
      <Group
        icon="journal"
        title="Your data"
        sub="Your journal lives as plain Markdown files you own. Eva's databases are derived from them and can always be rebuilt."
      >
        <Row label="Vault location" hint="Where your entries and settings are stored on this computer.">
          <VaultLocation path={vaultPath} loading={loading} />
        </Row>
      </Group>

      {/* ── Model ──────────────────────────────────────────────────────── */}
      <ModelGroup health={health} />
    </div>
  );
}

/* ── Privacy group ─────────────────────────────────────────────────────── */

function PrivacyGroup({ health }: { health: ReturnType<typeof useHealth> }) {
  const [audit, setAudit] = useState<PrivacyAudit | null>(null);
  const [running, setRunning] = useState(false);

  const blocked = health.netGuardViolations;
  const tone = !health.netGuard ? "warn" : blocked > 0 ? "danger" : "ok";
  const label = !health.netGuard
    ? "Guard inactive"
    : blocked > 0
      ? `${blocked} blocked ✓`
      : "Offline ✓";

  const run = async () => {
    setRunning(true);
    try {
      setAudit(await runPrivacyAudit());
    } catch {
      setAudit(null);
    } finally {
      setRunning(false);
    }
  };

  return (
    <Group
      icon="shield-check"
      title="Privacy"
      sub="Eva makes no outbound network calls at runtime. A guard in the backend blocks any attempt — this is enforced in code, not a promise."
    >
      <Row
        label="Outbound network"
        hint={
          blocked > 0
            ? "Something tried to connect out and was blocked. Your data never left."
            : "No connection out of this device has been attempted."
        }
      >
        <Badge tone={tone} iconBefore={<Icon name="shield-check" size={14} />}>
          {label}
        </Badge>
      </Row>
      <div className="settings__audit">
        <Button variant="secondary" size="sm" onClick={run} disabled={running}>
          {running ? "Checking…" : "Run network audit"}
        </Button>
        {audit && <p className="settings__audit-verdict">{audit.verdict}</p>}
      </div>
    </Group>
  );
}

/* ── Vault location ────────────────────────────────────────────────────── */

function VaultLocation({ path, loading }: { path: string | null; loading: boolean }) {
  const [reason, setReason] = useState<string | null>(null);

  const reveal = async () => {
    setReason(null);
    try {
      const r = await revealVault();
      if (!r.opened) setReason("Couldn't open the file manager — the path is shown above.");
    } catch {
      setReason("Couldn't open the file manager — the path is shown above.");
    }
  };

  return (
    <div className="settings__vault">
      <code className="settings__path">{loading ? "Loading…" : path ?? "—"}</code>
      <Button
        variant="secondary"
        size="sm"
        iconBefore={<Icon name="upload" size={15} />}
        onClick={reveal}
        disabled={loading || !path}
      >
        Reveal in file manager
      </Button>
      {reason && <p className="settings__muted settings__muted--small">{reason}</p>}
    </div>
  );
}

/* ── Model group ───────────────────────────────────────────────────────── */

function ModelGroup({ health }: { health: ReturnType<typeof useHealth> }) {
  const { model, modelPresent, modelServerRunning } = health;

  const statusTone = modelPresent ? (modelServerRunning ? "ok" : "warn") : "danger";
  const statusLabel = !modelPresent
    ? "Not downloaded"
    : modelServerRunning
      ? "Running ✓"
      : "Loading…";

  return (
    <Group
      icon="sparkle"
      title="Language model"
      sub="The Gemma model Eva thinks with, running locally on the GPU."
    >
      <Row label="Status" hint={model.endpoint ? `Served at ${model.endpoint}` : "Local model server."}>
        <Badge tone={statusTone}>{statusLabel}</Badge>
      </Row>
      {model.path && (
        <Row label="Model file" hint="Where the GGUF is expected on disk.">
          <code className="settings__path settings__path--sm">{model.path}</code>
        </Row>
      )}
      {!modelPresent && model.hint && <p className="settings__error">{model.hint}</p>}
    </Group>
  );
}

/* ── Shared layout primitives ──────────────────────────────────────────── */

function Group({
  icon,
  title,
  sub,
  children,
}: {
  icon: Parameters<typeof Icon>[0]["name"];
  title: string;
  sub: string;
  children: React.ReactNode;
}) {
  return (
    <section className="settings__group">
      <div className="settings__group-head">
        <span className="settings__group-icon" aria-hidden="true">
          <Icon name={icon} size={18} />
        </span>
        <div>
          <h2 className="settings__group-title">{title}</h2>
          <p className="settings__group-sub">{sub}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="settings__row">
      <div className="settings__row-label">
        <span className="settings__label">{label}</span>
        <p className="settings__hint">{hint}</p>
      </div>
      <div className="settings__control">{children}</div>
    </div>
  );
}

/** A small accessible on/off switch. */
function Switch({
  on,
  onToggle,
  labelOn,
  labelOff,
}: {
  on: boolean;
  onToggle: () => void;
  labelOn: string;
  labelOff: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      className={`settings__switch${on ? " settings__switch--on" : ""}`}
      onClick={onToggle}
    >
      <span className="settings__switch-track" aria-hidden="true">
        <span className="settings__switch-thumb" />
      </span>
      <span className="settings__switch-label">{on ? labelOn : labelOff}</span>
    </button>
  );
}
