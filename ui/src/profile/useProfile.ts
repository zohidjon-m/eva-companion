import { useCallback, useEffect, useState } from "react";
import { fetchProfile, saveProfile } from "./api";

/**
 * useProfile — load the profile rendering and apply edits, kept here so the
 * Profile screen stays presentational.
 *
 * The screen has two modes: reading the rendered profile.md, and editing it as
 * raw Markdown. Saving sends the edited text to PUT /profile, which runs the
 * lenient §7.2 sync and returns the canonical re-rendering plus any warnings
 * about sections it couldn't apply — so after a save the editor always reflects
 * exactly what was stored, and the user is told about anything left unchanged.
 */

export type UseProfile = {
  loading: boolean;
  error: string | null;
  /** Whether a profile exists at all (false → the "still getting to know you" state). */
  present: boolean;
  /** The rendered profile.md (view mode). */
  markdown: string;
  /** Whether the editor is open. */
  editing: boolean;
  /** The editable draft Markdown. */
  draft: string;
  setDraft: (md: string) => void;
  saving: boolean;
  /** Warnings from the last save (sections left unchanged); cleared on edit. */
  warnings: string[];
  /** Increments on each successful save (drives the brief "Saved" note). */
  savedTick: number;
  startEdit: () => void;
  cancelEdit: () => void;
  save: () => void;
};

export function useProfile(): UseProfile {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [present, setPresent] = useState(false);
  const [markdown, setMarkdown] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [savedTick, setSavedTick] = useState(0);

  useEffect(() => {
    let alive = true;
    fetchProfile()
      .then((r) => {
        if (!alive) return;
        setPresent(r.present);
        setMarkdown(r.markdown ?? "");
      })
      .catch(() => alive && setError("Couldn't load your profile. Is the backend running?"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const startEdit = useCallback(() => {
    setDraft(markdown);
    setWarnings([]);
    setError(null);
    setEditing(true);
  }, [markdown]);

  const cancelEdit = useCallback(() => {
    setEditing(false);
    setWarnings([]);
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const r = await saveProfile(draft);
      setMarkdown(r.markdown ?? "");
      setWarnings(r.warnings);
      setSavedTick((t) => t + 1);
      // Stay in view mode after a clean save; if there were warnings, the screen
      // shows them so the user can decide whether to edit again.
      setEditing(false);
    } catch {
      setError("Couldn't save your changes. Please try again.");
    } finally {
      setSaving(false);
    }
  }, [draft]);

  return {
    loading,
    error,
    present,
    markdown,
    editing,
    draft,
    setDraft,
    saving,
    warnings,
    savedTick,
    startEdit,
    cancelEdit,
    save,
  };
}
