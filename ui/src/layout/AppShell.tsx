import { useEffect, useState } from "react";
import { NAV_ITEMS, type SectionId } from "../nav";
import { SECTIONS } from "../sections";
import { useHealth } from "../useHealth";
import { useTheme } from "../useTheme";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/**
 * AppShell — the frame every screen lives in: sidebar rail, top bar, and a
 * scrollable content column with a section header. Holds the two pieces of
 * shell-level state (active section, theme) and the backend health poll.
 *
 * Navigation is plain state, not a router: the app is a fixed six-tab surface,
 * so a SectionId in state is simpler and lighter than pulling in routing.
 */

export function AppShell() {
  const [active, setActive] = useState<SectionId>("chat");
  const { theme, toggle } = useTheme();
  const health = useHealth();

  const item = NAV_ITEMS.find((n) => n.id === active)!;
  const Section = SECTIONS[active];
  // Chat owns its full-height layout (its own scroll + a pinned composer), so it
  // renders flush — without the centered content column and page header that the
  // empty-state sections use.
  const isFlush = active === "chat";

  // Keep the window title in step with the section, the way a native app does.
  useEffect(() => {
    document.title = `Eva — ${item.label}`;
  }, [item.label]);

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
