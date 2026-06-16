import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchSettings, patchSettings } from "../settings/api";
import { AudioQueue } from "./audioQueue";

/**
 * VoiceContext — shared voice-output state for the whole app (Phase 9).
 *
 * Voice out spans two places: the top bar owns the on/off toggle and the
 * stop-speaking button, while the chat hook receives the audio frames and feeds
 * them to playback. Rather than thread props through the shell, both read this
 * context.
 *
 * What it holds:
 *   - `enabled`   — whether Eva should speak her replies (the top-bar toggle).
 *                   Sent to the backend per turn so TTS is opt-in and the 8 GB
 *                   budget is untouched when voice is off. Phase 10 makes this the
 *                   single source of truth for the toggle and persists it to the
 *                   settings store, so the top-bar toggle and the Settings toggle
 *                   stay in sync and the choice survives a restart.
 *   - `speaking`  — whether audio is currently playing (drives the stop button +
 *                   a subtle indicator).
 *   - `enqueue`   — the chat hook calls this with each `audio` frame's payload.
 *   - `stop`      — the user's "stop speaking": halt now, drop the rest.
 *   - `notice`    — a soft message when voice can't be set up (Kokoro missing),
 *                   shown once so the user knows why Eva went quiet.
 *
 * Toggling voice off also stops any audio in flight — that's the plan's "toggle
 * voice off mid-reply → audio stops, text continues" (the text stream is the
 * chat socket and is never interrupted).
 */

type VoiceContextValue = {
  enabled: boolean;
  speaking: boolean;
  notice: string | null;
  toggle: () => void;
  enqueue: (wavBase64: string) => void;
  stop: () => void;
  /** Called by the chat hook on a `voice_unavailable` frame: go quiet + explain. */
  reportUnavailable: (message: string) => void;
  dismissNotice: () => void;
};

const VoiceContext = createContext<VoiceContextValue | null>(null);

export function VoiceProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // One AudioQueue for the app's lifetime; it reports playing/idle into state.
  const queueRef = useRef<AudioQueue | null>(null);
  if (queueRef.current === null) {
    queueRef.current = new AudioQueue(setSpeaking);
  }
  const queue = queueRef.current;

  // Load the persisted toggle once on mount so the choice survives a restart.
  // Best-effort: if settings can't be read, we keep the safe default (off).
  useEffect(() => {
    let alive = true;
    fetchSettings()
      .then((r) => {
        if (alive) setEnabled(r.settings.voice_enabled);
      })
      .catch(() => {
        /* backend not up yet — default off is fine */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Persist a toggle change without blocking the UI (fire-and-forget).
  const persistEnabled = useCallback((on: boolean) => {
    patchSettings({ voice_enabled: on }).catch(() => {
      /* a failed persist shouldn't disrupt the in-session toggle */
    });
  }, []);

  const stop = useCallback(() => queue.stop(), [queue]);

  const enqueue = useCallback(
    (wavBase64: string) => {
      // Guard against a late frame after the user muted: don't start speaking when
      // voice is off. (Reads state at call time via the functional setter pattern.)
      setEnabled((on) => {
        if (on) queue.enqueue(wavBase64);
        return on;
      });
    },
    [queue],
  );

  const toggle = useCallback(() => {
    setEnabled((on) => {
      const next = !on;
      // Turning voice off stops any speech immediately (the text keeps streaming).
      if (!next) queue.stop();
      persistEnabled(next);
      return next;
    });
  }, [queue, persistEnabled]);

  const reportUnavailable = useCallback(
    (message: string) => {
      queue.stop();
      setEnabled(false);
      setNotice(
        message ||
          "Eva's voice isn't set up yet, so she'll reply in text. You can keep chatting.",
      );
    },
    [queue],
  );

  const dismissNotice = useCallback(() => setNotice(null), []);

  const value = useMemo<VoiceContextValue>(
    () => ({ enabled, speaking, notice, toggle, enqueue, stop, reportUnavailable, dismissNotice }),
    [enabled, speaking, notice, toggle, enqueue, stop, reportUnavailable, dismissNotice],
  );

  return <VoiceContext.Provider value={value}>{children}</VoiceContext.Provider>;
}

/** Read the voice context. Must be used inside a <VoiceProvider>. */
export function useVoice(): VoiceContextValue {
  const ctx = useContext(VoiceContext);
  if (!ctx) throw new Error("useVoice must be used within a VoiceProvider");
  return ctx;
}
