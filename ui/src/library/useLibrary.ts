import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchDocuments,
  removeDocument,
  uploadDocument,
  type CorpusDocument,
} from "./api";

/**
 * useLibrary — all the state behind the Library surface, kept here so the screen
 * stays presentational.
 *
 * It owns three things:
 *   - the list of ingested documents (refreshed after every change)
 *   - the in-flight upload queue, so a 50-page PDF shows an "Indexing…" row while
 *     the synchronous server-side ingest runs, then is replaced by the real doc
 *   - the drag-over flag for the drop zone, and per-row removing state
 *
 * The backend ingest is synchronous (load → chunk → embed → index all happen in
 * the upload request), so "progress" here is simply: a pending row appears, then
 * resolves to ready/failed. A file the server couldn't parse returns as a
 * `failed` document (a normal result, not a thrown error); only a transport
 * failure (offline, too large) surfaces as a transient error row.
 */

/** An upload still in flight (the request hasn't returned yet). */
export type PendingUpload = {
  /** Client-only id so React can key the row before the server assigns one. */
  tempId: string;
  filename: string;
  /** "uploading" while indexing; "error" only on a transport/size failure. */
  state: "uploading" | "error";
  error?: string;
};

export type UseLibrary = {
  documents: CorpusDocument[];
  pending: PendingUpload[];
  dragging: boolean;
  removingId: string | null;
  loading: boolean;
  /** True once the first fetch has resolved (so we can show a real empty state). */
  loaded: boolean;
  upload: (files: FileList | File[]) => void;
  remove: (id: string) => void;
  dismissPending: (tempId: string) => void;
  setDragging: (on: boolean) => void;
};

// Monotonic counter for client-side temp ids — no Date/random needed, and stable
// across renders so an in-flight row keeps its key.
let _tempSeq = 0;

export function useLibrary(): UseLibrary {
  const [documents, setDocuments] = useState<CorpusDocument[]>([]);
  const [pending, setPending] = useState<PendingUpload[]>([]);
  const [dragging, setDragging] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const docs = await fetchDocuments();
      if (mounted.current) setDocuments(docs);
    } catch {
      /* backend not up yet — keep whatever list we have */
    } finally {
      if (mounted.current) {
        setLoading(false);
        setLoaded(true);
      }
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const upload = useCallback(
    (files: FileList | File[]) => {
      const list = Array.from(files);
      if (list.length === 0) return;

      for (const file of list) {
        const tempId = `up-${_tempSeq++}`;
        setPending((p) => [
          ...p,
          { tempId, filename: file.name, state: "uploading" },
        ]);

        uploadDocument(file)
          .then(async () => {
            // The doc (ready or failed) is now in the manifest — pull the truth.
            await refresh();
            if (mounted.current) {
              setPending((p) => p.filter((u) => u.tempId !== tempId));
            }
          })
          .catch((err: unknown) => {
            if (!mounted.current) return;
            const message =
              err instanceof Error ? err.message : "Couldn't upload this file.";
            setPending((p) =>
              p.map((u) =>
                u.tempId === tempId ? { ...u, state: "error", error: message } : u,
              ),
            );
          });
      }
    },
    [refresh],
  );

  const remove = useCallback(
    async (id: string) => {
      setRemovingId(id);
      try {
        await removeDocument(id);
        await refresh();
      } catch {
        /* leave the row; the user can retry */
      } finally {
        if (mounted.current) setRemovingId(null);
      }
    },
    [refresh],
  );

  const dismissPending = useCallback((tempId: string) => {
    setPending((p) => p.filter((u) => u.tempId !== tempId));
  }, []);

  return {
    documents,
    pending,
    dragging,
    removingId,
    loading,
    loaded,
    upload,
    remove,
    dismissPending,
    setDragging,
  };
}
