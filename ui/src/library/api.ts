/**
 * Library API — thin fetch wrappers over the Phase-6 corpus endpoints.
 *
 * Mirrors the journal API's style: tiny, dependency-free, talks to the backend on
 * loopback. Upload is a multipart POST (the only multipart call in the app);
 * list and remove are plain JSON. Each call returns a typed value or throws on a
 * transport/HTTP error so the hook decides how to surface it.
 */

const BASE = "http://127.0.0.1:8000";

/** One ingested document, as the manifest records it. */
export type CorpusDocument = {
  id: string;
  filename: string;
  ext: string;
  /** "ready" once indexed; "failed" if the file couldn't be read. */
  status: "ready" | "failed";
  chunk_count: number;
  /** A user-facing reason when status is "failed", else null. */
  error: string | null;
  added_at: string;
  stored_filename: string;
};

/** Fetch the list of ingested documents, newest first. */
export async function fetchDocuments(): Promise<CorpusDocument[]> {
  const resp = await fetch(`${BASE}/corpus`);
  if (!resp.ok) throw new Error(`GET /corpus -> ${resp.status}`);
  const { documents } = (await resp.json()) as { documents: CorpusDocument[] };
  return documents;
}

/**
 * Upload one file and resolve to its document record. Note the record may have
 * `status: "failed"` (a 200 response) — Eva read the upload but couldn't parse
 * the file. This function only throws on a real transport/HTTP error (e.g. the
 * file exceeds the size limit → 413).
 */
export async function uploadDocument(file: File): Promise<CorpusDocument> {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch(`${BASE}/corpus/upload`, { method: "POST", body: form });
  if (!resp.ok) {
    const detail = await resp
      .json()
      .then((b) => (b as { detail?: string }).detail)
      .catch(() => undefined);
    throw new Error(detail || `Upload failed (${resp.status})`);
  }
  return (await resp.json()) as CorpusDocument;
}

/** Remove a document (its chunks, stored bytes, and manifest entry). */
export async function removeDocument(id: string): Promise<void> {
  const resp = await fetch(`${BASE}/corpus/${id}`, { method: "DELETE" });
  if (!resp.ok && resp.status !== 404) {
    throw new Error(`DELETE /corpus/${id} -> ${resp.status}`);
  }
}
