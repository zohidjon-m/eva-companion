"""Phase 7 — corpus retrieval and the grounded-citation discipline.

The vector store is mocked so these test retrieval's own logic: the relevance
threshold (the thing that makes "not in the library" return nothing rather than a
fabricated citation), the citation labelling, the prompt-context formatting, and
the fail-soft behaviour when the store errors.
"""

from __future__ import annotations

from memory import retrieval


def _chroma(docs, metas, dists):
    """Build a ChromaDB-shaped query result (one query → index 0 lists)."""
    return {"documents": [docs], "metadatas": [metas], "distances": [dists]}


def test_threshold_drops_irrelevant_passages(monkeypatch):
    # Two near hits, one far-off hit; only those within max_distance survive.
    # An explicit max_distance keeps this test independent of the tuned default.
    raw = _chroma(
        docs=["near one", "near two", "way off"],
        metas=[
            {"source_file": "book.pdf", "page": 12},
            {"source_file": "book.pdf", "page": 13},
            {"source_file": "book.pdf", "page": 99},
        ],
        dists=[0.20, 0.30, 0.90],
    )
    monkeypatch.setattr(retrieval.vector, "query_corpus", lambda q, n_results: raw)

    passages = retrieval.retrieve_corpus("a relevant question", max_distance=0.40)
    assert [p.text for p in passages] == ["near one", "near two"]


def test_default_threshold_is_tuned_value():
    # The empirically-chosen default (see retrieval.py): mid-gap between an
    # in-document hit (~0.31) and the nearest off-topic hit (~0.45).
    assert retrieval.MAX_DISTANCE == 0.38


def test_nothing_relevant_returns_empty(monkeypatch):
    # Everything above the threshold → empty list → the caller lets Eva say she
    # doesn't find it, and shows no (fabricated) citation.
    raw = _chroma(
        docs=["unrelated"],
        metas=[{"source_file": "book.pdf", "page": 3}],
        dists=[1.20],
    )
    monkeypatch.setattr(retrieval.vector, "query_corpus", lambda q, n_results: raw)
    assert retrieval.retrieve_corpus("totally absent topic") == []


def test_empty_query_does_not_query_store(monkeypatch):
    called = {"n": 0}

    def spy(q, n_results):
        called["n"] += 1
        return _chroma([], [], [])

    monkeypatch.setattr(retrieval.vector, "query_corpus", spy)
    assert retrieval.retrieve_corpus("   ") == []
    assert called["n"] == 0


def test_store_error_degrades_to_no_passages(monkeypatch):
    def boom(q, n_results):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(retrieval.vector, "query_corpus", boom)
    # Must not raise — a retrieval failure fails toward fewer citations.
    assert retrieval.retrieve_corpus("anything") == []


def test_citation_label_prefers_page_then_section():
    page = retrieval.Passage("t", "book.pdf", page=42, section=None, distance=0.1)
    assert page.label() == "book.pdf · p. 42"

    section = retrieval.Passage("t", "notes.md", page=None, section="Intro", distance=0.1)
    assert section.label() == "notes.md · Intro"

    bare = retrieval.Passage("t", "plain.txt", page=None, section=None, distance=0.1)
    assert bare.label() == "plain.txt"


def test_as_citation_carries_full_passage_text():
    p = retrieval.Passage("the exact words", "book.pdf", page=7, section=None, distance=0.1)
    c = p.as_citation()
    assert c["label"] == "book.pdf · p. 7"
    assert c["text"] == "the exact words"
    assert c["source_file"] == "book.pdf"
    assert c["page"] == 7


def test_format_corpus_context_numbers_and_labels():
    passages = [
        retrieval.Passage("first", "book.pdf", page=1, section=None, distance=0.1),
        retrieval.Passage("second", "notes.md", page=None, section="Ch. 2", distance=0.2),
    ]
    out = retrieval.format_corpus_context(passages)
    assert "[1] book.pdf · p. 1" in out
    assert "first" in out
    assert "[2] notes.md · Ch. 2" in out
    assert out.index("[1]") < out.index("[2]")


def test_format_corpus_context_empty_is_blank():
    # An empty list yields "" so the prompt assembler drops the corpus slot.
    assert retrieval.format_corpus_context([]) == ""
