import { useCallback, useEffect, useRef, useState } from "react";
import type { ConversationTurn } from "./api";

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
 *   ← { type:"memory", memories }   past entries Eva recalled this turn (Phase 11)
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

/**
 * One past journal entry Eva recalled for this turn (Phase 11 "Eva remembers").
 * Deliberately minimal — just the day — so the chip is a subtle "she remembered
 * this" cue, not a window back into the entry's text.
 */
export type Memory = {
  /** ISO day of the recalled entry, e.g. "2026-06-03". */
  date: string;
  /** Short human label for the chip, e.g. "Jun 3". */
  label: string;
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
  /** Past entries Eva recalled for this turn (absent when nothing was recalled). */
  memories?: Memory[];
};

type ServerFrame =
  | { type: "start" }
  | { type: "citations"; citations: Citation[] }
  | { type: "memory"; memories: Memory[] }
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
  /** The conversation currently open (null = a fresh, unsaved thread). */
  conversationId: string | null;
  send: (text: string) => void;
  /** Re-run the last user turn (used by the error toast's Retry). */
  retry: () => void;
  dismissError: () => void;
  /** Replace the thread with a stored conversation's turns and continue it. */
  openConversation: (id: string, turns: ConversationTurn[]) => void;
  /** Start a brand-new, empty thread (the next turn creates a conversation). */
  newConversation: () => void;
};

export function useChat(voice?: VoiceWiring, mode: string = "friend"): UseChat {
  const [messages, setMessages] = useState<Message[]>([]);
  const [replying, setReplying] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const idSeq = useRef(0);
  const streamingId = useRef<string | null>(null);
  const lastUserText = useRef<string | null>(null);
  const closedByUs = useRef(false);
  const reconnectTimer = useRef<number | null>(null);
  // The conversation the socket is writing to, kept in a ref so the long-lived
  // send/receive handlers read the latest value without being rebuilt.
  const conversationIdRef = useRef<string | null>(null);

  // Hold the voice wiring in a ref so the long-lived socket handlers always see
  // the latest toggle state + callbacks without being torn down and rebuilt.
  const voiceRef = useRef<VoiceWiring | undefined>(voice);
  voiceRef.current = voice;

  // The persona/mode (friend/coach/mentor), read fresh at send-time so switching
  // it applies to the very next turn without rebuilding the socket handlers.
  const modeRef = useRef(mode);
  modeRef.current = mode;

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

  // Attach the past entries Eva recalled to the live Eva bubble (arrives before
  // the first token, like citations). Drives the subtle "Remembering …" chip.
  const attachMemories = (memories: Memory[]) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === streamingId.current ? { ...m, memories } : m,
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
        case "memory":
          attachMemories(frame.memories);
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
      // Tie the turn to the open conversation (if any); the backend starts a new
      // one and echoes its id back in a "conversation" frame when this is null.
      const convId = conversationIdRef.current ?? undefined;
      const mode = modeRef.current;
      ws.send(
        JSON.stringify(
          capture
            ? { text, voice: wantVoice, conversation_id: convId, mode }
            : { text, capture: false, voice: wantVoice, conversation_id: convId, mode },
        ),
      );
    },
    [connect],
  );

  const send = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text || streamingId.current) return;
      // A fresh thread gets a client-generated conversation id on its first turn,
      // sent with every frame, so the backend can persist both sides without
      // having to echo an id back over the socket.
      if (!conversationIdRef.current) {
        const id =
          globalThis.crypto?.randomUUID?.() ?? `c-${Date.now()}-${++idSeq.current}`;
        conversationIdRef.current = id;
        setConversationId(id);
      }
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

  // Replace the thread with a stored conversation's turns and make it the active
  // one, so the next send continues it. Used by the history rail and by the
  // initial restore-most-recent on mount.
  const openConversation = useCallback(
    (id: string, turns: ConversationTurn[]) => {
      conversationIdRef.current = id;
      streamingId.current = null;
      lastUserText.current = null;
      setConversationId(id);
      setReplying(false);
      setError(null);
      setMessages(
        turns.map((t) => ({ id: nextId(), role: t.role, text: t.text })),
      );
    },
    [],
  );

  // Start a fresh, empty thread. The next captured turn creates a new server-side
  // conversation and echoes its id back.
  const newConversation = useCallback(() => {
    conversationIdRef.current = null;
    streamingId.current = null;
    lastUserText.current = null;
    setConversationId(null);
    setReplying(false);
    setError(null);
    setMessages([]);
  }, []);

  return {
    messages,
    replying,
    connected,
    error,
    conversationId,
    send,
    retry,
    dismissError,
    openConversation,
    newConversation,
  };
}
