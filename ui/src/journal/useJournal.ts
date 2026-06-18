import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import {
  fetchAck,
  fetchEntries,
  fetchEntry,
  saveJournal,
  toDisplayMarkdown,
  toStorageMarkdown,
  updateJournal,
  type JournalEntry,
  type JournalEntryFull,
} from "./api";

/**
 * useJournal — all the state behind the journaling surface, kept here so the
 * screen stays presentational.
 *
 * Journaling is a stream of discrete posts, not one continuous daily page: every
 * Save creates its own entry, and the surface opens on a flat history of those
 * posts (newest first) with an explicit "New entry" action. This hook owns:
 *   - the compose draft, autosaved to localStorage every ~10s so a closed app
 *     never loses an unfinished entry
 *   - the explicit Save → capture → ask-Eva flow
 *   - the flat history list of past posts and one opened post's full text
 *   - which view is showing (history index / compose / a single entry) and the
 *     remembered grid-vs-list layout for the index
 */

const DRAFT_KEY = "eva.journal.draft";
const LAYOUT_KEY = "eva.journal.layout";
const AUTOSAVE_MS = 10_000;

/** Local calendar date as YYYY-MM-DD (matches how the backend stamps days). */
function localToday(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** The post-save acknowledgment lifecycle. */
export type AckState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "line"; text: string } // Eva offered a reflection
  | { kind: "saved" }; // saved, but no line (model offline / failed)

/** How the history index lays out posts. */
export type JournalLayout = "grid" | "list";

/** Which pane the surface is showing. */
export type JournalView =
  | { kind: "index" } // the flat history of posts (the landing view)
  | { kind: "compose" } // writing a new post
  | { kind: "entry"; id: string }; // reading one past post

export type UseJournal = {
  today: string;
  draft: string;
  /** The raw state setter — accepts a string or an updater, so callers (e.g. the
   *  mic button appending a transcript) can build on the latest draft safely. */
  setDraft: Dispatch<SetStateAction<string>>;
  /** Epoch ms the draft was last written to disk, or null if nothing pending. */
  draftSavedAt: number | null;
  saving: boolean;
  saveError: string | null;
  ack: AckState;
  /** The flat history of individual journal posts, newest first. */
  entries: JournalEntry[];
  loadingEntries: boolean;
  view: JournalView;
  /** The full text of the currently-open post (null while loading / not found). */
  entryDetail: JournalEntryFull | null;
  entryLoading: boolean;
  layout: JournalLayout;
  setLayout: (layout: JournalLayout) => void;
  save: () => void;
  openIndex: () => void;
  openCompose: () => void;
  openEntry: (id: string) => void;
  /** --- Editing an existing post --- */
  editing: boolean;
  editDraft: string;
  setEditDraft: Dispatch<SetStateAction<string>>;
  savingEdit: boolean;
  editError: string | null;
  startEdit: () => void;
  cancelEdit: () => void;
  saveEdit: () => void;
};

export function useJournal(): UseJournal {
  const today = localToday();

  const [draft, setDraft] = useState<string>(
    () => localStorage.getItem(DRAFT_KEY) ?? "",
  );
  const [draftSavedAt, setDraftSavedAt] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [ack, setAck] = useState<AckState>({ kind: "idle" });

  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [loadingEntries, setLoadingEntries] = useState(true);

  const [view, setView] = useState<JournalView>({ kind: "index" });
  const [entryDetail, setEntryDetail] = useState<JournalEntryFull | null>(null);
  const [entryLoading, setEntryLoading] = useState(false);

  // Editing an already-saved post. `editing` flips the entry view's reader into
  // an editable editor seeded with the post's current text (in display form).
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [layout, setLayoutState] = useState<JournalLayout>(() =>
    localStorage.getItem(LAYOUT_KEY) === "list" ? "list" : "grid",
  );
  const setLayout = useCallback((next: JournalLayout) => {
    setLayoutState(next);
    localStorage.setItem(LAYOUT_KEY, next);
  }, []);

  // The latest draft, readable from interval/unmount callbacks without making
  // them depend on (and re-bind to) every keystroke.
  const draftRef = useRef(draft);
  draftRef.current = draft;

  // Pull the flat history of posts. Best-effort: if the backend isn't up yet,
  // leave what we have rather than blanking the screen.
  const refresh = useCallback(async () => {
    try {
      setEntries(await fetchEntries());
    } catch {
      /* offline — keep prior list */
    } finally {
      setLoadingEntries(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Autosave the draft to localStorage on an interval, and once more on unmount /
  // tab close, so reopening the app restores an unfinished entry.
  useEffect(() => {
    const persist = () => {
      const text = draftRef.current;
      if (text && text.trim()) {
        localStorage.setItem(DRAFT_KEY, text);
        setDraftSavedAt(Date.now());
      } else {
        localStorage.removeItem(DRAFT_KEY);
        setDraftSavedAt(null);
      }
    };
    const id = window.setInterval(persist, AUTOSAVE_MS);
    window.addEventListener("beforeunload", persist);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("beforeunload", persist);
      persist();
    };
  }, []);

  // Load one post's full text for the read-only entry view.
  const loadEntry = useCallback(async (id: string) => {
    setEntryLoading(true);
    setEntryDetail(null);
    try {
      setEntryDetail(await fetchEntry(id));
    } catch {
      setEntryDetail(null);
    } finally {
      setEntryLoading(false);
    }
  }, []);

  const save = useCallback(async () => {
    const text = draftRef.current.trim();
    if (!text || saving) return;
    setSaving(true);
    setSaveError(null);
    setAck({ kind: "idle" });
    try {
      // The editor works in display form (absolute image URLs); the L0 file
      // stores vault-relative paths, so the Markdown stays portable.
      const { id } = await saveJournal(toStorageMarkdown(text));
      // Durable now: clear the editor + draft, refresh the history, and open the
      // post we just wrote so Eva's reflection lands on it.
      setDraft("");
      localStorage.removeItem(DRAFT_KEY);
      setDraftSavedAt(null);
      await refresh();
      setView({ kind: "entry", id });
      loadEntry(id);
      // Then ask Eva for her one gentle line (non-blocking on the save).
      setAck({ kind: "loading" });
      try {
        const line = await fetchAck(id);
        setAck(line ? { kind: "line", text: line } : { kind: "saved" });
      } catch {
        setAck({ kind: "saved" });
      }
    } catch {
      // The draft is untouched on failure, so nothing is lost — let them retry.
      setSaveError("Couldn't save your entry just now. It's still here — try again.");
    } finally {
      setSaving(false);
    }
  }, [saving, refresh, loadEntry]);

  const openIndex = useCallback(() => {
    setAck({ kind: "idle" });
    setEditing(false);
    setView({ kind: "index" });
  }, []);

  const openCompose = useCallback(() => {
    setAck({ kind: "idle" });
    setSaveError(null);
    setEditing(false);
    setView({ kind: "compose" });
  }, []);

  const openEntry = useCallback(
    (id: string) => {
      setAck({ kind: "idle" });
      setEditing(false);
      setView({ kind: "entry", id });
      loadEntry(id);
    },
    [loadEntry],
  );

  // Enter edit mode on the open post: seed the editor with its current text in
  // display form (absolute image URLs), so editing reuses the same editor as
  // compose.
  const startEdit = useCallback(() => {
    if (!entryDetail) return;
    setEditError(null);
    setEditDraft(toDisplayMarkdown(entryDetail.text));
    setEditing(true);
  }, [entryDetail]);

  const cancelEdit = useCallback(() => {
    setEditError(null);
    setEditing(false);
  }, []);

  const saveEdit = useCallback(async () => {
    const text = editDraft.trim();
    if (!entryDetail || !text || savingEdit) return;
    setSavingEdit(true);
    setEditError(null);
    try {
      // Same display→storage shrink as a new save, so edited image URLs stay
      // vault-relative in the L0 source of truth.
      const updated = await updateJournal(entryDetail.id, toStorageMarkdown(text));
      setEntryDetail(updated);
      setEditing(false);
      await refresh(); // word counts / previews in the index reflect the edit
    } catch {
      setEditError("Couldn't save your changes just now. They're still here — try again.");
    } finally {
      setSavingEdit(false);
    }
  }, [editDraft, entryDetail, savingEdit, refresh]);

  return {
    today,
    draft,
    setDraft,
    draftSavedAt,
    saving,
    saveError,
    ack,
    entries,
    loadingEntries,
    view,
    entryDetail,
    entryLoading,
    layout,
    setLayout,
    save,
    openIndex,
    openCompose,
    openEntry,
    editing,
    editDraft,
    setEditDraft,
    savingEdit,
    editError,
    startEdit,
    cancelEdit,
    saveEdit,
  };
}
