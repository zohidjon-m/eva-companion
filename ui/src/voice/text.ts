/**
 * Append a transcript to whatever the user has already typed/dictated, inserting
 * a single separating space only when one is needed. Shared by the chat composer
 * and the journal editor so dictation reads naturally in both: empty field → the
 * transcript as-is; mid-sentence → a space then the transcript; right after a
 * newline or existing space → no doubled whitespace.
 */
export function appendTranscript(existing: string, transcript: string): string {
  const add = transcript.trim();
  if (!add) return existing;
  if (!existing) return add;
  const needsSpace = !/\s$/.test(existing);
  return existing + (needsSpace ? " " : "") + add;
}
