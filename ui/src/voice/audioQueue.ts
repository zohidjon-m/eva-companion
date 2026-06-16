/**
 * AudioQueue — sequential playback of the WAV chunks Eva streams back (Phase 9).
 *
 * The backend synthesizes Eva's reply one sentence at a time and sends each as a
 * base64 WAV frame over the chat socket, in order. This queue plays them strictly
 * one after another — never overlapping — so speech sounds continuous even though
 * the chunks arrive a sentence at a time while later text is still streaming.
 *
 * It deliberately holds no React state: it's a plain class the VoiceContext owns
 * via a ref, and it reports playing/idle transitions through one callback so the
 * UI (the top-bar "speaking" indicator + stop button) can reflect them. `stop()`
 * is the user's "stop speaking": it halts the current chunk and drops the rest.
 */

/** Decode a base64 WAV payload into a playable object URL (revoked after use). */
function base64WavToUrl(base64: string): string {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
}

export class AudioQueue {
  private urls: string[] = [];
  private current: HTMLAudioElement | null = null;
  private playing = false;

  /** @param onSpeakingChange called with `true` when playback starts, `false` when it drains/stops. */
  constructor(private readonly onSpeakingChange: (speaking: boolean) => void) {}

  /** Add one synthesized WAV chunk; starts playback if nothing is playing. */
  enqueue(wavBase64: string): void {
    this.urls.push(base64WavToUrl(wavBase64));
    if (!this.playing) this.playNext();
  }

  /** Stop speaking immediately and discard anything queued (user "stop", or voice off). */
  stop(): void {
    if (this.current) {
      this.current.onended = null;
      this.current.onerror = null;
      this.current.pause();
      this.current = null;
    }
    this.urls.forEach(URL.revokeObjectURL);
    this.urls = [];
    if (this.playing) {
      this.playing = false;
      this.onSpeakingChange(false);
    }
  }

  private playNext(): void {
    const url = this.urls.shift();
    if (!url) {
      // Queue drained — Eva has finished speaking everything received so far.
      this.playing = false;
      this.onSpeakingChange(false);
      return;
    }
    if (!this.playing) {
      this.playing = true;
      this.onSpeakingChange(true);
    }
    const audio = new Audio(url);
    this.current = audio;
    const advance = () => {
      URL.revokeObjectURL(url);
      // Only advance if this element is still the active one (stop() nulls it).
      if (this.current === audio) {
        this.current = null;
        this.playNext();
      }
    };
    audio.onended = advance;
    audio.onerror = advance; // a bad chunk shouldn't wedge the queue
    void audio.play().catch(advance); // autoplay block → skip rather than stall
  }
}
