import { AppShell } from "./layout/AppShell";
import { HealthProvider } from "./useHealth";
import { VoiceProvider } from "./voice/VoiceContext";

/**
 * App root. Phase 3 replaces the Phase 0 status card with the real app shell:
 * sidebar + top bar + the six section surfaces, each rendering an intentional
 * empty state until its feature phase lands. All shell state lives in AppShell.
 *
 * Phase 9 wraps everything in VoiceProvider so the top-bar voice toggle and the
 * chat screen's audio playback share one piece of voice-output state. Phase 10
 * adds HealthProvider so the top bar, the first-run setup screen, and Settings
 * all read one backend health poll.
 */
function App() {
  return (
    <HealthProvider>
      <VoiceProvider>
        <AppShell />
      </VoiceProvider>
    </HealthProvider>
  );
}

export default App;
