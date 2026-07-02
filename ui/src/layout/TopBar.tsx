import { Badge, Icon } from "../components";
import type { Health } from "../useHealth";
import type { Theme } from "../useTheme";
import { useVoice } from "../voice/VoiceContext";
import { PersonaSelector } from "./PersonaSelector";
import { ThemeToggle } from "./ThemeToggle";

/**
 * TopBar — spans the content column. Left: a connection dot reflecting whether
 * the local backend is up. Right: the voice on/off toggle (with a stop-speaking
 * button while Eva is talking), the persona selector, the Offline ✓ privacy
 * badge, and the theme toggle.
 *
 * The Offline ✓ badge is the product's core promise made visible. It reads the
 * backend's network-guard state and, Phase 10, the live truth behind it: it
 * rests green ("Offline ✓") while the guard holds with nothing attempted, turns
 * warning-red the instant any outbound call is blocked, and shows "Network open"
 * only if the guard somehow isn't installed.
 */

type TopBarProps = {
  health: Health;
  theme: Theme;
  onToggleTheme: () => void;
};

const CONN_LABEL: Record<Health["conn"], string> = {
  connecting: "Connecting to Eva…",
  online: "Eva is running locally",
  offline: "Backend not reachable",
};

export function TopBar({ health, theme, onToggleTheme }: TopBarProps) {
  return (
    <header className="topbar">
      <div className="topbar__left">
        <span
          className={`conn-dot conn-dot--${health.conn}`}
          title={CONN_LABEL[health.conn]}
          aria-hidden="true"
        />
        <span className="topbar__conn">{CONN_LABEL[health.conn]}</span>
      </div>

      <div className="topbar__right">
        <VoiceControl />
        <PersonaSelector />
        <OfflineBadge health={health} />
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </div>
    </header>
  );
}

/**
 * OfflineBadge — the privacy promise as a live status pill.
 *
 *   guard installed, 0 blocked → ok, "Offline ✓"      (the resting state)
 *   guard installed, N blocked → danger, "Blocked ✓"  (held — something tried)
 *   guard not installed        → warn, "Network open"  (should never happen)
 *
 * A blocked attempt is shown as a *good* outcome (the guard did its job) but in
 * danger-red so a demo audience can't miss that the promise is actively enforced,
 * not just configured.
 */
function OfflineBadge({ health }: { health: Health }) {
  const { netGuard, netGuardViolations } = health;
  if (health.ai.ai_mode === "online") {
    return (
      <Badge
        tone={health.ai.configured ? "warn" : "danger"}
        iconBefore={<Icon name="sparkle" size={14} />}
        title="Eva is configured to use an online API provider."
      >
        {health.ai.configured ? "Online API" : "Provider unavailable"}
      </Badge>
    );
  }
  if (!netGuard) {
    return (
      <Badge
        tone="warn"
        iconBefore={<Icon name="shield-check" size={14} />}
        title="The outbound network guard is not active."
      >
        Network open
      </Badge>
    );
  }
  if (netGuardViolations > 0) {
    return (
      <Badge
        tone="danger"
        iconBefore={<Icon name="shield-check" size={14} />}
        title={`Outbound network is blocked. ${netGuardViolations} attempt(s) stopped this session.`}
      >
        Blocked {netGuardViolations} ✓
      </Badge>
    );
  }
  return (
    <Badge
      tone="ok"
      iconBefore={<Icon name="shield-check" size={14} />}
      title="No data leaves this device. Outbound network is blocked."
    >
      Local AI
    </Badge>
  );
}

/**
 * VoiceControl — the voice on/off toggle, plus a stop-speaking button that
 * appears only while Eva is actually talking. A soft notice surfaces (and can be
 * dismissed) if her voice isn't set up, so the toggle going quiet is explained.
 */
function VoiceControl() {
  const { enabled, speaking, notice, toggle, stop, dismissNotice } = useVoice();

  return (
    <div className="voice-ctl">
      {speaking && (
        <button
          type="button"
          className="voice-ctl__stop"
          onClick={stop}
          aria-label="Stop speaking"
          title="Stop speaking"
        >
          <Icon name="stop" size={15} />
        </button>
      )}
      <button
        type="button"
        className={`voice-ctl__toggle${enabled ? " voice-ctl__toggle--on" : ""}`}
        onClick={toggle}
        aria-pressed={enabled}
        aria-label={enabled ? "Turn Eva's voice off" : "Turn Eva's voice on"}
        title={enabled ? "Eva's voice is on" : "Eva's voice is off"}
      >
        <Icon name={enabled ? "speaker" : "speaker-off"} size={18} />
        {speaking && <span className="voice-ctl__pulse" aria-hidden="true" />}
      </button>

      {notice && (
        <div className="voice-ctl__notice" role="status">
          <span className="voice-ctl__notice-text">{notice}</span>
          <button
            type="button"
            className="voice-ctl__notice-dismiss"
            onClick={dismissNotice}
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
