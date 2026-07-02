import { useEffect, useState } from "react";
import { Badge, Button, Icon } from "../components";
import { ThemeToggle } from "../layout/ThemeToggle";
import {
  discoverLocalAi,
  fetchModelDownloadStatus,
  patchAiConfig,
  setSessionSecret,
  startModelDownload,
  testAiProvider,
  type DownloadStatus,
  type LocalDiscovery,
} from "../settings/api";
import type { Health } from "../useHealth";
import type { Theme } from "../useTheme";

type FirstRunProps = {
  health: Health;
  theme: Theme;
  onToggleTheme: () => void;
  onExplore: () => void;
};

type SetupMode = "local" | "online";

export function FirstRunScreen({ health, theme, onToggleTheme, onExplore }: FirstRunProps) {
  const [mode, setMode] = useState<SetupMode>(health.ai.ai_mode ?? "local");
  const [localFound, setLocalFound] = useState<LocalDiscovery[]>([]);
  const [download, setDownload] = useState<DownloadStatus | null>(
    health.ai.local_download ?? null,
  );
  const [providerId, setProviderId] = useState("openai_compatible_api");
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (download?.state !== "downloading" && download?.state !== "starting") return;
    const id = window.setInterval(() => {
      fetchModelDownloadStatus().then(setDownload).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(id);
  }, [download?.state]);

  const chooseExistingLocal = async (item: LocalDiscovery) => {
    setBusy(true);
    setMessage(null);
    try {
      await patchAiConfig({
        ai_provider_id: "local_openai_compatible",
        local_endpoint: item.base_url,
        api_model: item.models[0]?.id ?? "",
        local_runtime: "openai_compatible",
      });
      const status = await testAiProvider();
      setMessage(status.message);
    } catch {
      setMessage("Could not save that local AI server.");
    } finally {
      setBusy(false);
    }
  };

  const findLocal = async () => {
    setBusy(true);
    setMessage(null);
    try {
      setLocalFound(await discoverLocalAi());
    } catch {
      setMessage("Could not scan local AI endpoints.");
    } finally {
      setBusy(false);
    }
  };

  const downloadLocal = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await patchAiConfig({ ai_provider_id: "local_llamacpp", local_runtime: "llamacpp" });
      setDownload(await startModelDownload(false));
    } catch {
      setMessage("Could not start the model download.");
    } finally {
      setBusy(false);
    }
  };

  const saveOnline = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await patchAiConfig({
        ai_provider_id: providerId,
        api_base_url: providerId === "openai_compatible_api" ? baseUrl : "",
        api_model: model,
      });
      await setSessionSecret(providerId, apiKey);
      const status = await testAiProvider();
      setMessage(status.message);
    } catch {
      setMessage("Could not save or test that API provider.");
    } finally {
      setBusy(false);
    }
  };

  const downloadPct =
    download?.total_bytes && download.total_bytes > 0
      ? Math.round((download.bytes_downloaded / download.total_bytes) * 100)
      : null;

  return (
    <div className="firstrun">
      <div className="firstrun__bar">
        <span className="firstrun__brand">
          <span className="firstrun__brand-mark" aria-hidden="true">
            <Icon name="feather" size={20} />
          </span>
          Eva
        </span>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </div>

      <div className="firstrun__inner">
        <header className="firstrun__head">
          <p className="eyebrow">AI setup</p>
          <h1 className="firstrun__title">Choose how Eva thinks</h1>
          <p className="firstrun__lede">
            Run AI on this computer for maximum privacy, or connect your own online
            provider. Online mode may send prompts and journal-derived context to
            the provider you choose.
          </p>
        </header>

        <div className="fr-choice">
          <button
            type="button"
            className={`fr-choice__button${mode === "local" ? " fr-choice__button--active" : ""}`}
            onClick={() => setMode("local")}
          >
            <Icon name="shield-check" size={18} />
            <span>Run AI on this computer</span>
          </button>
          <button
            type="button"
            className={`fr-choice__button${mode === "online" ? " fr-choice__button--active" : ""}`}
            onClick={() => setMode("online")}
          >
            <Icon name="sparkle" size={18} />
            <span>Use online API</span>
          </button>
        </div>

        {mode === "local" ? (
          <section className="fr-step">
            <div className="fr-step__num" aria-hidden="true">
              <Icon name="shield-check" size={18} />
            </div>
            <div className="fr-step__body">
              <div className="fr-step__head">
                <h2 className="fr-step__title">Local AI</h2>
                <Badge tone={health.ai.configured ? "ok" : "neutral"}>
                  {health.ai.configured ? "Configured" : "Setup needed"}
                </Badge>
              </div>
              <p className="fr-step__blurb">
                Eva can use an existing local AI server, or download Gemma and run it
                through llama.cpp.
              </p>
              <div className="fr-actions">
                <Button variant="secondary" onClick={findLocal} disabled={busy}>
                  Find local AI
                </Button>
                <Button variant="primary" onClick={downloadLocal} disabled={busy}>
                  Download Gemma
                </Button>
              </div>
              {localFound.length > 0 && (
                <div className="fr-list">
                  {localFound.map((item) => (
                    <button
                      type="button"
                      key={item.base_url}
                      className="fr-list__item"
                      onClick={() => chooseExistingLocal(item)}
                    >
                      <span>{item.label}</span>
                      <code>{item.base_url}</code>
                    </button>
                  ))}
                </div>
              )}
              {download && (
                <p className="firstrun__path">
                  Download: <code>{download.state}</code>
                  {downloadPct !== null ? ` ${downloadPct}%` : ""} at{" "}
                  <code>{download.path}</code>
                  {download.error ? ` (${download.error})` : ""}
                </p>
              )}
            </div>
          </section>
        ) : (
          <section className="fr-step">
            <div className="fr-step__num" aria-hidden="true">
              <Icon name="sparkle" size={18} />
            </div>
            <div className="fr-step__body">
              <div className="fr-step__head">
                <h2 className="fr-step__title">Online API</h2>
                <Badge tone={health.ai.configured ? "ok" : "warn"}>
                  {health.ai.configured ? "Configured" : "API key required"}
                </Badge>
              </div>
              <div className="fr-form">
                <select value={providerId} onChange={(e) => setProviderId(e.target.value)}>
                  <option value="openai_compatible_api">OpenAI-compatible</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="gemini">Gemini</option>
                </select>
                {providerId === "openai_compatible_api" && (
                  <input
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="Base URL"
                  />
                )}
                <input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="Model name"
                />
                <input
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="API key"
                  type="password"
                />
                <Button variant="primary" onClick={saveOnline} disabled={busy || !apiKey || !model}>
                  Save and test
                </Button>
              </div>
            </div>
          </section>
        )}

        {message && <p className="settings__error">{message}</p>}

        <footer className="firstrun__foot">
          {health.ai.configured ? (
            <p className="firstrun__done">
              <Icon name="shield-check" size={16} /> AI configured. Opening Eva...
            </p>
          ) : (
            <Button variant="ghost" onClick={onExplore}>
              Explore Eva without AI
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}
