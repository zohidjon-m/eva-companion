import { AppShell } from "./layout/AppShell";

/**
 * App root. Phase 3 replaces the Phase 0 status card with the real app shell:
 * sidebar + top bar + the six section surfaces, each rendering an intentional
 * empty state until its feature phase lands. All shell state lives in AppShell.
 */
function App() {
  return <AppShell />;
}

export default App;
