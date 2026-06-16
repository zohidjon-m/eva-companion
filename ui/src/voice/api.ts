/**
 * Voice API — the one network call behind push-to-talk: POST /stt.
 *
 * The recorder hands us the recorded audio Blob; we post it as multipart to the
 * backend, which lazy-loads faster-whisper and returns the transcript. The text
 * then lands in the input box for the user to confirm before it is sent through
 * the normal chat/journal pipeline — so a spoken turn is captured and grounded
 * exactly like a typed one. Kept dependency-free (the app ships fully offline).
 */

const BASE = "http://127.0.0.1:8000";

export type Transcript = {
  text: string;
  duration: number;
  model_size: string;
};

/** A transcription failure carrying the backend's user-facing message + status. */
export class STTError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "STTError";
    this.status = status;
  }
}

/**
 * Transcribe a recorded clip. Resolves to the transcript, or throws `STTError`
 * with the backend's message (model not set up → 503, clip too long → 413, …) so
 * the mic button can show a helpful line rather than failing silently.
 */
export async function transcribe(audio: Blob): Promise<Transcript> {
  const form = new FormData();
  // A filename with an extension helps the server's decoder pick a demuxer; the
  // real container is detected from the bytes regardless.
  const ext = audio.type.includes("ogg") ? "ogg" : audio.type.includes("mp4") ? "mp4" : "webm";
  form.append("file", audio, `recording.${ext}`);

  let resp: Response;
  try {
    resp = await fetch(`${BASE}/stt`, { method: "POST", body: form });
  } catch {
    throw new STTError("Couldn't reach Eva to transcribe. Is the backend running?", 0);
  }
  if (!resp.ok) {
    let detail = `Transcription failed (${resp.status}).`;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body — keep the generic message */
    }
    throw new STTError(detail, resp.status);
  }
  return (await resp.json()) as Transcript;
}
