import { useCallback, useEffect, useState } from "react";

/**
 * useTheme — light/dark control for the whole app.
 *
 * The choice is written to the <html data-theme> attribute (which tokens.css
 * keys off) and persisted in localStorage so it survives reloads. If the user
 * has never chosen, we follow the OS via prefers-color-scheme. No network, no
 * cookies — just a local preference.
 */

export type Theme = "light" | "dark";
const STORAGE_KEY = "eva.theme";

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function initialTheme(): Theme {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return systemTheme();
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  // Reflect the current theme onto the document root for the CSS variables.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { theme, toggle };
}
