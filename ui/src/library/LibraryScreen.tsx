import { useRef, type ChangeEvent, type DragEvent } from "react";
import { Icon } from "../components";
import { useLibrary, type PendingUpload, type UseLibrary } from "./useLibrary";
import type { CorpusDocument } from "./api";

/**
 * LibraryScreen — the Phase-6 Library surface.
 *
 * The user hands Eva their books: a drag-and-drop (or click-to-browse) zone
 * accepts PDF / Markdown / text, each upload runs the real ingest pipeline
 * server-side (load → chunk → embed → index), and the documents list below shows
 * each one with its chunk count and status, plus a remove control. A file Eva
 * can't read appears with a clear failure state rather than vanishing.
 */

const ACCEPT = ".pdf,.md,.markdown,.txt,.text";

export function LibraryScreen() {
  const lib = useLibrary();
  return (
    <div className="library">
      <DropZone lib={lib} />
      <DocumentList lib={lib} />
    </div>
  );
}

/* --- The drop zone -------------------------------------------------------- */

function DropZone({ lib }: { lib: UseLibrary }) {
  const inputRef = useRef<HTMLInputElement>(null);

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    lib.setDragging(false);
    if (e.dataTransfer.files?.length) lib.upload(e.dataTransfer.files);
  };

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) lib.upload(e.target.files);
    e.target.value = ""; // allow re-selecting the same file
  };

  return (
    <div
      className={`library__drop${lib.dragging ? " library__drop--over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!lib.dragging) lib.setDragging(true);
      }}
      onDragLeave={(e) => {
        // Only clear when leaving the zone itself, not when crossing a child.
        if (e.currentTarget === e.target) lib.setDragging(false);
      }}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
      }}
      aria-label="Add a document by dropping a file here or pressing Enter to browse"
    >
      <span className="library__drop-mark" aria-hidden="true">
        <Icon name="upload" size={26} />
      </span>
      <p className="library__drop-title">
        {lib.dragging ? "Drop to add it to your library" : "Add a document"}
      </p>
      <p className="library__drop-hint">
        Drag a file here, or click to browse. PDF, Markdown, or text — it stays on
        this computer.
      </p>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        multiple
        className="library__file-input"
        onChange={onChange}
      />
    </div>
  );
}

/* --- The documents list --------------------------------------------------- */

function DocumentList({ lib }: { lib: UseLibrary }) {
  const isEmpty =
    lib.loaded && lib.documents.length === 0 && lib.pending.length === 0;

  return (
    <div className="library__list">
      {lib.pending.map((u) => (
        <PendingRow key={u.tempId} upload={u} onDismiss={() => lib.dismissPending(u.tempId)} />
      ))}

      {lib.documents.map((doc) => (
        <DocumentRow
          key={doc.id}
          doc={doc}
          removing={lib.removingId === doc.id}
          onRemove={() => lib.remove(doc.id)}
        />
      ))}

      {isEmpty && (
        <p className="library__empty">
          Nothing here yet. Once you add a book or some notes, Eva can draw on them
          — quoting the page, never inventing a source.
        </p>
      )}
    </div>
  );
}

/** A file still being indexed (or one that failed to even reach the server). */
function PendingRow({
  upload,
  onDismiss,
}: {
  upload: PendingUpload;
  onDismiss: () => void;
}) {
  const errored = upload.state === "error";
  return (
    <article className={`libdoc${errored ? " libdoc--failed" : ""}`}>
      <span className="libdoc__icon" aria-hidden="true">
        {errored ? <Icon name="alert" size={20} /> : <span className="libdoc__spinner" />}
      </span>
      <div className="libdoc__body">
        <p className="libdoc__name">{upload.filename}</p>
        <p className="libdoc__meta">
          {errored ? upload.error : "Reading and indexing…"}
        </p>
      </div>
      {errored && (
        <button className="btn btn--ghost btn--sm" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </article>
  );
}

/** One ingested document: name, chunk count / status, and remove. */
function DocumentRow({
  doc,
  removing,
  onRemove,
}: {
  doc: CorpusDocument;
  removing: boolean;
  onRemove: () => void;
}) {
  const failed = doc.status === "failed";
  return (
    <article className={`libdoc${failed ? " libdoc--failed" : ""}`}>
      <span className="libdoc__icon" aria-hidden="true">
        <Icon name={failed ? "alert" : "file"} size={20} />
      </span>
      <div className="libdoc__body">
        <p className="libdoc__name">{doc.filename}</p>
        <p className="libdoc__meta">
          {failed ? (
            <span className="libdoc__error">{doc.error || "Couldn't read this file."}</span>
          ) : (
            <>
              <span className="libdoc__badge">Ready</span>
              {doc.chunk_count} {doc.chunk_count === 1 ? "passage" : "passages"} indexed
            </>
          )}
        </p>
      </div>
      <button
        className="btn btn--ghost btn--sm libdoc__remove"
        onClick={onRemove}
        disabled={removing}
        aria-label={`Remove ${doc.filename}`}
        title="Remove"
      >
        {removing ? "Removing…" : <Icon name="trash" size={18} />}
      </button>
    </article>
  );
}
