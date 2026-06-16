import { useCallback, useEffect, useRef, useState } from "react";
import { STTError, transcribe } from "./api";

/**
 * useRecorder — push-to-talk audio capture + transcription, in one hook.
 *
 * The lifecycle is a small state machine:
 *   idle → (start) → recording → (stop) → transcribing → idle
 *                         └──────────────→ error  (mic denied / STT failed)
 *
 * While recording it exposes a live `level` (0..1) for the on-screen meter and an
 * elapsed `seconds` counter, and it auto-stops at the 120 s cap (EVA_SYSTEM_DESIGN
 * §9) so a forgotten recording can never run away. On release it posts the clip to
 * `POST /stt` and calls `onResult(text)` — the screen drops that text into its
 * input box for the user to confirm and send through the normal pipeline; the hook
 * deliberately never sends anything itself.
 *
 * Everything is torn down on stop/unmount (recorder, analyser, and crucially the
 * media stream tracks) so the OS microphone indicator goes dark the moment we're
 * done listening — important for a privacy-first app.
 */

/** Hard cap on a single recording, mirroring the backend (§9: ≤ 120 s). */
export const MAX_SECONDS = 120;

export type RecorderStatus = "idle" | "recording" | "transcribing" | "error";

export type UseRecorder = {
  status: RecorderStatus;
  /** Live input level 0..1 while recording (drives the meter). */
  level: number;
  /** Whole seconds elapsed in the current recording. */
  seconds: number;
  /** A user-facing message when status === "error" (mic denied, STT failed…). */
  error: string | null;
  start: () => void;
  /** Stop and transcribe (the normal "release" path). */
  stop: () => void;
  /** Abandon a recording without transcribing (e.g. user cancels). */
  cancel: () => void;
  /** Clear an error back to idle. */
  reset: () => void;
};

/** Pick a MediaRecorder mime type the current webview actually supports. */
function pickMimeType(): string | undefined {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4", // Safari / WKWebView (the Tauri webview on macOS)
  ];
  if (typeof MediaRecorder === "undefined") return undefined;
  return candidates.find((t) => MediaRecorder.isTypeSupported?.(t));
}

/** Turn a getUserMedia rejection into a calm, actionable message. */
function micErrorMessage(err: unknown): string {
  const name = (err as { name?: string })?.name;
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone access was denied. Enable it in your system settings, or just type instead.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No microphone was found. You can type your message instead.";
  }
  return "Couldn't start the microphone. You can type your message instead.";
}

export function useRecorder(onResult: (text: string) => void): UseRecorder {
  const [status, setStatus] = useState<RecorderStatus>("idle");
  const [level, setLevel] = useState(0);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const capTimerRef = useRef<number | null>(null);
  const tickRef = useRef<number | null>(null);
  // Set when the user cancels, so the recorder's onstop skips transcription.
  const cancelledRef = useRef(false);
  // Latest onResult, kept in a ref so onstop (bound once) always calls the
  // current callback without re-creating the recorder.
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  // Release the mic and every audio resource. Safe to call more than once.
  const teardown = useCallback(() => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    if (capTimerRef.current != null) window.clearTimeout(capTimerRef.current);
    capTimerRef.current = null;
    if (tickRef.current != null) window.clearInterval(tickRef.current);
    tickRef.current = null;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setLevel(0);
  }, []);

  // Drive the level meter from the analyser's time-domain RMS.
  const meter = useCallback((analyser: AnalyserNode) => {
    const buf = new Uint8Array(analyser.fftSize);
    const loop = () => {
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128; // center at 0, range -1..1
        sum += v * v;
      }
      const rms = Math.sqrt(sum / buf.length);
      // Light scaling so normal speech fills the meter without clipping.
      setLevel(Math.min(1, rms * 2.2));
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
  }, []);

  const start = useCallback(async () => {
    if (status === "recording" || status === "transcribing") return;
    setError(null);
    cancelledRef.current = false;
    chunksRef.current = [];

    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("error");
      setError("This device can't capture audio. You can type your message instead.");
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      // Permission denied / no device → a helpful message, never a crash.
      setStatus("error");
      setError(micErrorMessage(err));
      return;
    }
    streamRef.current = stream;

    // Level meter (best-effort: if the AudioContext can't start we still record).
    try {
      const ctx = new (window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      meter(analyser);
    } catch {
      /* meter is cosmetic; recording proceeds without it */
    }

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorderRef.current = recorder;
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = async () => {
      const wasCancelled = cancelledRef.current;
      const blob = new Blob(chunksRef.current, { type: mimeType || "audio/webm" });
      teardown();
      if (wasCancelled || blob.size === 0) {
        setStatus("idle");
        return;
      }
      setStatus("transcribing");
      try {
        const { text } = await transcribe(blob);
        setStatus("idle");
        const trimmed = text.trim();
        if (trimmed) onResultRef.current(trimmed);
        // An empty transcript (silence) just returns to idle — nothing to confirm.
      } catch (err) {
        setStatus("error");
        setError(err instanceof STTError ? err.message : "Couldn't transcribe that. Try again, or type instead.");
      }
    };

    recorder.start();
    setStatus("recording");
    setSeconds(0);

    // Elapsed-time counter + the hard 120 s cap (auto-stops the recording).
    const startedAt = Date.now();
    tickRef.current = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 250);
    capTimerRef.current = window.setTimeout(() => {
      if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    }, MAX_SECONDS * 1000);
  }, [status, meter, teardown]);

  const stop = useCallback(() => {
    const r = recorderRef.current;
    if (r && r.state === "recording") r.stop(); // → onstop transcribes
  }, []);

  const cancel = useCallback(() => {
    cancelledRef.current = true;
    const r = recorderRef.current;
    if (r && r.state === "recording") r.stop();
    else {
      teardown();
      setStatus("idle");
    }
  }, [teardown]);

  const reset = useCallback(() => {
    setError(null);
    setStatus("idle");
  }, []);

  // Release the mic if the component unmounts mid-recording.
  useEffect(() => () => teardown(), [teardown]);

  return { status, level, seconds, error, start, stop, cancel, reset };
}
