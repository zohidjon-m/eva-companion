/**
 * Shared mood vocabulary — the one place a numeric mood (−5…5, or a float
 * average) becomes a plain word. Used by both the mood chart's axis/tooltip and
 * the "Looking back" report so they never describe the same score differently.
 */

export type MoodWord = "Great" | "Good" | "Okay" | "Low" | "Rough";

/** Map any mood value (integer score or a float average) to a plain word. */
export function moodWord(m: number): MoodWord {
  if (m >= 2.5) return "Great";
  if (m >= 0.5) return "Good";
  if (m > -0.5) return "Okay";
  if (m >= -2.5) return "Low";
  return "Rough";
}
