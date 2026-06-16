import { useCallback, useEffect, useRef, useState } from "react";

/**
 * useChat — owns the WebSocket conversation with Eva (`WS /chat`).
 *
 * Responsibilities, kept in this one hook so the screen stays presentational:
 *   - hold the running session transcript (user + Eva messages)
 *   - stream Eva's reply token-by-token into the latest Eva bubble
 *   - expose `replying` so the composer can disable input while Eva talks
 *   - recover from a dropped socket: surface an error, auto-reconnect, and let
 *     the user retry the turn that was in flight (without re-saving it)
 *
 * The backend protocol (Phase 1/4/9):
 *   → send `{ text, voice }`     normal turn (backend captures it to the vault)
 *   → send `{ text, capture:false, voice }`  retry of an already-saved turn
 *   ← { type:"start" }              Eva is about to speak
 *   ← { type:"citations", citations } grounded sources for this turn (Phase 7)
 *   ← { type:"token", content }     one streamed piece
 *   ← { type:"done" }               text reply complete
 *   ← { type:"audio", seq, data }   one synthesized sentence (Phase 9, voice on)
 *   ← { type:"audio_done" }         no more audio will arrive this turn
 *   ← { type:"voice_unavailable" }  TTS couldn't load → fall back to text
 *   ← { type:"error", message }     graceful failure (model missing/error)
 *
 * The citations frame (when present) arrives right after `start`, before any
 * token, so the source chips can render with the bubble. A turn that retrieved
 * nothing — a vent, or a question with no match in the library — sends no such
 * frame, so no chips appear and Eva never shows a fabricated source.
 *
 * Phase 9 voice: when voice is on the same socket also carries `audio` frames,
 * each a base64 WAV for one sentence, in order. They're handed to the caller's
 * `onAudio` for sequential playback; the text stream is never blocked by them.
 */

const WS_URL = "ws://127.0.0.1:8000/chat";
const RECONNECT_MS = 1500;

export type ChatRole = "user" | "eva";

/** One grounded source behind an Eva reply (Phase 7 RAG). */
export type Citation = {
  source_file: string;
  page: number | null;
  section: string | null;
  /** Short display label, e.g. "book.pdf · p. 42". */
  label: string;
  /** The exact passage Eva was grounded in (shown when a chip is opened). */
  text: string;
};

export type Message = {
  id: string;
  role: ChatRole;
  text: string;
  /** Eva bubble still streaming (awaiting or receiving tokens). */
  streaming?: boolean;
  /** This turn failed (model error or the socket dropped mid-reply). */
  failed?: boolean;
  /** Grounded sources for this Eva turn (absent when nothing was retrieved). */
  citations?: Citation[];
};

type ServerFrame =
  | { type: "start" }
  | { type: "citations"; citations: Citation[] }
  | { type: "token"; content: string }
  | { type: "done" }
  | { type: "audio"; seq: number; format: string; text: string; data: string }
  | { type: "audio_done" }
  | { type: "voice_unavailable"; message?: string }
  | { type: "error"; code?: string; message?: string };

/**
 * Voice wiring passed in by the chat screen (from VoiceContext). Kept as an
 * options object so a voice-less caller (or a test) can omit it entirely.
 *   - `enabled`         read fresh at send-time to set the per-turn `voice` flag.
 *   - `onAudio`         each `audio` frame's WAV payload, for sequential playback.
 *   - `onVoiceUnavailable` TTS couldn't load; surface it and drop back to text.
 *   - `onTurnStart`     a new turn began — stop any audio still playing.
 */
export type VoiceWiring = {
  enabled: boolean;
  onAudio: (wavBase64: string) => void;
  onVoiceUnavailable: (message: string) => void;
  onTurnStart: () => void;
};

export type UseChat = {
  messages: Message[];
  /** True while Eva is replying — the composer disables input. */
  replying: boolean;
  /** True when the socket is open and ready to send. */
  connected: boolean;
  /** Current error banner text, or null. */
  error: string | null;
  send: (text: string) => void;
  /** Re-run the last user turn (used by the error toast's Retry). */
  retry: () => void;
  dismissError: () => void;
};

export function useChat(voice?: VoiceWiring): UseChat {
  const [messages, setMessages] = useState<Message[]>([]);
  const [replying, setReplying] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const idSeq = useRef(0);
  const streamingId = useRef<string | null>(null);
  const lastUserText = useRef<string | null>(null);
  const closedByUs = useRef(false);
  const reconnectTimer = useRef<number | null>(null);

  // Hold the voice wiring in a ref so the long-lived socket handlers always see
  // the latest toggle state + callbacks without being torn down and rebuilt.
  const voiceRef = useRef<VoiceWiring | undefined>(voice);
  voiceRef.current = voice;

  const nextId = () => `m${++idSeq.current}`;

  // Append a streamed piece to the live Eva bubble. Uses the ref + functional
  // update so it stays correct even though it's captured by the first render.
  const appendToken = (content: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === streamingId.current ? { ...m, text: m.text + content } : m,
      ),
    );
  };

  // Attach the turn's grounded sources to the live Eva bubble (arrives before
  // the first token). Targets the streaming bubble by id, like appendToken.
  const attachCitations = (citations: Citation[]) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === streamingId.current ? { ...m, citations } : m,
      ),
    );
  };

  // End the current stream. Targets the live bubble by its `streaming` flag
  // (there is only ever one) rather than an id, so it stays correct even if a
  // superseded socket fires late. On failure an empty Eva bubble (the error
  // arrived before any token, e.g. model missing) is dropped so only the toast
  // shows; a partial bubble is kept and marked failed.
  const finishStream = (failed = false) => {
    setMessages((prev) =>
      prev.flatMap((m) => {
        if (!m.streaming) return [m];
        if (failed && m.text === "") return [];
        return [{ ...m, streaming: false, failed }];
      }),
    );
    streamingId.current = null;
    setReplying(false);
  };

  const connect = useCallback(() => {
    const existing = wsRef.current;
    if (
      existing &&
      (existing.readyState === WebSocket.OPEN ||
        existing.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    closedByUs.current = false;
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    // Identity guard: under React StrictMode the connect/cleanup effect runs
    // twice, so an older socket can fire events after a newer one supersedes it.
    // Every handler ignores its socket once it is no longer the current one, so
    // we never end up with duplicate connections or reconnect loops.
    const isCurrent = () => wsRef.current === ws;

    ws.onopen = () => {
      if (!isCurrent()) return;
      setConnected(true);
      // A successful (re)connect clears a stale "reconnecting…" banner.
      setError((e) => (e && e.includes("connect") ? null : e));
    };

    ws.onmessage = (ev) => {
      if (!isCurrent()) return;
      let frame: ServerFrame;
      try {
        frame = JSON.parse(ev.data) as ServerFrame;
      } catch {
        return;
      }
      switch (frame.type) {
        case "start":
          break; // placeholder bubble already shows the typing indicator
        case "citations":
          attachCitations(frame.citations);
          break;
        case "token":
          appendToken(frame.content);
          break;
        case "audio":
          // One synthesized sentence — hand it to the audio queue to play in
          // order. The text stream is untouched, so audio lagging never holds it.
          voiceRef.current?.onAudio(frame.data);
          break;
        case "audio_done":
          break; // queue drains on its own; nothing to do on the text side
        case "voice_unavailable":
          voiceRef.current?.onVoiceUnavailable(frame.message || "");
          break;
        case "done":
          finishStream(false);
          break;
        case "error":
          finishStream(true);
          setError(frame.message || "Eva couldn't reply just now.");
          break;
      }
    };

    ws.onerror = () => {
      // Let onclose drive recovery — it always follows an error.
    };

    ws.onclose = () => {
      if (!isCurrent()) return; // a superseded socket; ignore its close
      setConnected(false);
      wsRef.current = null;
      // Dropped mid-reply (e.g. the backend was killed): mark the turn failed and
      // tell the user. Their message is already saved server-side.
      if (streamingId.current) {
        finishStream(true);
        setError("Lost connection to Eva mid-reply. Reconnecting…");
      }
      if (!closedByUs.current) {
        if (reconnectTimer.current) window.clearTimeout(reconnectTimer.current);
        reconnectTimer.current = window.setTimeout(connect, RECONNECT_MS);
      }
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      closedByUs.current = true;
      if (reconnectTimer.current) window.clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  // Push a user bubble + an empty streaming Eva bubble and send the frame.
  const dispatch = useCallback(
    (text: string, capture: boolean) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        setError("Not connected to Eva yet. Reconnecting…");
        connect();
        return;
      }
      // A new turn supersedes the previous reply: stop any audio still playing so
      // Eva doesn't talk over the next answer.
      voiceRef.current?.onTurnStart();
      const wantVoice = voiceRef.current?.enabled ?? false;

      const evaId = nextId();
      streamingId.current = evaId;
      setMessages((prev) => [
        ...prev,
        ...(capture
          ? [{ id: nextId(), role: "user" as const, text }]
          : []),
        { id: evaId, role: "eva" as const, text: "", streaming: true },
      ]);
      setReplying(true);
      setError(null);
      ws.send(
        JSON.stringify(
          capture
            ? { text, voice: wantVoice }
            : { text, capture: false, voice: wantVoice },
        ),
      );
    },
    [connect],
  );

  const send = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text || streamingId.current) return;
      lastUserText.current = text;
      dispatch(text, true);
    },
    [dispatch],
  );

  // Retry the last turn after a failure. capture=false so the backend regenerates
  // a reply without writing the user's message to the vault a second time.
  const retry = useCallback(() => {
    const text = lastUserText.current;
    if (!text || streamingId.current) return;
    setError(null);
    // Drop a prior failed Eva bubble so the retry reads cleanly.
    setMessages((prev) => prev.filter((m) => !(m.role === "eva" && m.failed)));
    dispatch(text, false);
  }, [dispatch]);

  const dismissError = useCallback(() => setError(null), []);

  return { messages, replying, connected, error, send, retry, dismissError };
}
