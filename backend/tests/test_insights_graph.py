"""R10 - GET /insights/graph from real structured L1 extraction fields."""

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


def _entry(
    conn,
    *,
    date,
    themes=None,
    emotions=None,
    entities=None,
    stated_goals=None,
    open_loops=None,
    text="journal text",
    summary="a real reflection",
    is_seeded=False,
):
    """Insert one done extraction with the structured fields R10 graphs read."""
    entry_id = str(uuid.uuid4())
    db.insert_entry(
        conn,
        id=entry_id,
        date=date,
        type="journal",
        text=text,
        word_count=len(text.split()),
        created_at=f"{date}T21:30:00",
        is_seeded=is_seeded,
    )
    db.create_pending_extraction(conn, entry_id)
    db.finalize_extraction(
        conn,
        entry_id,
        mood=0,
        emotions=emotions or [],
        entities=entities or [],
        themes=themes or [],
        events=[],
        stated_goals=stated_goals or [],
        behaviors=[],
        decisions=[],
        open_loops=open_loops or [],
        self_judgments=[],
        summary=summary,
        extracted_at=f"{date}T21:30:01",
    )
    return entry_id


def test_empty_vault_returns_empty_graph(client):
    resp = client.get("/insights/graph")
    assert resp.status_code == 200
    assert resp.json() == {"nodes": [], "edges": []}


def test_graph_nodes_come_from_structured_l1_fields(client):
    conn = db.get_or_create_db()
    eid = _entry(
        conn,
        date="2026-06-01",
        themes=["work"],
        emotions=[{"name": "anxiety", "intensity": 0.5}],
        entities=[
            {"name": "Daniel", "type": "person", "normalized": "daniel"},
            {"name": "Library", "type": "place", "normalized": "library"},
            {"name": "Apollo", "type": "project", "normalized": "apollo"},
        ],
        stated_goals=[{"text": "exercise regularly", "is_new": True}],
        open_loops=[{"description": "finish the report", "status": "open"}],
        text="No unstructured lexicon should be needed here.",
    )
    conn.close()

    payload = client.get("/insights/graph").json()
    by_label = {(n["label"], n["type"]): n for n in payload["nodes"]}

    assert ("work", "theme") in by_label
    assert ("anxiety", "emotion") in by_label
    assert ("Daniel", "person") in by_label
    assert ("Library", "place") in by_label
    assert ("apollo", "theme") in by_label  # project entities fold into theme.
    assert ("exercise regularly", "goal") in by_label
    assert ("finish the report", "problem") in by_label
    assert all(n["entries"] == [eid] for n in by_label.values())


def test_edges_are_typed_evidence_backed_and_include_r10_edge_types(client):
    conn = db.get_or_create_db()
    _entry(conn, date="2026-06-01", themes=["work", "deadline"])
    _entry(
        conn,
        date="2026-06-02",
        themes=["exercise plan"],
        stated_goals=[{"text": "exercise regularly", "is_new": True}],
    )
    _entry(conn, date="2026-06-03", themes=["stress"])
    _entry(conn, date="2026-06-04", emotions=[{"name": "anxiety", "intensity": 0.7}])
    _entry(conn, date="2026-06-05", themes=["stress"])
    _entry(conn, date="2026-06-06", emotions=[{"name": "anxiety", "intensity": 0.8}])
    conn.close()

    payload = client.get("/insights/graph").json()
    node_ids = {n["id"] for n in payload["nodes"]}
    edge_types = {e["type"] for e in payload["edges"]}

    assert {"co_occurrence", "temporal", "similarity", "hypothesis"} <= edge_types
    for node in payload["nodes"]:
        assert node["type"] in NODE_TYPES
        assert node["entries"], node
    for edge in payload["edges"]:
        assert edge["type"] in EDGE_TYPES
        assert edge["source"] in node_ids and edge["target"] in node_ids
        assert 0 <= edge["weight"] <= 1
        assert edge["entries"], edge
        if edge["is_hypothesis"]:
            assert edge["type"] == "hypothesis"
            assert edge["label"] == "may be related to"
        else:
            assert edge["type"] != "hypothesis"
            assert edge["label"] is None


def test_seeded_filter_is_computed_on_read(client):
    conn = db.get_or_create_db()
    _entry(conn, date="2026-06-01", themes=["seed only"], is_seeded=True)
    conn.close()

    assert client.get("/insights/graph").json()["nodes"] == []
    assert client.get("/insights/graph", params={"include_seeded": "true"}).json()["nodes"]


def test_builder_does_not_recover_nodes_from_text_lexicons():
    rows = [
        {
            "entry_id": "e1",
            "date": "2026-06-01",
            "text": "I talked to Daniel by the river about a deadline.",
            "themes": '["work"]',
            "emotions": "[]",
            "entities": "[]",
            "stated_goals": "[]",
            "open_loops": "[]",
            "summary": "s",
        }
    ]
    nodes, _ = graph.build_seed_graph(rows)
    labels = {n.label for n in nodes}
    assert "work" in labels
    assert "Daniel" not in labels
    assert "The river" not in labels
    assert "Deadline pressure" not in labels
