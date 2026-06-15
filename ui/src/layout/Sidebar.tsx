import { Icon } from "../components";
import { NAV_ITEMS, type SectionId } from "../nav";

/**
 * Sidebar — the left rail: the Eva wordmark and the six section links. The
 * active item gets a moss accent bar and soft fill. Selection is lifted to the
 * shell (single-source SectionId state) rather than a router, since the app is
 * a fixed six-tab surface.
 */

type SidebarProps = {
  active: SectionId;
  onSelect: (id: SectionId) => void;
};

export function Sidebar({ active, onSelect }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand__mark" aria-hidden="true">
          {/* Eva's mark: a calm crescent + a quiet inner light. */}
          <svg viewBox="0 0 32 32" width="30" height="30" fill="none">
            <circle cx="16" cy="16" r="13" stroke="var(--accent)" strokeWidth="1.6" />
            <path
              d="M22.5 9.5a9 9 0 1 0 0 13 7 7 0 0 1 0-13z"
              fill="var(--accent)"
              opacity="0.18"
            />
            <circle cx="16" cy="16" r="3.4" fill="var(--accent)" />
          </svg>
        </span>
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
