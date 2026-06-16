import { useState } from "react";
import { Badge, Button, Icon } from "../components";
import { ThemeToggle } from "../layout/ThemeToggle";
import type { Health } from "../useHealth";
import type { Theme } from "../useTheme";

/**
 * FirstRunScreen — the Phase 10 "wizard-lite" setup surface.
 *
 * Shown by the shell when the backend is up but the language model isn't on disk
 * yet, so a fresh clone meets clear, copyable instructions instead of a chat that
 * errors. It is deliberately *lite* (not the full old setup wizard): one required
 * step (the model) with a live "found ✓" check driven by the health poll, plus an
 * optional voice step. There is nothing to click to proceed — the instant the
 * model is detected the shell swaps this screen for the real app.
 *
 * Every command here is run by the user in their own terminal; the app issues no
 * downloads itself. The commands mirror the repo's setup scripts exactly.
 */

type FirstRunProps = {
  health: Health;
  theme: Theme;
  onToggleTheme: () => void;
  onExplore: () => void;
};

// The exact setup commands (they mirror scripts/ in the repo). The model is
// required for chat; voice is optional and degrades to text if absent.
const MODEL_CMD = "bash scripts/download_model_mac.sh";
const STT_CMD = "backend/.venv/bin/python scripts/download_whisper_model.py";
const TTS_CMD = "backend/.venv/bin/python scripts/download_kokoro_model.py";

export function FirstRunScreen({ health, theme, onToggleTheme, onExplore }: FirstRunProps) {
  const voiceReady = health.voices.stt && health.voices.tts;

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
          <p className="eyebrow">First-time setup</p>
          <h1 className="firstrun__title">Let's get Eva ready</h1>
          <p className="firstrun__lede">
            Eva runs entirely on this machine — nothing you say ever leaves it. To
            do that she needs her models downloaded once. Run the command below in
            a terminal at the project root; this page updates on its own as each
            piece arrives.
          </p>
        </header>

        <Step
          n={1}
          title="Language model"
          required
          ready={health.modelPresent}
          readyLabel="Model found"
          waitingLabel="Not downloaded yet"
          blurb="The Gemma model Eva thinks with. This is the one piece chat can't run without (about 3 GB, downloaded once)."
          command={MODEL_CMD}
          extra={
            health.model.path ? (
              <p className="firstrun__path">
                Expected at <code>{health.model.path}</code>
              </p>
            ) : null
          }
        />

        <Step
          n={2}
          title="Voice (optional)"
          required={false}
          ready={voiceReady}
          readyLabel="Voice ready"
          waitingLabel="Not set up"
          blurb="Speak to Eva and hear her reply. Optional — without it she simply stays in text, and you can add it any time. Run both commands for speech-in and speech-out."
          command={STT_CMD}
          command2={TTS_CMD}
          partial={!voiceReady && (health.voices.stt || health.voices.tts)}
        />

        <footer className="firstrun__foot">
          {health.modelPresent ? (
            <p className="firstrun__done">
              <Icon name="shield-check" size={16} /> All set — opening Eva…
            </p>
          ) : (
            <>
              <p className="firstrun__hint">
                Eva will open automatically the moment the model is ready.
              </p>
              <Button variant="ghost" onClick={onExplore}>
                Explore Eva without the model →
              </Button>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}

type StepProps = {
  n: number;
  title: string;
  required: boolean;
  ready: boolean;
  readyLabel: string;
  waitingLabel: string;
  blurb: string;
  command: string;
  command2?: string;
  extra?: React.ReactNode;
  /** Voice only: one of the two halves is present but not both. */
  partial?: boolean;
};

/** One setup step: a numbered card with a live status pill and copyable command(s). */
function Step({
  n,
  title,
  required,
  ready,
  readyLabel,
  waitingLabel,
  blurb,
  command,
  command2,
  extra,
  partial,
}: StepProps) {
  return (
    <section className={`fr-step${ready ? " fr-step--ready" : ""}`}>
      <div className="fr-step__num" aria-hidden="true">
        {ready ? <Icon name="shield-check" size={18} /> : n}
      </div>
      <div className="fr-step__body">
        <div className="fr-step__head">
          <h2 className="fr-step__title">{title}</h2>
          {required && !ready && (
            <span className="fr-step__req">Required</span>
          )}
          <Badge tone={ready ? "ok" : partial ? "warn" : "neutral"}>
            {ready ? `${readyLabel} ✓` : partial ? "Halfway there" : waitingLabel}
          </Badge>
        </div>
        <p className="fr-step__blurb">{blurb}</p>
        <CommandLine command={command} />
        {command2 && <CommandLine command={command2} />}
        {extra}
      </div>
    </section>
  );
}

/** A monospace command with a copy-to-clipboard button (no network, local only). */
function CommandLine({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="fr-cmd">
      <code className="fr-cmd__text">{command}</code>
      <button
        type="button"
        className="fr-cmd__copy"
        onClick={copy}
        aria-label={copied ? "Copied" : "Copy command"}
      >
        {copied ? "Copied!" : "Copy"}
      </button>
    </div>
  );
}
