import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { Icon } from "../components";
import { MicButton } from "../voice/MicButton";
import { appendTranscript } from "../voice/text";
import { useVoice } from "../voice/VoiceContext";
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

export function ChatScreen() {
  // Bridge the shared voice state into the chat socket: the per-turn `voice` flag
  // comes from the top-bar toggle, and Eva's synthesized audio is routed to the
  // playback queue. enqueue/stop/reportUnavailable are stable callbacks.
  const voice = useVoice();
  const { messages, replying, connected, error, send, retry, dismissError } =
    useChat({
      enabled: voice.enabled,
      onAudio: voice.enqueue,
      onVoiceUnavailable: voice.reportUnavailable,
      onTurnStart: voice.stop,
    });

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
            messages.map((m) => <Bubble key={m.id} message={m} />)
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

/** One message row — Eva left, user right. Eva shows typing dots until tokens land. */
function Bubble({ message }: { message: Message }) {
  const { role, text, streaming, failed, citations, memories } = message;
  const isEva = role === "eva";
  const waiting = isEva && streaming && text === "";

  return (
    <div className={`msg msg--${role}`}>
      <div className="msg__content">
        {isEva && memories && memories.length > 0 && (
          <Memories memories={memories} />
        )}
        <div
          className={[
            "bubble",
            `bubble--${role}`,
            failed ? "bubble--failed" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          {waiting ? (
            <TypingDots />
          ) : (
            <>
              {text}
              {isEva && streaming && <span className="bubble__cursor" />}
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
 * The "Eva remembers" cue (Phase 11) — a subtle line above her reply naming the
 * past day(s) she recalled for this turn. It sits ABOVE the bubble (citations sit
 * below) so the demo audience reads "remembering Jun 3" as Eva reaches back, just
 * before she answers. Chips are non-interactive on purpose: this is a quiet "she
 * remembered", not a doorway into the entry — re-reading lives in Journal browse.
 * Chips only ever render from memories the backend sent, each gated by relevance,
 * so a chip can never name a day Eva didn't genuinely recall.
 */
function Memories({ memories }: { memories: Memory[] }) {
  return (
    <div className="remember">
      <Icon name="sparkle" size={13} className="remember__mark" />
      <span className="remember__label">Remembering</span>
      <span className="remember__chips">
        {memories.map((m) => (
          <span key={m.date} className="remember__chip">
            {m.label}
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
