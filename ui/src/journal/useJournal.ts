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
  fetchDay,
  fetchDays,
  saveJournal,
  type DayEntry,
  type JournalDay,
} from "./api";

/**
 * useJournal — all the state behind the journaling surface, kept here so the
 * screen stays presentational.
 *
 * It owns four things:
 *   - the today's-entry draft, autosaved to localStorage every ~10s so a closed
 *     app never loses an unfinished entry
 *   - the explicit Save → capture → ask-Eva flow
 *   - the browse list of past days and the today's saved entries
 *   - which view is showing: the writer (today) or a read-only past day
 */

const DRAFT_KEY = "eva.journal.draft";
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

/** Which pane the main area is showing. */
export type JournalView = { kind: "write" } | { kind: "day"; date: string };

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
  days: JournalDay[];
  /** Entries already saved earlier today (shown above the editor). */
  todayEntries: DayEntry[];
  view: JournalView;
  dayEntries: DayEntry[];
  dayLoading: boolean;
  save: () => void;
  openDay: (date: string) => void;
  openWrite: () => void;
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

  const [days, setDays] = useState<JournalDay[]>([]);
  const [todayEntries, setTodayEntries] = useState<DayEntry[]>([]);

  const [view, setView] = useState<JournalView>({ kind: "write" });
  const [dayEntries, setDayEntries] = useState<DayEntry[]>([]);
  const [dayLoading, setDayLoading] = useState(false);

  // The latest draft, readable from interval/unmount callbacks without making
  // them depend on (and re-bind to) every keystroke.
  const draftRef = useRef(draft);
  draftRef.current = draft;

  // Pull the browse list and today's already-saved entries. Best-effort: if the
  // backend isn't up yet, leave what we have rather than blanking the screen.
  const refresh = useCallback(async () => {
    try {
      setDays(await fetchDays());
    } catch {
      /* offline — keep prior list */
    }
    try {
      setTodayEntries(await fetchDay(today));
    } catch {
      setTodayEntries([]);
    }
  }, [today]);

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

  const save = useCallback(async () => {
    const text = draftRef.current.trim();
    if (!text || saving) return;
    setSaving(true);
    setSaveError(null);
    setAck({ kind: "idle" });
    try {
      const { id } = await saveJournal(text);
      // Durable now: clear the editor + draft and surface the saved entry.
      setDraft("");
      localStorage.removeItem(DRAFT_KEY);
      setDraftSavedAt(null);
      await refresh();
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
  }, [saving, refresh]);

  const openDay = useCallback(
    async (date: string) => {
      if (date === today) {
        setView({ kind: "write" });
        return;
      }
      setView({ kind: "day", date });
      setDayLoading(true);
      setDayEntries([]);
      try {
        setDayEntries(await fetchDay(date));
      } catch {
        setDayEntries([]);
      } finally {
        setDayLoading(false);
      }
    },
    [today],
  );

  const openWrite = useCallback(() => setView({ kind: "write" }), []);

  return {
    today,
    draft,
    setDraft,
    draftSavedAt,
    saving,
    saveError,
    ack,
    days,
    todayEntries,
    view,
    dayEntries,
    dayLoading,
    save,
    openDay,
    openWrite,
  };
}
