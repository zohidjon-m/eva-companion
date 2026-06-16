import { useRef, type PointerEvent } from "react";
import { Icon } from "../components";
import { MAX_SECONDS, useRecorder } from "./useRecorder";

/**
 * MicButton — push-to-talk for any text input. Drop it next to a field; the
 * transcript is handed back via `onTranscribed` for the user to confirm and send.
 *
 * Two gestures, both natural (plan: "hold-to-record (or click-toggle)"):
 *   • press-and-hold the button, release to transcribe;
 *   • a quick tap starts recording and leaves it running — tap (or click) again
 *     to stop.
 * Pointer capture makes release reliable even if the cursor leaves the button.
 *
 * While recording it shows a live level meter and an elapsed timer counting
 * toward the 120 s cap; the recorder auto-stops at the cap. While transcribing it
 * shows a spinner. A mic-permission denial (or any failure) surfaces a calm,
 * dismissable message — never a crash — so the user can simply keep typing.
 */

/** A press shorter than this is treated as a tap → toggle-on, not a hold. */
const TAP_MS = 350;

export function MicButton({
  onTranscribed,
  disabled = false,
}: {
  onTranscribed: (text: string) => void;
  disabled?: boolean;
}) {
  const { status, level, seconds, error, start, stop, reset } = useRecorder(onTranscribed);
  const downAt = useRef(0);

  const recording = status === "recording";
  const transcribing = status === "transcribing";

  const onPointerDown = (e: PointerEvent<HTMLButtonElement>) => {
    if (disabled || transcribing) return;
    // A click while already recording (toggle mode) stops it.
    if (recording) {
      e.preventDefault();
      stop();
      return;
    }
    if (status === "error") reset();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    downAt.current = Date.now();
    void start();
  };

  const onPointerUp = () => {
    // Only a genuine hold-release stops here; a quick tap leaves it recording
    // (toggle on) so the user can talk hands-free and click again to finish.
    if (status !== "recording") return;
    if (Date.now() - downAt.current >= TAP_MS) stop();
  };

  const label = recording
    ? "Stop recording"
    : transcribing
      ? "Transcribing…"
      : "Hold to talk, or tap to record";

  return (
    <span className={`mic mic--${status}`}>
      <button
        type="button"
        className="mic__btn"
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        disabled={disabled || transcribing}
        aria-label={label}
        aria-pressed={recording}
        title={label}
      >
        {transcribing ? (
          <span className="mic__spinner" aria-hidden="true" />
        ) : recording ? (
          <span className="mic__stop" aria-hidden="true" />
        ) : (
          <Icon name="mic" size={20} />
        )}
      </button>

      {(recording || transcribing || error) && (
        <div className="mic__panel" role="status">
          {recording && <Meter level={level} seconds={seconds} />}
          {transcribing && <span className="mic__panel-text">Transcribing…</span>}
          {error && (
            <div className="mic__error">
              <span className="mic__error-text">{error}</span>
              <button type="button" className="mic__error-dismiss" onClick={reset}>
                Dismiss
              </button>
            </div>
          )}
        </div>
      )}
    </span>
  );
}

/** Live level bars + an elapsed timer counting toward the 120 s cap. */
function Meter({ level, seconds }: { level: number; seconds: number }) {
  const remaining = MAX_SECONDS - seconds;
  const nearCap = remaining <= 10;
  // Eight bars; each lights once the level crosses its share of the range, so the
  // meter reacts to speech rather than just animating.
  const bars = Array.from({ length: 8 }, (_, i) => level >= (i + 1) / 8);
  return (
    <div className="mic__meter">
      <span className="mic__rec-dot" aria-hidden="true" />
      <span className="mic__bars" aria-hidden="true">
        {bars.map((on, i) => (
          <span key={i} className={`mic__bar${on ? " mic__bar--on" : ""}`} />
        ))}
      </span>
      <span className={`mic__time${nearCap ? " mic__time--warn" : ""}`}>
        {fmt(seconds)}
        {nearCap && <span className="mic__cap"> · {remaining}s left</span>}
      </span>
    </div>
  );
}

/** Seconds → "M:SS". */
function fmt(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
