import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Icon } from "../components";
import { MicButton } from "../voice/MicButton";
import { JournalEditor, type JournalEditorHandle } from "./Editor";
import {
  useJournal,
  type AckState,
  type JournalLayout,
  type UseJournal,
} from "./useJournal";
import { toDisplayMarkdown, type JournalEntry } from "./api";

/**
 * JournalScreen — the journaling surface, a stream of discrete posts.
 *
 * It opens on a flat history of past entries (newest first) shown as a grid of
 * cards or a list of rows — the reader's choice, remembered between visits — with
 * an explicit "New entry" action. Writing happens on its own compose screen;
 * every Save creates a separate post. Opening a post shows it read-only, with
 * Eva's one gentle line when it was just written.
 */

export function JournalScreen() {
  const j = useJournal();

  return (
    <div className="journal">
      {j.view.kind === "index" && <HistoryIndex journal={j} />}
      {j.view.kind === "compose" && <Composer journal={j} />}
      {j.view.kind === "entry" && <EntryView journal={j} />}
    </div>
  );
}

/* --- History index: the landing view -------------------------------------- */

function HistoryIndex({ journal: j }: { journal: UseJournal }) {
  return (
    <section className="journal__main">
      <div className="jhist">
        <header className="jhist__bar">
          <h2 className="jhist__title">Your journal</h2>
          <div className="jhist__actions">
            {j.entries.length > 0 && (
              <LayoutToggle layout={j.layout} onChange={j.setLayout} />
            )}
            <button className="btn btn--primary btn--md" onClick={j.openCompose}>
              <Icon name="feather" size={16} /> New entry
            </button>
          </div>
        </header>

        {j.loadingEntries && j.entries.length === 0 ? (
          <p className="jhist__empty">Gathering your entries…</p>
        ) : j.entries.length === 0 ? (
          <EmptyHistory onNew={j.openCompose} />
        ) : j.layout === "grid" ? (
          <div className="jhist__grid">
            {j.entries.map((e) => (
              <EntryCard
                key={e.id}
                entry={e}
                today={j.today}
                onOpen={() => j.openEntry(e.id)}
              />
            ))}
          </div>
        ) : (
          <div className="jhist__list">
            {j.entries.map((e) => (
              <EntryRow
                key={e.id}
                entry={e}
                today={j.today}
                onOpen={() => j.openEntry(e.id)}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

/** One post as a card (grid layout). */
function EntryCard({
  entry,
  today,
  onOpen,
}: {
  entry: JournalEntry;
  today: string;
  onOpen: () => void;
}) {
  return (
    <button className="jcard" onClick={onOpen}>
      <span className="jcard__date">{formatDate(entry.date, today)}</span>
      <span className="jcard__time">{timeFromCreated(entry.created_at)}</span>
      <span className="jcard__preview">{entry.preview || "—"}</span>
      <span className="jcard__meta">{entry.word_count} words</span>
    </button>
  );
}

/** One post as a row (list layout, like a chat-history rail). */
function EntryRow({
  entry,
  today,
  onOpen,
}: {
  entry: JournalEntry;
  today: string;
  onOpen: () => void;
}) {
  return (
    <button className="jrow" onClick={onOpen}>
      <span className="jrow__date">
        {formatDate(entry.date, today)} · {timeFromCreated(entry.created_at)}
      </span>
      <span className="jrow__preview">{entry.preview || "—"}</span>
    </button>
  );
}

/** Grid/list segmented toggle. */
function LayoutToggle({
  layout,
  onChange,
}: {
  layout: JournalLayout;
  onChange: (l: JournalLayout) => void;
}) {
  return (
    <div className="jtoggle" role="group" aria-label="History layout">
      <button
        className={`jtoggle__btn${layout === "grid" ? " jtoggle__btn--on" : ""}`}
        onClick={() => onChange("grid")}
        aria-pressed={layout === "grid"}
        title="Grid"
      >
        <GridGlyph />
      </button>
      <button
        className={`jtoggle__btn${layout === "list" ? " jtoggle__btn--on" : ""}`}
        onClick={() => onChange("list")}
        aria-pressed={layout === "list"}
        title="List"
      >
        <ListGlyph />
      </button>
    </div>
  );
}

function EmptyHistory({ onNew }: { onNew: () => void }) {
  return (
    <div className="jhist__empty-state">
      <p className="jhist__empty-title">Your journal is empty</p>
      <p className="jhist__empty-sub">
        Each entry you write becomes its own post, kept here on this device.
      </p>
      <button className="btn btn--primary btn--md" onClick={onNew}>
        <Icon name="feather" size={16} /> Write your first entry
      </button>
    </div>
  );
}

/* --- Compose: a new post --------------------------------------------------- */

function Composer({ journal: j }: { journal: UseJournal }) {
  const editorRef = useRef<JournalEditorHandle>(null);
  const [imgError, setImgError] = useState<string | null>(null);

  // Land in the editor ready to type.
  useEffect(() => {
    if (!j.saving) editorRef.current?.focus();
  }, [j.saving]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // Cmd/Ctrl+Enter saves; plain Enter is a newline (this is long-form writing).
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      j.save();
    }
  };

  const canSave = j.draft.trim().length > 0 && !j.saving;

  // Dictation inserts the spoken words at the cursor for the user to read over
  // and edit before they explicitly Save — same ritual as typing.
  const onTranscribed = (text: string) => {
    editorRef.current?.insertText(text);
  };

  return (
    <section className="journal__main">
      <div className="journal__editor" onKeyDown={onKeyDown}>
        <div className="journal__editor-scroll">
          <div className="journal__dayhead">
            <button className="btn btn--ghost btn--sm" onClick={j.openIndex}>
              ← All entries
            </button>
            <h2 className="journal__prompt">New entry</h2>
          </div>
          <JournalEditor
            ref={editorRef}
            editable
            value={j.draft}
            onChange={(md) => j.setDraft(md)}
            onUploadError={setImgError}
          />
          {imgError && <p className="journal__error">{imgError}</p>}
          {j.saveError && <p className="journal__error">{j.saveError}</p>}
        </div>

        <div className="journal__footer">
          <div className="journal__footer-left">
            <MicButton onTranscribed={onTranscribed} disabled={j.saving} />
            <span className="journal__autosave">
              {autosaveLabel(j.draft, j.draftSavedAt)}
            </span>
          </div>
          <button
            className="btn btn--primary btn--md"
            onClick={j.save}
            disabled={!canSave}
          >
            {j.saving ? "Saving…" : "Save entry"}
          </button>
        </div>
      </div>
    </section>
  );
}

/* --- Entry: one post, read-only ------------------------------------------- */

function EntryView({ journal: j }: { journal: UseJournal }) {
  const d = j.entryDetail;
  const editRef = useRef<JournalEditorHandle>(null);
  const [imgError, setImgError] = useState<string | null>(null);
  const displayedText =
    d && j.showingOriginal && d.original_text ? d.original_text : d?.text ?? "";

  // When edit mode opens, drop the cursor into the editor ready to type.
  useEffect(() => {
    if (j.editing && !j.savingEdit) editRef.current?.focus();
  }, [j.editing, j.savingEdit]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // Cmd/Ctrl+Enter saves the edit; Esc cancels — same long-form ergonomics.
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      j.saveEdit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      j.cancelEdit();
    }
  };

  const canSaveEdit = j.editDraft.trim().length > 0 && !j.savingEdit;

  return (
    <section className="journal__main">
      <div className="journal__editor" onKeyDown={j.editing ? onKeyDown : undefined}>
        <div className="journal__editor-scroll">
          <div className="journal__dayhead">
            <button
              className="btn btn--ghost btn--sm"
              onClick={j.editing ? j.cancelEdit : j.openIndex}
            >
              ← {j.editing ? "Cancel edit" : "All entries"}
            </button>
            <h2 className="journal__prompt">
              {d
                ? `${formatDate(d.date, j.today)} · ${timeFromCreated(d.created_at)}`
                : "Entry"}
            </h2>
            {d && !j.editing && d.has_revisions && (
              <button className="btn btn--ghost btn--sm" onClick={j.toggleOriginal}>
                {j.showingOriginal ? "Current" : "Original"}
              </button>
            )}
            {/* Edit affordance — only on a loaded post, hidden while editing. */}
            {d && !j.editing && (
              <button
                className="btn btn--ghost btn--sm journal__edit-btn"
                onClick={j.startEdit}
              >
                <Icon name="feather" size={15} /> Edit
              </button>
            )}
          </div>

          {j.entryLoading ? (
            <p className="journal__saved-note">Opening…</p>
          ) : !d ? (
            <p className="journal__saved-note">This entry could not be found.</p>
          ) : j.editing ? (
            <>
              <JournalEditor
                ref={editRef}
                editable
                value={j.editDraft}
                onChange={(md) => j.setEditDraft(md)}
                onUploadError={setImgError}
              />
              {imgError && <p className="journal__error">{imgError}</p>}
              {j.editError && <p className="journal__error">{j.editError}</p>}
            </>
          ) : (
            <JournalEditor editable={false} value={toDisplayMarkdown(displayedText)} />
          )}

          {!j.editing && <Acknowledgment ack={j.ack} />}
        </div>

        <div className={`journal__footer${j.editing ? "" : " journal__footer--end"}`}>
          {j.editing ? (
            <>
              <div className="journal__footer-left">
                <MicButton
                  onTranscribed={(text) => editRef.current?.insertText(text)}
                  disabled={j.savingEdit}
                />
                <span className="journal__autosave">
                  Dictate or type — saved when you press Save changes.
                </span>
              </div>
              <div className="journal__footer-actions">
                <button className="btn btn--ghost btn--md" onClick={j.cancelEdit}>
                  Cancel
                </button>
                <button
                  className="btn btn--primary btn--md"
                  onClick={j.saveEdit}
                  disabled={!canSaveEdit}
                >
                  {j.savingEdit ? "Saving…" : "Save changes"}
                </button>
              </div>
            </>
          ) : (
            <button className="btn btn--primary btn--md" onClick={j.openCompose}>
              <Icon name="feather" size={16} /> New entry
            </button>
          )}
        </div>
      </div>
    </section>
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

/* --- Inline glyphs for the layout toggle ---------------------------------- */

function GridGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden="true" fill="currentColor">
      <rect x="0" y="0" width="6.5" height="6.5" rx="1.5" />
      <rect x="8.5" y="0" width="6.5" height="6.5" rx="1.5" />
      <rect x="0" y="8.5" width="6.5" height="6.5" rx="1.5" />
      <rect x="8.5" y="8.5" width="6.5" height="6.5" rx="1.5" />
    </svg>
  );
}

function ListGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden="true" fill="currentColor">
      <rect x="0" y="1" width="15" height="2.5" rx="1.25" />
      <rect x="0" y="6.25" width="15" height="2.5" rx="1.25" />
      <rect x="0" y="11.5" width="15" height="2.5" rx="1.25" />
    </svg>
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

/** Pull "9:14 AM" out of an ISO "YYYY-MM-DDTHH:MM:SS" timestamp. */
function timeFromCreated(created: string): string {
  const t = created.includes("T") ? created.split("T")[1] : "";
  return t ? shortTime(t) : "";
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
