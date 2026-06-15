import { Badge, Icon } from "../components";
import type { Health } from "../useHealth";
import type { Theme } from "../useTheme";
import { PersonaSelector } from "./PersonaSelector";
import { ThemeToggle } from "./ThemeToggle";

/**
 * TopBar — spans the content column. Left: a connection dot reflecting whether
 * the local backend is up. Right: the persona selector, the Offline ✓ privacy
 * badge, and the theme toggle.
 *
 * The Offline ✓ badge is the product's core promise made visible. It reads the
 * backend's network-guard state; full wiring (turning warning-red if anything
 * is ever attempted) lands in Phase 10 — here it shows the resting guarded state.
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
        <PersonaSelector />
        <Badge
          tone={health.netGuard ? "ok" : "warn"}
          iconBefore={<Icon name="shield-check" size={14} />}
          title="No data leaves this device. Outbound network is blocked."
        >
          {health.netGuard ? "Offline ✓" : "Network open"}
        </Badge>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </div>
    </header>
  );
}
