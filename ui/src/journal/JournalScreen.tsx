import { useEffect, useRef, type KeyboardEvent } from "react";
import { Icon } from "../components";
import { MicButton } from "../voice/MicButton";
import { appendTranscript } from "../voice/text";
import { useJournal, type AckState, type UseJournal } from "./useJournal";
import type { DayEntry, JournalDay } from "./api";

/**
 * JournalScreen — the Phase-5 journaling surface, deliberately not a chat thread.
 *
 * A two-pane ritual: a left rail to browse past days (the seed of time-travel)
 * and a calm, full-height editor on the right for today's entry. Saving is
 * explicit; the draft autosaves to this device every ~10s so nothing is lost.
 * After a save, Eva offers one gentle line — a reflection or a soft question,
 * never advice. Selecting a past day swaps the editor for a read-only day view.
 */

export function JournalScreen() {
  const j = useJournal();

  return (
    <div className="journal">
      <Aside journal={j} />
      <section className="journal__main">
        {j.view.kind === "write" ? (
          <Writer journal={j} />
        ) : (
          <DayView
            date={j.view.date}
            entries={j.dayEntries}
            loading={j.dayLoading}
            onBack={() => j.openDay(j.today)}
          />
        )}
      </section>
    </div>
  );
}

/* --- Left rail: browse past days ------------------------------------------ */

function Aside({ journal: j }: { journal: UseJournal }) {
  const writing = j.view.kind === "write";
  return (
    <aside className="journal__aside">
      <p className="journal__aside-head">Your journal</p>
      <button
        className={`jday jday--today${writing ? " jday--active" : ""}`}
        onClick={j.openWrite}
      >
        <span className="jday__date">
          <Icon name="feather" size={16} /> Today
        </span>
        <span className="jday__preview">Write a new entry</span>
      </button>

      {j.days.length > 0 && <p className="journal__aside-label">Past entries</p>}
      <div className="journal__days">
        {j.days
          .filter((d) => d.date !== j.today)
          .map((d) => (
            <DayButton
              key={d.date}
              day={d}
              today={j.today}
              active={j.view.kind === "day" && j.view.date === d.date}
              onClick={() => j.openDay(d.date)}
            />
          ))}
        {j.days.filter((d) => d.date !== j.today).length === 0 && (
          <p className="journal__aside-empty">
            Entries you save will gather here, day by day.
          </p>
        )}
      </div>
    </aside>
  );
}

function DayButton({
  day,
  today,
  active,
  onClick,
}: {
  day: JournalDay;
  today: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button className={`jday${active ? " jday--active" : ""}`} onClick={onClick}>
      <span className="jday__date">
        {formatDate(day.date, today)}
        {day.count > 1 && <span className="jday__count">{day.count}</span>}
      </span>
      <span className="jday__preview">{day.preview || "—"}</span>
    </button>
  );
}

/* --- Right pane: today's editor ------------------------------------------- */

function Writer({ journal: j }: { journal: UseJournal }) {
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Land in the editor ready to type.
  useEffect(() => {
    if (!j.saving) taRef.current?.focus();
  }, [j.saving]);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Cmd/Ctrl+Enter saves; plain Enter is a newline (this is long-form writing).
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      j.save();
    }
  };

  const canSave = j.draft.trim().length > 0 && !j.saving;

  // Dictation appends to the draft for the user to read over and edit before they
  // explicitly Save — the spoken path stays the same ritual as typing, just with
  // the words captured by voice. setDraft is the raw state setter, so the updater
  // form keeps this correct even if several transcripts land in quick succession.
  const onTranscribed = (text: string) => {
    j.setDraft((d) => appendTranscript(d, text));
    taRef.current?.focus();
  };

  return (
    <div className="journal__editor">
      <div className="journal__editor-scroll">
        {j.todayEntries.length > 0 && (
          <EarlierToday entries={j.todayEntries} />
        )}

        <h2 className="journal__prompt">How was your day?</h2>
        <textarea
          ref={taRef}
          className="journal__field"
          placeholder="Write as much or as little as you like. This stays on your device."
          value={j.draft}
          disabled={j.saving}
          onChange={(e) => j.setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          aria-label="Today's journal entry"
        />

        <Acknowledgment ack={j.ack} />
        {j.saveError && <p className="journal__error">{j.saveError}</p>}
      </div>

      <div className="journal__footer">
        <div className="journal__footer-left">
          <MicButton onTranscribed={onTranscribed} disabled={j.saving} />
          <span className="journal__autosave">{autosaveLabel(j.draft, j.draftSavedAt)}</span>
        </div>
        <button className="btn btn--primary btn--md" onClick={j.save} disabled={!canSave}>
          {j.saving ? "Saving…" : "Save entry"}
        </button>
      </div>
    </div>
  );
}

/** Entries already written today, shown read-only above the editor. */
function EarlierToday({ entries }: { entries: DayEntry[] }) {
  return (
    <div className="journal__earlier">
      <p className="journal__earlier-head">Earlier today</p>
      {entries.map((e, i) => (
        <article className="journal__entry" key={e.id ?? i}>
          <p className="journal__entry-time">{shortTime(e.time)}</p>
          <p className="journal__entry-text">{e.text}</p>
        </article>
      ))}
    </div>
  );
}

/** Eva's one gentle line after a save — or a quiet "saved" when she's offline. */
function Acknowledgment({ ack }: { ack: AckState }) {
  if (ack.kind === "idle") return null;
  if (ack.kind === "saved") {
    return <p className="journal__saved-note">Saved to your journal.</p>;
  }
  return (
    <div className="journal__ack" role="status">
      <span className="journal__ack-mark" aria-hidden="true">
        <Icon name="sparkle" size={18} />
      </span>
      {ack.kind === "loading" ? (
        <span className="journal__ack-text journal__ack-text--muted">
          Eva is reading what you wrote…
        </span>
      ) : (
        <span className="journal__ack-text">{ack.text}</span>
      )}
    </div>
  );
}

/* --- Right pane: a past day, read-only ------------------------------------ */

function DayView({
  date,
  entries,
  loading,
  onBack,
}: {
  date: string;
  entries: DayEntry[];
  loading: boolean;
  onBack: () => void;
}) {
  return (
    <div className="journal__editor">
      <div className="journal__editor-scroll">
        <div className="journal__dayhead">
          <button className="btn btn--ghost btn--sm" onClick={onBack}>
            ← Today
          </button>
          <h2 className="journal__prompt">{formatDate(date, "")}</h2>
        </div>

        {loading ? (
          <p className="journal__saved-note">Opening that day…</p>
        ) : entries.length === 0 ? (
          <p className="journal__saved-note">Nothing was written on this day.</p>
        ) : (
          entries.map((e, i) => (
            <article className="journal__entry" key={e.id ?? i}>
              <p className="journal__entry-time">{shortTime(e.time)}</p>
              <p className="journal__entry-text">{e.text}</p>
            </article>
          ))
        )}
      </div>
    </div>
  );
}

/* --- helpers -------------------------------------------------------------- */

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Friendly date: "Today" / "Yesterday" / "Mon, Jun 3" (year added if not this one). */
function formatDate(date: string, today: string): string {
  if (date === today) return "Today";
  const [y, m, d] = date.split("-").map(Number);
  if (!y || !m || !d) return date;
  const dt = new Date(y, m - 1, d);
  if (today) {
    const [ty, tm, td] = today.split("-").map(Number);
    const yest = new Date(ty, tm - 1, td - 1);
    if (dt.getFullYear() === yest.getFullYear() && dt.getMonth() === yest.getMonth() && dt.getDate() === yest.getDate()) {
      return "Yesterday";
    }
  }
  const base = `${WEEKDAYS[dt.getDay()]}, ${MONTHS[m - 1]} ${d}`;
  const nowYear = today ? Number(today.slice(0, 4)) : y;
  return y === nowYear ? base : `${base}, ${y}`;
}

/** "09:14:03" → "9:14 AM". */
function shortTime(time: string): string {
  const [h, m] = time.split(":").map(Number);
  if (Number.isNaN(h)) return time;
  const period = h < 12 ? "AM" : "PM";
  const hour = h % 12 === 0 ? 12 : h % 12;
  return `${hour}:${String(m).padStart(2, "0")} ${period}`;
}

function autosaveLabel(draft: string, savedAt: number | null): string {
  if (!draft.trim()) return "Your draft saves automatically as you write.";
  if (savedAt) return `Draft kept on this device · ${shortClock(savedAt)}`;
  return "Draft kept on this device.";
}

function shortClock(epochMs: number): string {
  const d = new Date(epochMs);
  const period = d.getHours() < 12 ? "AM" : "PM";
  const hour = d.getHours() % 12 === 0 ? 12 : d.getHours() % 12;
  return `${hour}:${String(d.getMinutes()).padStart(2, "0")} ${period}`;
}
