"""Phase 6 — corpus ingestion: loaders, chunker, and the upload/list/remove path.

Covers the three pieces independently:
* loaders — Markdown headings → sections, plain text → one section, and the
  failure cases (corrupt PDF, unsupported type, empty file) raise LoaderError.
* chunker — overlapping ~N-word windows that never cross a section boundary and
  carry the page/section locator forward.
* corpus orchestrator — a good file becomes a ``ready`` manifest entry with a
  chunk count; a corrupt file becomes a ``failed`` entry (no crash); remove drops
  the entry and its vectors. The embedder/Chroma are stubbed so these stay fast
  and offline.

The real-PDF loader test uses reportlab to mint a fixture and is skipped if it
isn't installed, so the suite passes in a minimal environment.
"""

from __future__ import annotations

import io

import pytest

from ingest import chunker
from ingest.chunker import chunk_sections
from ingest.loaders import LoadedSection, LoaderError, load_document


# --- loaders --------------------------------------------------------------- #

def test_markdown_splits_on_headings_and_keeps_them():
    md = b"# Title\n\nIntro line.\n\n## Section One\n\nBody one.\n\n## Section Two\n\nBody two."
    sections = load_document("notes.md", md)
    headings = [s.section for s in sections]
    assert "Section One" in headings
    assert "Section Two" in headings
    # Body text is attached to its heading's section.
    one = next(s for s in sections if s.section == "Section One")
    assert "Body one." in one.text


def test_markdown_without_headings_is_one_section():
    sections = load_document("flat.md", b"Just some prose with no headings at all.")
    assert len(sections) == 1
    assert sections[0].section is None


def test_text_loads_as_single_section_no_locator():
    sections = load_document("a.txt", b"plain text content")
    assert len(sections) == 1
    assert sections[0].page is None and sections[0].section is None


def test_unsupported_extension_raises():
    with pytest.raises(LoaderError):
        load_document("photo.png", b"\x89PNG\r\n")


def test_empty_file_raises():
    with pytest.raises(LoaderError):
        load_document("empty.txt", b"   \n  ")


def test_corrupt_pdf_raises_loadererror_not_crash():
    with pytest.raises(LoaderError):
        load_document("broken.pdf", b"%PDF-1.4 not really a pdf \x00\x01 garbage")


def test_real_pdf_loads_pages_with_page_numbers():
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for pg in range(1, 4):
        c.drawString(72, 720, f"Page {pg}: the quick brown fox jumps over the lazy dog.")
        c.showPage()
    c.save()

    sections = load_document("doc.pdf", buf.getvalue())
    assert len(sections) == 3
    assert [s.page for s in sections] == [1, 2, 3]
    assert "quick brown fox" in sections[0].text


# --- chunker --------------------------------------------------------------- #

def test_chunks_overlap_and_stay_under_size():
    words = " ".join(f"w{i}" for i in range(100))
    sections = [LoadedSection(text=words, page=5)]
    chunks = chunk_sections(sections, words_per_chunk=40, overlap_words=10)

    assert len(chunks) > 1
    assert all(len(c.text.split()) <= 40 for c in chunks)
    # Overlap: the start of chunk 2 repeats the tail of chunk 1.
    tail = chunks[0].text.split()[-10:]
    assert chunks[1].text.split()[:10] == tail
    # The page locator is carried onto every chunk; indexes are sequential.
    assert all(c.page == 5 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunks_never_cross_a_section_boundary():
    sections = [
        LoadedSection(text="alpha beta gamma", page=1),
        LoadedSection(text="delta epsilon zeta", page=2),
    ]
    chunks = chunk_sections(sections, words_per_chunk=50, overlap_words=5)
    # Two short sections → two chunks, each on its own page, neither merged.
    assert len(chunks) == 2
    assert {c.page for c in chunks} == {1, 2}
    assert "delta" not in chunks[0].text


def test_default_chunk_size_targets_about_500_tokens():
    # ~385 words ≈ 500 tokens at the documented ratio.
    assert 360 <= chunker.WORDS_PER_CHUNK <= 410
    assert chunker.estimate_tokens("one two three four five") == round(5 * 1.3)


# --- corpus orchestrator (embedder + Chroma stubbed) ----------------------- #

@pytest.fixture()
def orch(tmp_path, monkeypatch):
    """The corpus orchestrator pointed at a temp vault, with vector ops stubbed.

    Stubbing ``memory.vector`` keeps these tests offline and fast (no embedding
    model, no ChromaDB on disk) while still exercising the real save → load →
    chunk path and the manifest/remove bookkeeping.
    """
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))

    from ingest import corpus
    from memory import vector

    indexed: dict[str, int] = {}
    deleted: list[str] = []

    def fake_index(*, doc_id, source_file, chunks):
        indexed[doc_id] = len(chunks)
        return len(chunks)

    monkeypatch.setattr(vector, "index_corpus_chunks", fake_index)
    monkeypatch.setattr(vector, "delete_corpus_document", lambda doc_id: deleted.append(doc_id))

    return corpus, {"indexed": indexed, "deleted": deleted}


def test_ingest_good_file_becomes_ready_manifest_entry(orch):
    corpus, spy = orch
    doc = corpus.ingest_file("knots.txt", b"The bowline forms a fixed loop. " * 20)
    assert doc["status"] == "ready"
    assert doc["chunk_count"] >= 1
    # The raw bytes were stored in the vault and the manifest lists it.
    assert (corpus.corpus_dir() / doc["stored_filename"]).exists()
    listed = corpus.list_documents()
    assert [d["id"] for d in listed] == [doc["id"]]
    assert spy["indexed"][doc["id"]] == doc["chunk_count"]


def test_ingest_corrupt_file_becomes_failed_entry_not_crash(orch):
    corpus, _ = orch
    doc = corpus.ingest_file("broken.pdf", b"%PDF not a real pdf \x00 garbage")
    assert doc["status"] == "failed"
    assert doc["error"]
    assert doc["chunk_count"] == 0
    # Failed documents still appear in the library so the user can see + remove them.
    assert corpus.list_documents()[0]["id"] == doc["id"]
    # No orphaned bytes left behind for a file that couldn't be read.
    assert not (corpus.corpus_dir() / doc["stored_filename"]).exists()


def test_list_is_newest_first(orch):
    corpus, _ = orch
    a = corpus.ingest_file("a.txt", b"first file content here")
    b = corpus.ingest_file("b.txt", b"second file content here")
    ids = [d["id"] for d in corpus.list_documents()]
    assert ids[0] == b["id"] and ids[1] == a["id"]


def test_remove_drops_entry_and_deletes_vectors(orch):
    corpus, spy = orch
    doc = corpus.ingest_file("k.txt", b"some indexable content for removal test")
    assert corpus.remove_document(doc["id"]) is True
    assert corpus.list_documents() == []
    assert doc["id"] in spy["deleted"]
    assert corpus.remove_document(doc["id"]) is False  # already gone


# --- versioning guard (§6) ------------------------------------------------- #

def test_model_version_mismatch_raises():
    from memory import vector

    class FakeCollection:
        name = "corpus"
        metadata = {vector.MODEL_META_KEY: "some-other-model"}

    with pytest.raises(vector.EmbeddingModelMismatch):
        vector._check_model_version(FakeCollection())


def test_model_version_match_is_ok():
    from memory import vector

    class FakeCollection:
        name = "corpus"
        metadata = {vector.MODEL_META_KEY: vector.EMBED_MODEL}

    vector._check_model_version(FakeCollection())  # does not raise
