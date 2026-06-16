import { useEffect, useState } from "react";
import { FirstRunScreen } from "../firstrun/FirstRunScreen";
import { NAV_ITEMS, type SectionId } from "../nav";
import { SECTIONS } from "../sections";
import { useHealth } from "../useHealth";
import { useTheme } from "../useTheme";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/**
 * AppShell — the frame every screen lives in: sidebar rail, top bar, and a
 * scrollable content column with a section header. Holds the two pieces of
 * shell-level state (active section, theme) and reads the shared backend health.
 *
 * Navigation is plain state, not a router: the app is a fixed six-tab surface,
 * so a SectionId in state is simpler and lighter than pulling in routing.
 *
 * Phase 10 first-run: when the backend is up but the language model isn't on
 * disk yet, the whole shell is replaced by a guided setup screen — so a fresh
 * clone lands on clear instructions, not a chat that errors. The moment the
 * model is detected (the health poll flips), the real app appears. The user can
 * also choose to explore Eva without the model (journaling still works).
 */

export function AppShell() {
  const [active, setActive] = useState<SectionId>("chat");
  const [exploreWithoutModel, setExploreWithoutModel] = useState(false);
  const { theme, toggle } = useTheme();
  const health = useHealth();

  const item = NAV_ITEMS.find((n) => n.id === active)!;
  const Section = SECTIONS[active];
  // Chat and Journal own their full-height layouts (their own scroll + pinned
  // chrome), so they render flush — without the centered content column and page
  // header that the empty-state sections use.
  const isFlush = active === "chat" || active === "journal";

  // Keep the window title in step with the section, the way a native app does.
  // Declared before any conditional return so the hook order stays stable whether
  // or not the first-run screen takes over (React's rules of hooks).
  useEffect(() => {
    document.title = `Eva — ${item.label}`;
  }, [item.label]);

  // First-run: replace the whole shell with the guided setup screen once we know
  // the backend is reachable AND the model is absent — never on the first
  // connecting tick (which would flash the wizard), and not if the user chose to
  // look around without it. The moment the model is detected, the shell returns.
  const needsSetup =
    health.conn === "online" && !health.modelPresent && !exploreWithoutModel;
  if (needsSetup) {
    return (
      <FirstRunScreen
        health={health}
        theme={theme}
        onToggleTheme={toggle}
        onExplore={() => setExploreWithoutModel(true)}
      />
    );
  }

  return (
    <div className="app">
      <Sidebar active={active} onSelect={setActive} />

      <div className="app__main">
        <TopBar health={health} theme={theme} onToggleTheme={toggle} />

        <main
          className={`content${isFlush ? " content--flush" : ""}`}
          key={active}
        >
          {isFlush ? (
            <Section />
          ) : (
            <div className="content__inner">
              <header className="page-head">
                <h1 className="page-head__title">{item.label}</h1>
                <p className="page-head__blurb">{item.blurb}</p>
              </header>
              <Section />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
