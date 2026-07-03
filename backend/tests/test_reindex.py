"""R4 — L2 rebuildability: reindex derives ChromaDB vectors from L1 + corpus.

The vector store (embedding model + ChromaDB) is stubbed so these tests exercise
reindex's own logic — reading done extractions, decomposing them into journal
summaries and episode units, threading the seed flag, and idempotence — without
loading bge-small or writing a real Chroma store. A separate test drives the real
``vector.embed_episodes`` against a fake collection to pin the id/metadata
contract and the delete-before-upsert that keeps a shrunk unit set honest.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.fixture()
def rx(tmp_path, monkeypatch):
    """Reload the memory stack on a temp vault; stub the embedding calls."""
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    import memory
    import memory.db as db
    import memory.vault as vault
    import memory.vector as vector
    import memory.reindex as reindex

    for m in (memory, db, vault, vector, reindex):
        importlib.reload(m)

    summary_calls: list[dict] = []
    episode_calls: list[dict] = []

    def fake_embed_summary(**kw):
        summary_calls.append(kw)

    def fake_embed_episodes(**kw):
        episode_calls.append(kw)
        loops = [l for l in kw["open_loops"] if (l or {}).get("description")]
        events = [e for e in kw["events"] if e]
        return len(loops) + len(events)

    monkeypatch.setattr(vector, "embed_summary", fake_embed_summary)
    monkeypatch.setattr(vector, "embed_episodes", fake_embed_episodes)

    return reindex, db, vault, {"summary": summary_calls, "episodes": episode_calls}


def _seed_done(
    db, *, entry_id, date, mood, themes, summary, open_loops, events, is_seeded=False
):
    """Insert an entry + a done extraction directly into L1 (test seed helper)."""
    conn = db.get_or_create_db()
    try:
        db.insert_entry(
            conn, id=entry_id, date=date, type="journal", text="body",
            word_count=2, created_at=f"{date}T12:00:00", is_seeded=is_seeded,
        )
        db.create_pending_extraction(conn, entry_id, source_hash="hash-" + entry_id)
        db.finalize_extraction(
            conn, entry_id,
            mood=mood, emotions=[], entities=[], themes=themes, events=events,
            stated_goals=[], behaviors=[], decisions=[], open_loops=open_loops,
            self_judgments=[], summary=summary, extracted_at=f"{date}T12:00:05",
            source_hash="hash-" + entry_id,
        )
    finally:
        conn.close()


def test_reindex_embeds_journals_and_episodes_from_l1(rx):
    reindex, db, _, spy = rx
    _seed_done(
        db, entry_id="real1", date="2026-06-10", mood=2, themes=["work", "family"],
        summary="A full day at work, then dinner with family.",
        open_loops=[{"description": "call Mom back", "status": "open"}],
        events=["shipped the release"],
    )

    report = asyncio.run(reindex.reindex_all(include_corpus=False))

    assert report.entries_scanned == 1
    assert report.journals_embedded == 1
    assert report.episode_units_embedded == 2  # one loop + one event

    assert spy["summary"][0]["entry_id"] == "real1"
    assert spy["summary"][0]["themes"] == ["work", "family"]
    ep = spy["episodes"][0]
    assert ep["open_loops"] == [{"description": "call Mom back", "status": "open"}]
    assert ep["events"] == ["shipped the release"]


def test_reindex_threads_seed_flag_through_both_collections(rx):
    reindex, db, _, spy = rx
    _seed_done(
        db, entry_id="real1", date="2026-06-10", mood=1, themes=["a"],
        summary="real one", open_loops=[], events=["did a thing"], is_seeded=False,
    )
    _seed_done(
        db, entry_id="seed1", date="2026-06-11", mood=0, themes=["b"],
        summary="seed one", open_loops=[], events=["seeded thing"], is_seeded=True,
    )

    asyncio.run(reindex.reindex_all(include_corpus=False))

    seeded_by_id = {c["entry_id"]: c["is_seeded"] for c in spy["summary"]}
    assert seeded_by_id == {"real1": False, "seed1": True}
    ep_seeded = {c["entry_id"]: c["is_seeded"] for c in spy["episodes"]}
    assert ep_seeded == {"real1": False, "seed1": True}


def test_reindex_summary_only_entry_embeds_no_episode_units(rx):
    reindex, db, _, spy = rx
    _seed_done(
        db, entry_id="quiet", date="2026-06-10", mood=0, themes=[],
        summary="A quiet, uneventful day.", open_loops=[], events=[],
    )

    report = asyncio.run(reindex.reindex_all(include_corpus=False))

    assert report.journals_embedded == 1
    assert report.episode_units_embedded == 0
    # embed_episodes is still called (so a prior unit set would be cleared).
    assert len(spy["episodes"]) == 1


def test_reindex_is_idempotent(rx):
    reindex, db, _, _ = rx
    _seed_done(
        db, entry_id="e1", date="2026-06-10", mood=2, themes=["x"],
        summary="s", open_loops=[{"description": "loop", "status": "open"}],
        events=["ev"],
    )

    first = asyncio.run(reindex.reindex_all(include_corpus=False))
    second = asyncio.run(reindex.reindex_all(include_corpus=False))
    assert first == second


def test_reindex_folds_in_corpus_counts_when_enabled(rx, monkeypatch):
    reindex, db, _, _ = rx
    _seed_done(
        db, entry_id="e1", date="2026-06-10", mood=0, themes=[],
        summary="s", open_loops=[], events=[],
    )
    from ingest import corpus
    monkeypatch.setattr(corpus, "reindex_all_documents", lambda: (3, 42, 1))

    report = asyncio.run(reindex.reindex_all(include_corpus=True))

    assert (report.corpus_docs, report.corpus_chunks, report.corpus_failed) == (3, 42, 1)


# --- vector.embed_episodes contract (fake collection, no real Chroma) ------- #

class _FakeCollection:
    """Records delete/upsert calls so we can assert the episodes id/metadata contract."""

    def __init__(self):
        self.deleted: list[dict] = []
        self.upserts: list[dict] = []

    def delete(self, *, where):
        self.deleted.append(where)

    def upsert(self, *, ids, embeddings, documents, metadatas):
        self.upserts.append(
            {"ids": ids, "documents": documents, "metadatas": metadatas}
        )


def _patch_vector_for_fake_collection(monkeypatch):
    import memory.vector as vector
    importlib.reload(vector)
    fake = _FakeCollection()
    monkeypatch.setattr(vector, "_get_episodes_collection", lambda: fake)
    monkeypatch.setattr(vector, "_embed", lambda texts: [[0.0] for _ in texts])
    return vector, fake


def test_embed_episodes_builds_stable_ids_and_metadata(monkeypatch):
    vector, fake = _patch_vector_for_fake_collection(monkeypatch)

    n = vector.embed_episodes(
        entry_id="e1", date="2026-06-10", mood=-1, themes=["family", "guilt"],
        open_loops=[{"description": "call Mom back", "status": "open"}],
        events=["missed the deadline"],
    )

    assert n == 2
    # Existing units cleared first (delete-before-upsert), scoped to this entry.
    assert fake.deleted == [{"entry_id": "e1"}]
    up = fake.upserts[0]
    assert up["ids"] == ["e1:open_loop:0", "e1:event:1"]
    assert up["documents"] == ["call Mom back", "missed the deadline"]
    m0 = up["metadatas"][0]
    assert m0["entry_id"] == "e1" and m0["type"] == "open_loop" and m0["unit_id"] == 0
    assert m0["themes"] == "family, guilt" and m0["mood"] == -1 and m0["is_seeded"] is False


def test_embed_episodes_with_no_units_clears_but_does_not_upsert(monkeypatch):
    vector, fake = _patch_vector_for_fake_collection(monkeypatch)

    n = vector.embed_episodes(
        entry_id="e2", date="2026-06-10", mood=None, themes=[],
        open_loops=[], events=[],
    )

    assert n == 0
    assert fake.deleted == [{"entry_id": "e2"}]  # stale units still cleared
    assert fake.upserts == []  # nothing to insert


def test_embed_episodes_omits_mood_when_none(monkeypatch):
    vector, fake = _patch_vector_for_fake_collection(monkeypatch)

    vector.embed_episodes(
        entry_id="e3", date="2026-06-10", mood=None, themes=["x"],
        open_loops=[], events=["something happened"],
    )

    assert "mood" not in fake.upserts[0]["metadatas"][0]
