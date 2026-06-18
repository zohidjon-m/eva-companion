import { Icon } from "../components";
import { NAV_ITEMS, type SectionId } from "../nav";

/**
 * Sidebar — the left rail: the Eva mark (which doubles as the open/close toggle)
 * and the six section links. The active item gets a moss accent bar and soft
 * fill. Selection is lifted to the shell (single-source SectionId state) rather
 * than a router, since the app is a fixed six-tab surface.
 *
 * The rail collapses to a thin strip showing only the Eva mark, to free up room
 * for writing. Pressing the mark toggles it; `spin` increments on every press so
 * the circular mark turns a full revolution in one direction each time (a small
 * bit of life on a circle that's literally round). When collapsed the nav labels
 * and footer are hidden — pressing the mark brings the whole bar back.
 */

type SidebarProps = {
  active: SectionId;
  onSelect: (id: SectionId) => void;
  collapsed: boolean;
  /** Press counter: drives a full-turn spin of the mark on each toggle. */
  spin: number;
  onToggle: () => void;
};

export function Sidebar({ active, onSelect, collapsed, spin, onToggle }: SidebarProps) {
  return (
    <aside className={"sidebar" + (collapsed ? " sidebar--collapsed" : "")}>
      <div className="brand">
        <button
          type="button"
          className="brand__mark"
          onClick={onToggle}
          aria-label={collapsed ? "Open the menu" : "Close the menu"}
          aria-expanded={!collapsed}
          title={collapsed ? "Open the menu" : "Close the menu"}
        >
          {/* Eva's mark: a calm crescent + a quiet inner light. The full-turn
              spin is applied here and transitions, so each press rotates it. */}
          <svg
            viewBox="0 0 32 32"
            width="30"
            height="30"
            fill="none"
            style={{ transform: `rotate(${spin * 360}deg)` }}
          >
            <circle cx="16" cy="16" r="13" stroke="var(--accent)" strokeWidth="1.6" />
            <path
              d="M22.5 9.5a9 9 0 1 0 0 13 7 7 0 0 1 0-13z"
              fill="var(--accent)"
              opacity="0.18"
            />
            <circle cx="16" cy="16" r="3.4" fill="var(--accent)" />
          </svg>
        </button>
        <span className="brand__name">Eva</span>
      </div>

      <nav className="nav" aria-label="Primary">
        {NAV_ITEMS.map((item) => {
          const isActive = item.id === active;
          return (
            <button
              key={item.id}
              className={"nav__item" + (isActive ? " nav__item--active" : "")}
              aria-current={isActive ? "page" : undefined}
              onClick={() => onSelect(item.id)}
            >
              <span className="nav__bar" aria-hidden="true" />
              <Icon name={item.icon} size={19} className="nav__icon" />
              <span className="nav__label">{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="sidebar__foot">
        <p className="sidebar__tag">Private by design. Everything stays on this Mac.</p>
      </div>
    </aside>
  );
}
