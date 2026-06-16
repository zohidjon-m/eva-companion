"""Phase 14 — GET /insights/graph + the seeded graph builder (§7.4 contract).

The endpoint is a pure DB read; the builder derives the seeded graph from the same
extractions the mood chart uses. These tests assert the §7.4 shape, the is_seeded
filter, referential integrity, and the rule that hypothesis edges (and only those)
carry is_hypothesis=true with a label and are typed "hypothesis".
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import app
from memory import db, graph

NODE_TYPES = set(graph.NODE_TYPES)
EDGE_TYPES = set(graph.EDGE_TYPES)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_VAULT_DIR", str(tmp_path / "local_vault"))
    return TestClient(app)


def _seed_entry(conn, *, date, themes, emotions, text, summary="s"):
    """One seeded entry + done extraction (the graph builder's input)."""
    entry_id = str(uuid.uuid4())
    db.insert_entry(
        conn, id=entry_id, date=date, type="journal", text=text,
        word_count=len(text.split()), created_at=f"{date}T21:30:00", is_seeded=True,
    )
    db.create_pending_extraction(conn, entry_id)
    db.finalize_extraction(
        conn, entry_id, mood=0, emotions=[{"name": e, "intensity": 0.5} for e in emotions],
        entities=[], themes=themes, events=[], stated_goals=[], behaviors=[],
        decisions=[], open_loops=[], self_judgments=[], summary=summary,
        extracted_at=f"{date}T21:30:01",
    )
    return entry_id


def test_empty_vault_returns_empty_graph(client):
    resp = client.get("/insights/graph")
    assert resp.status_code == 200
    assert resp.json() == {"nodes": [], "edges": []}


def test_seeded_graph_conforms_to_7_4(client):
    conn = db.get_or_create_db()
    # Two themes that co-occur across two entries → a co_occurrence edge.
    _seed_entry(conn, date="2026-06-01", themes=["work", "stress"], emotions=["anxiety"], text="(seed) busy")
    _seed_entry(conn, date="2026-06-02", themes=["work", "stress"], emotions=["anxiety"], text="(seed) busy again")
    graph.store_seed_graph(conn)
    conn.close()

    payload = client.get("/insights/graph", params={"include_seeded": "true"}).json()
    assert payload["nodes"], "expected seeded nodes"
    node_ids = {n["id"] for n in payload["nodes"]}

    for n in payload["nodes"]:
        assert set(n) >= {"id", "label", "type", "entry_count", "entries"}
        assert n["type"] in NODE_TYPES
        assert isinstance(n["entry_count"], int) and n["entry_count"] >= 0
        assert isinstance(n["entries"], list) and all(isinstance(x, str) for x in n["entries"])

    for e in payload["edges"]:
        assert set(e) >= {"id", "source", "target", "type", "weight", "is_hypothesis", "label", "entries"}
        assert e["type"] in EDGE_TYPES
        assert e["source"] in node_ids and e["target"] in node_ids  # referential integrity
        assert 0.0 <= e["weight"] <= 1.0
        assert isinstance(e["is_hypothesis"], bool)


def test_hypothesis_edges_are_typed_and_labelled(client):
    """A hypothesis edge is type 'hypothesis', carries is_hypothesis=true + a label;
    ordinary edges are not 'hypothesis' and carry no label."""
    conn = db.get_or_create_db()
    # Build a graph rich enough to trigger the curated hypothesis edges
    # (running habit ↔ calm, fajr ↔ discipline).
    for d, themes, emos, text in [
        ("2026-06-01", ["running", "health"], ["calm"], "(seed) a run by the river left me calm"),
        ("2026-06-02", ["running", "health"], ["calm"], "(seed) another run, calm after"),
        ("2026-06-03", ["faith", "discipline"], ["calm"], "(seed) prayed fajr, felt disciplined"),
        ("2026-06-04", ["faith", "discipline"], ["hope"], "(seed) fajr again, building discipline"),
    ]:
        _seed_entry(conn, date=d, themes=themes, emotions=emos, text=text)
    graph.store_seed_graph(conn)
    conn.close()

    edges = client.get("/insights/graph", params={"include_seeded": "true"}).json()["edges"]
    hyp = [e for e in edges if e["is_hypothesis"]]
    assert hyp, "expected at least one hypothesis edge from the seeded narrative"
    for e in hyp:
        assert e["type"] == "hypothesis"
        assert isinstance(e["label"], str) and e["label"].strip()
    for e in edges:
        if not e["is_hypothesis"]:
            assert e["type"] != "hypothesis"
            assert e["label"] is None


def test_is_seeded_filter(client):
    """Seeded graph rows are hidden by default and shown with include_seeded."""
    conn = db.get_or_create_db()
    _seed_entry(conn, date="2026-06-01", themes=["work", "stress"], emotions=["anxiety"], text="(seed) x")
    _seed_entry(conn, date="2026-06-02", themes=["work", "stress"], emotions=["anxiety"], text="(seed) y")
    graph.store_seed_graph(conn)
    conn.close()

    assert client.get("/insights/graph").json()["nodes"] == []  # live-only default
    assert client.get("/insights/graph", params={"include_seeded": "true"}).json()["nodes"]


def test_builder_node_types_and_evidence():
    """The builder derives all node kinds it can and ties each to real entries."""
    rows = [
        {"entry_id": "e1", "date": "2026-06-01", "text": "(seed) snapped at Daniel over the deadline",
         "themes": '["work","conflict"]', "emotions": '[{"name":"anxiety","intensity":0.6}]', "summary": "s"},
        {"entry_id": "e2", "date": "2026-06-02", "text": "(seed) a run by the river, prayed fajr",
         "themes": '["running","faith"]', "emotions": '[{"name":"calm","intensity":0.5}]', "summary": "s"},
        {"entry_id": "e3", "date": "2026-06-03", "text": "(seed) another deadline at work",
         "themes": '["work"]', "emotions": '[{"name":"anxiety","intensity":0.5}]', "summary": "s"},
    ]
    nodes, edges = graph.build_seed_graph(rows)
    by_label = {n.label: n for n in nodes}
    # Lexicon recovers person/place/goal/problem from the entry text, tied to entries.
    assert by_label["Daniel"].type == "person" and by_label["Daniel"].entries == ["e1"]
    assert by_label["The river"].type == "place"
    assert by_label["Praying fajr"].type == "goal"
    assert by_label["Deadline pressure"].type == "problem"
    assert set(by_label["Deadline pressure"].entries) == {"e1", "e3"}
    # work appears in e1 and e3 → a theme node carrying both as evidence.
    assert by_label["work"].type == "theme" and by_label["work"].entry_count == 2
    assert set(by_label["work"].entries) == {"e1", "e3"}
    # Every edge references real node ids.
    ids = {n.id for n in nodes}
    assert all(e.source in ids and e.target in ids for e in edges)
