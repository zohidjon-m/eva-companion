import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Icon } from "../components";
import { usePersona } from "../layout/PersonaContext";
import { useShell } from "../layout/ShellContext";
import { MicButton } from "../voice/MicButton";
import { appendTranscript } from "../voice/text";
import { useVoice } from "../voice/VoiceContext";
import {
  deleteConversation,
  fetchConversation,
  fetchConversations,
  type ConversationSummary,
} from "./api";
import { useChat, type Citation, type Memory, type Message } from "./useChat";

/**
 * ChatScreen — the Phase 4 chat surface, wired to `WS /chat` via useChat.
 *
 * The frame is a full-height flex column: a scrollable thread on top and a
 * pinned composer at the bottom. Eva's bubbles stream token-by-token with a
 * typing indicator; the thread auto-scrolls while the user is at the bottom but
 * leaves them alone if they've scrolled up to re-read. A dropped socket or model
 * error raises a toast with a Retry. Enter sends, Shift+Enter makes a newline,
 * and the input is disabled while Eva is replying.
 */

// The history rail is user-resizable so long conversation titles can be read in
// full. Width is clamped and remembered between visits.
const RAIL_MIN = 200;
const RAIL_MAX = 460;
const RAIL_DEFAULT = 248;
const RAIL_KEY = "eva.chatRailWidth";

function loadRailWidth(): number {
  const v = Number(localStorage.getItem(RAIL_KEY));
  return Number.isFinite(v) && v >= RAIL_MIN && v <= RAIL_MAX ? v : RAIL_DEFAULT;
}

export function ChatScreen() {
  // Whether the past-conversations rail is shown — toggled by pressing "Chat"
  // again in the sidebar (lifted to the shell, read here via context).
  const { chatRailOpen } = useShell();
  // The chosen persona (friend/coach/mentor) — sent as `mode` on every turn.
  const { persona } = usePersona();

  // Bridge the shared voice state into the chat socket: the per-turn `voice` flag
  // comes from the top-bar toggle, and Eva's synthesized audio is routed to the
  // playback queue. enqueue/stop/reportUnavailable are stable callbacks.
  const voice = useVoice();
  const {
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
  } = useChat(
    {
      enabled: voice.enabled,
      onAudio: voice.enqueue,
      onVoiceUnavailable: voice.reportUnavailable,
      onTurnStart: voice.stop,
    },
    persona,
  );

  // The history rail: the list of past conversations, kept in sync with the
  // backend. Loaded on mount and refreshed whenever the thread goes idle (a turn
  // finished) or the active conversation changes.
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const didRestore = useRef(false);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await fetchConversations());
    } catch {
      // Best-effort: a backend hiccup leaves the prior list rather than blanking.
    }
  }, []);

  // Restore the most recent conversation once on mount, so a reload or a tab
  // switch back to Chat brings the last thread back instead of an empty screen.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await fetchConversations();
        if (cancelled) return;
        setConversations(list);
        if (!didRestore.current && list.length > 0) {
          didRestore.current = true;
          const convo = await fetchConversation(list[0].id);
          if (!cancelled && convo) openConversation(convo.id, convo.turns);
        }
      } catch {
        /* offline: start with an empty thread */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [openConversation]);

  // Keep the rail fresh as turns complete (new conversation appears, ordering
  // updates) — only when idle so we don't refetch mid-stream.
  useEffect(() => {
    if (!replying) void refreshConversations();
  }, [replying, conversationId, refreshConversations]);

  const onOpen = useCallback(
    async (id: string) => {
      if (id === conversationId) return;
      const convo = await fetchConversation(id);
      if (convo) openConversation(convo.id, convo.turns);
    },
    [conversationId, openConversation],
  );

  const onDelete = useCallback(
    async (id: string) => {
      await deleteConversation(id);
      if (id === conversationId) newConversation();
      void refreshConversations();
    },
    [conversationId, newConversation, refreshConversations],
  );

  // Rail resize: drag the divider to set the rail width. We measure from the
  // layout's left edge so the width tracks the cursor exactly, and suppress the
  // width transition while dragging so it follows the pointer without lag.
  const layoutRef = useRef<HTMLDivElement>(null);
  const [railWidth, setRailWidth] = useState(loadRailWidth);
  const [resizing, setResizing] = useState(false);

  useEffect(() => {
    localStorage.setItem(RAIL_KEY, String(railWidth));
  }, [railWidth]);

  const startResize = useCallback((e: ReactPointerEvent) => {
    e.preventDefault();
    const left = layoutRef.current?.getBoundingClientRect().left ?? 0;
    setResizing(true);
    const onMove = (ev: PointerEvent) => {
      const w = Math.min(RAIL_MAX, Math.max(RAIL_MIN, ev.clientX - left));
      setRailWidth(w);
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, []);

  const scrollRef = useRef<HTMLDivElement>(null);
  // Whether the view is pinned to the bottom (so streaming keeps it there).
  const pinned = useRef(true);

  const updatePinned = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    pinned.current = distance < 80;
  };

  // Keep the latest content in view as tokens stream in — but only if the user
  // hasn't scrolled up. useLayoutEffect avoids a visible jump.
  useLayoutEffect(() => {
    if (!pinned.current) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  return (
    <div
      className={`chat-layout${resizing ? " chat-layout--resizing" : ""}`}
      ref={layoutRef}
    >
      <ConversationRail
        conversations={conversations}
        activeId={conversationId}
        onNew={newConversation}
        onOpen={onOpen}
        onDelete={onDelete}
        open={chatRailOpen}
        width={railWidth}
      />
      {chatRailOpen && (
        <div
          className="chat-rail-resizer"
          onPointerDown={startResize}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize conversation list"
        />
      )}
      <div className="chat">
        <div
          className="chat__scroll"
          ref={scrollRef}
          onScroll={updatePinned}
        >
          <div className="chat__thread">
            {messages.length === 0 ? (
              <ChatGreeting />
            ) : (
              messages.map((m) => <Turn key={m.id} message={m} />)
            )}
          </div>
        </div>

        {error && (
          <Toast
            message={error}
            onRetry={retry}
            onDismiss={dismissError}
          />
        )}

        <Composer onSend={send} disabled={replying} connected={connected} />
      </div>
    </div>
  );
}

/**
 * ConversationRail — the chat history sidebar. Lists past conversations newest
 * first, highlights the open one, and offers a "New chat" affordance. Clicking a
 * row reopens that conversation (both sides); the small ✕ deletes it. The rail
 * scrolls independently of the thread, so a long history never moves the chat.
 *
 * It can be tucked away (pressing "Chat" again in the sidebar) — it animates to
 * zero width rather than vanishing — and dragged wider via the divider so long
 * titles can be read in full; `width` is the user's chosen size.
 */
function ConversationRail({
  conversations,
  activeId,
  onNew,
  onOpen,
  onDelete,
  open,
  width,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  onNew: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  open: boolean;
  width: number;
}) {
  return (
    <aside
      className={`convo-rail${open ? "" : " convo-rail--closed"}`}
      style={{ width: open ? width : 0 }}
      aria-label="Conversation history"
      aria-hidden={!open}
    >
      <button className="convo-rail__new" onClick={onNew}>
        <Icon name="feather" size={16} />
        New chat
      </button>
      <div className="convo-rail__list">
        {conversations.length === 0 ? (
          <p className="convo-rail__empty">Your conversations will appear here.</p>
        ) : (
          conversations.map((c) => (
            <div
              key={c.id}
              className={`convo-item${c.id === activeId ? " convo-item--active" : ""}`}
            >
              <button
                className="convo-item__open"
                onClick={() => onOpen(c.id)}
                title={c.title ?? "Conversation"}
              >
                <span className="convo-item__title">
                  {c.title?.trim() || "New conversation"}
                </span>
              </button>
              <button
                className="convo-item__del"
                onClick={() => onDelete(c.id)}
                aria-label="Delete conversation"
                title="Delete"
              >
                <Icon name="trash" size={14} />
              </button>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}

/** The empty-thread greeting — Eva's voice, no dead "start" button. */
function ChatGreeting() {
  return (
    <div className="chat__greeting">
      <span className="chat__greeting-mark" aria-hidden="true">
        <Icon name="chat" size={26} />
      </span>
      <p className="chat__greeting-title">I'm here. What's on your mind?</p>
      <p className="chat__greeting-sub">
        Talk to me like you would a friend. Everything you say stays on this
        device.
      </p>
    </div>
  );
}

/**
 * One conversation turn, in the modern assistant-chat layout (Claude/ChatGPT/
 * Gemini style): Eva's replies are full-width plain text with a small avatar and
 * name; the user's turns are a compact, right-aligned soft block. Eva shows the
 * typing dots until her first token lands, then a blinking caret while streaming.
 */
function Turn({ message }: { message: Message }) {
  const { role, text, streaming, failed, citations, memories } = message;
  const isEva = role === "eva";
  const waiting = isEva && streaming && text === "";

  return (
    <div className={`turn turn--${role}`}>
      {isEva && (
        <div className="turn__avatar" aria-hidden="true">
          <Icon name="sparkle" size={16} />
        </div>
      )}
      <div className="turn__col">
        {isEva && <span className="turn__name">Eva</span>}
        {isEva && memories && memories.length > 0 && <Memories memories={memories} />}
        <div
          className={["turn__body", failed ? "turn__body--failed" : ""]
            .filter(Boolean)
            .join(" ")}
        >
          {waiting ? (
            <TypingDots />
          ) : (
            <>
              {text}
              {isEva && streaming && <span className="turn__cursor" />}
            </>
          )}
        </div>
        {isEva && citations && citations.length > 0 && (
          <Citations citations={citations} />
        )}
      </div>
    </div>
  );
}

/**
 * The "Eva remembers" cue (Phase 11) — a subtle line above her reply that signals
 * she reached back into past entries for this turn. It sits ABOVE the bubble
 * (citations sit below) so the reader sees "Remembering" as Eva reaches back, just
 * before she answers. It only ever renders when the backend actually sent recalled
 * memories for the turn, so the cue can never claim a recall that didn't happen.
 *
 * R9 renders date labels from the memory frame so recall is visible without
 * turning the chat flow into an entry browser.
 */
function Memories({ memories }: { memories: Memory[] }) {
  return (
    <div className="remember">
      <Icon name="sparkle" size={13} className="remember__mark" />
      <span className="remember__label">Remembering</span>
      <span className="remember__chips">
        {memories.map((memory) => (
          <span key={`${memory.date}-${memory.label}`} className="remember__chip">
            {memory.label}
          </span>
        ))}
      </span>
    </div>
  );
}

/**
 * Source chips under an Eva reply — the visible proof that the answer was
 * grounded in the user's own library. Each chip names a source (file + page or
 * section); clicking one opens the exact passage Eva was given, so the user can
 * check the citation themselves. Chips only ever render from citations the
 * backend sent, so Eva can never show a source she didn't actually retrieve.
 */
function Citations({ citations }: { citations: Citation[] }) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  return (
    <div className="citations">
      <span className="citations__label">
        <Icon name="library" size={13} />
        From your library
      </span>
      <div className="citations__chips">
        {citations.map((c, i) => (
          <button
            key={i}
            type="button"
            className={`cite-chip${openIndex === i ? " cite-chip--open" : ""}`}
            onClick={() => setOpenIndex(openIndex === i ? null : i)}
            aria-expanded={openIndex === i}
            title="Show the passage"
          >
            {c.label}
          </button>
        ))}
      </div>
      {openIndex !== null && (
        <blockquote className="citations__passage">
          {citations[openIndex].text}
        </blockquote>
      )}
    </div>
  );
}

/** The three-dot "Eva is typing" indicator shown before the first token. */
function TypingDots() {
  return (
    <span className="typing" aria-label="Eva is typing">
      <span className="typing__dot" />
      <span className="typing__dot" />
      <span className="typing__dot" />
    </span>
  );
}

/** Transient error banner with a Retry affordance. */
function Toast({
  message,
  onRetry,
  onDismiss,
}: {
  message: string;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="chat__toast" role="alert">
      <span className="chat__toast-text">{message}</span>
      <div className="chat__toast-actions">
        <button className="chat__toast-btn" onClick={onRetry}>
          Retry
        </button>
        <button
          className="chat__toast-btn chat__toast-btn--ghost"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

/** The composer: an auto-growing textarea + send button. */
function Composer({
  onSend,
  disabled,
  connected,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
  connected: boolean;
}) {
  const [value, setValue] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Grow the textarea with its content, up to a cap (then it scrolls).
  const resize = () => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };
  useEffect(resize, [value]);

  // Re-focus the input when Eva finishes replying, so the user can keep typing.
  useEffect(() => {
    if (!disabled) taRef.current?.focus();
  }, [disabled]);

  const submit = () => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue("");
  };

  // A finished transcription lands in the box for the user to confirm and edit —
  // it is never auto-sent, so a spoken turn goes through the exact same review +
  // submit path as a typed one. Appends to whatever is already there.
  const onTranscribed = (text: string) => {
    setValue((v) => appendTranscript(v, text));
    taRef.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer">
      <div className="composer__inner">
        <textarea
          ref={taRef}
          className="composer__field"
          placeholder={disabled ? "Eva is replying…" : "Message Eva…"}
          rows={1}
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          aria-label="Message Eva"
        />
        <MicButton onTranscribed={onTranscribed} disabled={disabled} />
        <button
          className="composer__send"
          onClick={submit}
          disabled={disabled || !value.trim()}
          aria-label="Send message"
          title={connected ? "Send" : "Reconnecting…"}
        >
          <Icon name="send" size={20} />
        </button>
      </div>
      <p className="composer__hint">
        Enter to send · Shift+Enter for a new line
      </p>
    </div>
  );
}
