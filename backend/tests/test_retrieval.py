"""Phase 7 — corpus retrieval and the grounded-citation discipline.
Phase 11 — journal memory recall (the "Eva remembers" tests live at the bottom).

The vector store is mocked so these test retrieval's own logic: the relevance
threshold (the thing that makes "not in the library" return nothing rather than a
fabricated citation), the citation labelling, the prompt-context formatting, and
the fail-soft behaviour when the store errors.
"""

from __future__ import annotations

from datetime import date

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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 11 — memory recall ("Eva remembers"). vector.recall is mocked so these
# exercise recall's own logic: the anti-fabrication threshold, recency weighting,
# the chip label, and the prompt-context formatting.
# ─────────────────────────────────────────────────────────────────────────────


def _journals(docs, metas, dists):
    """Build a ChromaDB-shaped recall result (one query → index 0 lists)."""
    return {"documents": [docs], "metadatas": [metas], "distances": [dists]}


def _meta(entry_id, date_str, *, mood=None, themes="work, family"):
    """A journals-collection metadata row as vector.embed_summary stores it."""
    m = {"entry_id": entry_id, "date": date_str, "themes": themes, "is_seeded": False}
    if mood is not None:
        m["mood"] = mood
    return m


def test_recall_threshold_drops_irrelevant_memories(monkeypatch):
    # Two near summaries, one far-off; only those within max_distance survive — the
    # gate that stops an off-topic message surfacing a (false) memory.
    raw = _journals(
        docs=["near one", "near two", "way off"],
        metas=[
            _meta("e1", "2026-06-10"),
            _meta("e2", "2026-06-09"),
            _meta("e3", "2026-06-08"),
        ],
        dists=[0.20, 0.30, 0.90],
    )
    monkeypatch.setattr(retrieval.vector, "recall", lambda q, n_results: raw)

    mems = retrieval.recall_memories(
        "a relevant message", max_distance=0.40, today=date(2026, 6, 11)
    )
    assert {m.entry_id for m in mems} == {"e1", "e2"}


def test_recall_never_fabricates_when_nothing_relevant(monkeypatch):
    # The Phase-11 honesty rule: ask about something never journaled → no memory,
    # so no context block and (in the handler) no chip.
    raw = _journals(
        docs=["unrelated life event"],
        metas=[_meta("e9", "2026-06-01")],
        dists=[1.30],
    )
    monkeypatch.setattr(retrieval.vector, "recall", lambda q, n_results: raw)
    assert retrieval.recall_memories("a topic never written about") == []


def test_recall_is_recency_weighted_among_relevant(monkeypatch):
    # Two memories cleared the gate at the SAME distance; the more recent one ranks
    # first. Recency only reorders memories that are already relevant.
    raw = _journals(
        docs=["older but relevant", "newer and relevant"],
        metas=[_meta("old", "2026-04-01"), _meta("new", "2026-06-10")],
        dists=[0.30, 0.30],
    )
    monkeypatch.setattr(retrieval.vector, "recall", lambda q, n_results: raw)

    mems = retrieval.recall_memories("relevant message", today=date(2026, 6, 11))
    assert [m.entry_id for m in mems] == ["new", "old"]


def test_recall_relevance_outranks_recency(monkeypatch):
    # Recency must NOT override relevance: a much closer (older) memory still beats a
    # barely-relevant fresh one, because the recency floor preserves most of the
    # relevance signal. This is what keeps recall honest rather than just "recent".
    raw = _journals(
        docs=["strongly relevant, older", "weakly relevant, today"],
        metas=[_meta("strong", "2026-03-01"), _meta("weak", "2026-06-11")],
        dists=[0.10, 0.54],
    )
    monkeypatch.setattr(retrieval.vector, "recall", lambda q, n_results: raw)

    mems = retrieval.recall_memories("message", today=date(2026, 6, 11))
    assert mems[0].entry_id == "strong"


def test_recall_keeps_at_most_top_k(monkeypatch):
    raw = _journals(
        docs=[f"s{i}" for i in range(6)],
        metas=[_meta(f"e{i}", "2026-06-10") for i in range(6)],
        dists=[0.10 + i * 0.01 for i in range(6)],
    )
    monkeypatch.setattr(retrieval.vector, "recall", lambda q, n_results: raw)
    mems = retrieval.recall_memories("msg", top_k=3, today=date(2026, 6, 11))
    assert len(mems) == 3


def test_recall_empty_query_does_not_query_store(monkeypatch):
    called = {"n": 0}

    def spy(q, n_results):
        called["n"] += 1
        return _journals([], [], [])

    monkeypatch.setattr(retrieval.vector, "recall", spy)
    assert retrieval.recall_memories("   ") == []
    assert called["n"] == 0


def test_recall_store_error_degrades_to_no_memories(monkeypatch):
    def boom(q, n_results):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(retrieval.vector, "recall", boom)
    # Must not raise — a recall failure is a non-event, never a crash.
    assert retrieval.recall_memories("anything") == []


def test_memory_chip_label_is_short_human_date():
    m = retrieval.Memory("e1", "2026-06-03", "summary", mood=2, themes=["work"], distance=0.2)
    assert m.chip_label() == "Jun 3"
    assert m.as_chip() == {"date": "2026-06-03", "label": "Jun 3"}


def test_memory_chip_label_tolerates_bad_date():
    m = retrieval.Memory("e1", "not-a-date", "s", mood=None, themes=[], distance=0.2)
    # Falls back to the raw string rather than crashing the chip.
    assert m.chip_label() == "not-a-date"


def test_parse_memory_results_splits_themes_back_to_list(monkeypatch):
    raw = _journals(
        docs=["a summary"],
        metas=[_meta("e1", "2026-06-10", mood=3, themes="work, family, prayer")],
        dists=[0.20],
    )
    monkeypatch.setattr(retrieval.vector, "recall", lambda q, n_results: raw)
    mems = retrieval.recall_memories("msg", today=date(2026, 6, 11))
    assert mems[0].themes == ["work", "family", "prayer"]
    assert mems[0].mood == 3


def test_format_memory_context_prefixes_each_with_its_date():
    mems = [
        retrieval.Memory("e1", "2026-06-03", "first summary", None, [], 0.2),
        retrieval.Memory("e2", "2026-05-28", "second summary", None, [], 0.3),
    ]
    out = retrieval.format_memory_context(mems)
    assert "[2026-06-03] first summary" in out
    assert "[2026-05-28] second summary" in out
    assert out.index("2026-06-03") < out.index("2026-05-28")


def test_format_memory_context_empty_is_blank():
    # Empty → "" so the assembler drops the memory slot (no past entry to reference).
    assert retrieval.format_memory_context([]) == ""


def test_memory_max_distance_is_in_a_sane_gating_range():
    # A conservative starting value pending tuning on a real vault (see retrieval.py):
    # tight enough to gate fabrication, loose enough to recall genuine matches.
    assert 0.4 <= retrieval.MEMORY_MAX_DISTANCE <= 0.7


# ─────────────────────────────────────────────────────────────────────────────
# R4 — episodes recall (open loops + notable events). Same store-mocked
# discipline: these test the threshold gate, the fail-soft behaviour, and the
# metadata parsing — never a real ChromaDB.
# ─────────────────────────────────────────────────────────────────────────────


def _episodes(docs, metas, dists):
    """Build a ChromaDB-shaped episodes result (one query → index 0 lists)."""
    return {"documents": [docs], "metadatas": [metas], "distances": [dists]}


def _ep_meta(entry_id, date_str, unit_type, *, unit_id=0, mood=None, themes="work"):
    """An episodes-collection metadata row as vector.embed_episodes stores it."""
    m = {
        "entry_id": entry_id,
        "date": date_str,
        "type": unit_type,
        "unit_id": unit_id,
        "themes": themes,
        "is_seeded": False,
    }
    if mood is not None:
        m["mood"] = mood
    return m


def test_recall_episodes_threshold_drops_irrelevant_units(monkeypatch):
    # Two near units, one far-off; only those within max_distance survive.
    raw = _episodes(
        docs=["call Mom back", "finish the grant draft", "unrelated event"],
        metas=[
            _ep_meta("e1", "2026-06-10", "open_loop"),
            _ep_meta("e2", "2026-06-09", "open_loop"),
            _ep_meta("e3", "2026-06-08", "event"),
        ],
        dists=[0.20, 0.30, 0.90],
    )
    monkeypatch.setattr(retrieval.vector, "recall_episodes", lambda q, n_results: raw)

    eps = retrieval.recall_episodes("a relevant message", max_distance=0.40)
    assert {e.entry_id for e in eps} == {"e1", "e2"}


def test_recall_episodes_never_fabricates_when_nothing_relevant(monkeypatch):
    # The honesty rule carries over: nothing on-topic → no episode at all.
    raw = _episodes(
        docs=["a loop about something else"],
        metas=[_ep_meta("e9", "2026-06-01", "open_loop")],
        dists=[1.30],
    )
    monkeypatch.setattr(retrieval.vector, "recall_episodes", lambda q, n_results: raw)
    assert retrieval.recall_episodes("a topic never written about") == []


def test_recall_episodes_ranks_by_relevance_not_recency(monkeypatch):
    # Episodes rank by distance alone — an old-but-closer open loop beats a fresher,
    # weaker one (an unresolved loop's value is often that it is old).
    raw = _episodes(
        docs=["older, closer loop", "newer, weaker loop"],
        metas=[_ep_meta("old", "2026-01-01", "open_loop"),
               _ep_meta("new", "2026-06-10", "open_loop")],
        dists=[0.15, 0.40],
    )
    monkeypatch.setattr(retrieval.vector, "recall_episodes", lambda q, n_results: raw)
    eps = retrieval.recall_episodes("message")
    assert [e.entry_id for e in eps] == ["old", "new"]


def test_recall_episodes_keeps_at_most_top_k(monkeypatch):
    raw = _episodes(
        docs=[f"u{i}" for i in range(6)],
        metas=[_ep_meta(f"e{i}", "2026-06-10", "event", unit_id=i) for i in range(6)],
        dists=[0.10 + i * 0.01 for i in range(6)],
    )
    monkeypatch.setattr(retrieval.vector, "recall_episodes", lambda q, n_results: raw)
    assert len(retrieval.recall_episodes("msg", top_k=3)) == 3


def test_recall_episodes_empty_query_does_not_query_store(monkeypatch):
    called = {"n": 0}

    def spy(q, n_results):
        called["n"] += 1
        return _episodes([], [], [])

    monkeypatch.setattr(retrieval.vector, "recall_episodes", spy)
    assert retrieval.recall_episodes("   ") == []
    assert called["n"] == 0


def test_recall_episodes_store_error_degrades_to_no_episodes(monkeypatch):
    def boom(q, n_results):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(retrieval.vector, "recall_episodes", boom)
    assert retrieval.recall_episodes("anything") == []


def test_parse_episode_results_carries_type_and_splits_themes(monkeypatch):
    raw = _episodes(
        docs=["call Mom back"],
        metas=[_ep_meta("e1", "2026-06-10", "open_loop", mood=-1, themes="family, guilt")],
        dists=[0.20],
    )
    monkeypatch.setattr(retrieval.vector, "recall_episodes", lambda q, n_results: raw)
    eps = retrieval.recall_episodes("msg")
    assert eps[0].type == "open_loop"
    assert eps[0].text == "call Mom back"
    assert eps[0].themes == ["family", "guilt"]
    assert eps[0].mood == -1


def test_episode_chip_label_is_short_human_date():
    e = retrieval.Episode("e1", "2026-06-03", "event", "hiked", mood=2, themes=["x"], distance=0.2)
    assert e.chip_label() == "Jun 3"
    assert e.as_chip() == {"date": "2026-06-03", "label": "Jun 3", "type": "event"}


def test_episode_max_distance_is_in_a_sane_gating_range():
    assert 0.4 <= retrieval.EPISODE_MAX_DISTANCE <= 0.7
