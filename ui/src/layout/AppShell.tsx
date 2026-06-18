import { useCallback, useEffect, useState } from "react";
import { FirstRunScreen } from "../firstrun/FirstRunScreen";
import { NAV_ITEMS, type SectionId } from "../nav";
import { SECTIONS } from "../sections";
import { useHealth } from "../useHealth";
import { useTheme } from "../useTheme";
import { ShellProvider } from "./ShellContext";
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
  // The sidebar can be tucked to a thin rail (just the Eva mark) to free up room
  // for writing; pressing the mark toggles it. `spin` counts presses so the mark
  // does a full turn in the same direction on every toggle (see Sidebar).
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [spin, setSpin] = useState(0);
  // The chat history rail (past conversations). Pressing "Chat" while already on
  // Chat tucks it away / brings it back — "press once to go to chats, press again
  // and the previous chats go inside".
  const [chatRailOpen, setChatRailOpen] = useState(true);
  const { theme, toggle } = useTheme();
  const health = useHealth();

  // The Eva mark is a master "minimize": one press tucks away everything —
  // the nav rail AND the chat history column — leaving just the thread and
  // composer; the next press brings the whole frame back. (The "press Chat
  // again" gesture below still toggles only the chat rail on its own.)
  const toggleSidebar = useCallback(() => {
    setSidebarOpen((o) => {
      const next = !o;
      setChatRailOpen(next);
      return next;
    });
    setSpin((s) => s + 1);
  }, []);

  // Nav selection with the chat double-press gesture: selecting a different
  // section just switches; pressing the already-active Chat tab toggles its rail.
  const onSelect = useCallback(
    (id: SectionId) => {
      if (id === "chat" && active === "chat") {
        setChatRailOpen((o) => !o);
        return;
      }
      setActive(id);
    },
    [active],
  );

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
    <ShellProvider value={{ chatRailOpen }}>
      <div className={`app${sidebarOpen ? "" : " app--rail-collapsed"}`}>
        <Sidebar
          active={active}
          onSelect={onSelect}
          collapsed={!sidebarOpen}
          spin={spin}
          onToggle={toggleSidebar}
        />

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
    </ShellProvider>
  );
}
