import { Icon } from "../components";
import type { Theme } from "../useTheme";

/**
 * ThemeToggle — a single icon button that flips light/dark. Shows the icon of
 * the theme you'd switch *to*, which is the convention people read fastest.
 */

type ThemeToggleProps = {
  theme: Theme;
  onToggle: () => void;
};

export function ThemeToggle({ theme, onToggle }: ThemeToggleProps) {
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      className="icon-btn"
      onClick={onToggle}
      title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}
    >
      <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
    </button>
  );
}
