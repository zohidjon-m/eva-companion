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
 * The backend protocol (Phase 1/4):
 *   → send `{ text }`            normal turn (backend captures it to the vault)
 *   → send `{ text, capture:false }`  retry of an already-saved turn
 *   ← { type:"start" }           Eva is about to speak
 *   ← { type:"token", content }  one streamed piece
 *   ← { type:"done" }            reply complete
 *   ← { type:"error", message }  graceful failure (model missing/error)
 */

const WS_URL = "ws://127.0.0.1:8000/chat";
const RECONNECT_MS = 1500;

export type ChatRole = "user" | "eva";

export type Message = {
  id: string;
  role: ChatRole;
  text: string;
  /** Eva bubble still streaming (awaiting or receiving tokens). */
  streaming?: boolean;
  /** This turn failed (model error or the socket dropped mid-reply). */
  failed?: boolean;
};

type ServerFrame =
  | { type: "start" }
  | { type: "token"; content: string }
  | { type: "done" }
  | { type: "error"; code?: string; message?: string };

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

export function useChat(): UseChat {
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
        case "token":
          appendToken(frame.content);
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
      ws.send(JSON.stringify(capture ? { text } : { text, capture: false }));
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
