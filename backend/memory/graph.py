"""L4 knowledge graph — the Phase-14 seeded builder + the §7.4 read shape.

# DEMO-STUB: replaced by the real L4 build_graph()
# ─────────────────────────────────────────────────────────────────────────────
# This module is the SEAM the real L4 graph builder plugs into later. Two jobs:
#
#   * :func:`build_seed_graph` / :func:`store_seed_graph` — derive a *seeded*
#     knowledge graph from the demo extractions (scripts/seed_demo.py) and persist
#     it to ``graph_nodes``/``graph_edges`` with ``is_seeded=1`` so it can be
#     pruned the day the real builder runs. The real builder writes ``is_seeded=0``
#     rows through the same ``db`` helpers; nothing downstream changes.
#   * :func:`read_graph` — return the exact EVA_MEMORY_ARCHITECTURE §7.4 payload
#     for ``GET /insights/graph``. This is the contract the real L4 must satisfy.
#
# Honesty (Phase 14): the bulk of the graph is derived from the *same* extracted
# data the mood chart uses — theme and emotion nodes come straight from the
# seeded extractions, with co-occurrence edges between concepts that genuinely
# appear in the same entries. Person/place/goal/problem nodes need fields the demo
# seed doesn't carry (the seeded extractions store only themes + emotions), so
# they are recovered by a tiny curated lexicon scanned over the seeded entry text
# — a stand-in for the entity/open-loop extraction the real L1 produces, and still
# tied to the actual entries that mention each term. Nothing is invented: every
# node and edge points at real seeded entries as its evidence.
# ─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from . import db

log = logging.getLogger("eva.memory.graph")

# The §7.4 enums, kept here so the builder and the validator agree on the contract.
NODE_TYPES = ("theme", "person", "place", "goal", "problem", "emotion")
EDGE_TYPES = ("co_occurrence", "temporal", "similarity", "hypothesis")

# Tuning for a readable ~25–30 node demo graph (see module docstring):
THEME_MIN_SUPPORT = 2  # auto-derived theme/emotion nodes need ≥2 entries to earn a place
EDGE_FLOOR = 0.5       # drop weak co-occurrence (overlap coefficient below this)
MAX_DEGREE = 6         # cap co-occurrence edges per node so the graph never hairballs

# Curated lexicon — a DEMO-STUB stand-in for entity / open-loop / goal extraction.
# Each (pattern, type, label) is scanned over the seeded entry text; the node's
# evidence is exactly the entries whose text matches. Labels are distinct from the
# theme/emotion labels so a concept never appears twice under two types.
_LEXICON: list[tuple[str, str, str]] = [
    (r"\bdaniel\b", "person", "Daniel"),
    (r"\b(?:mother|mum)\b", "person", "My mother"),
    (r"\briver\b", "place", "The river"),
    (r"\bbridge\b", "place", "The bridge"),
    (r"\bfajr\b", "goal", "Praying fajr"),
    (r"\b(?:run|ran|running)\b", "goal", "A running habit"),
    (r"\btemper\b", "goal", "Keeping my temper"),
    (r"\bscope\b", "problem", "Shifting project scope"),
    (r"\bdeadline\b", "problem", "Deadline pressure"),
    (r"self-doubt|not good at this", "problem", "Self-doubt at work"),
]
_LEXICON_COMPILED = [(re.compile(p, re.IGNORECASE), t, label) for p, t, label in _LEXICON]

# Curated hypothesis edges — the only model-*proposed* links (everything else is
# observed co-occurrence). Each names two concept labels that must already exist as
# nodes; the edge is rendered dashed with a confirm/dismiss affordance and is never
# presented as established fact (§7.4). (source_label, target_label, edge_label, weight).
_HYPOTHESES: list[tuple[str, str, str, float]] = [
    ("A running habit", "calm", "may be steadying you", 0.6),
    ("Praying fajr", "discipline", "may be reinforcing", 0.58),
    ("Deadline pressure", "anxiety", "may be a trigger for", 0.62),
]


@dataclass
class GraphNode:
    """One §7.4 node: a typed concept and the entries that evidence it."""

    id: str
    label: str
    type: str
    entry_count: int
    entries: list[str] = field(default_factory=list)
    is_seeded: bool = True


@dataclass
class GraphEdge:
    """One §7.4 edge. A hypothesis edge carries ``is_hypothesis=True`` + a label."""

    id: str
    source: str
    target: str
    type: str
    weight: float
    is_hypothesis: bool
    label: str | None
    entries: list[str] = field(default_factory=list)
    is_seeded: bool = True


def _json_list(raw: str | None) -> list:
    """Decode a JSON-list TEXT column, tolerating NULL/garbage (→ ``[]``)."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def build_seed_graph(rows) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Derive (nodes, edges) from seeded extraction rows. Pure — no DB writes.

    ``rows`` are :func:`db.seeded_extractions` rows (entry_id, text, themes JSON,
    emotions JSON). Theme and emotion nodes are read from the structured fields;
    person/place/goal/problem nodes come from the curated lexicon scan over the
    entry text. Edges are the co-occurrence between concepts that share entries
    (overlap coefficient, thresholded and degree-capped), plus the curated
    hypothesis edges. Every node/edge is ``is_seeded=True``.
    """
    # 1. Concept → set of entry-ids it appears in, with a type. Emotions are added
    #    first so a label shared by an emotion and a theme (e.g. "calm") resolves
    #    to a single emotion node (the entries are unioned, never duplicated).
    concept_entries: dict[str, set[str]] = defaultdict(set)
    concept_type: dict[str, str] = {}

    def _add(label: str, ctype: str, entry_id: str) -> None:
        if label not in concept_type:
            concept_type[label] = ctype
        concept_entries[label].add(entry_id)

    for r in rows:
        eid = r["entry_id"]
        for emo in _json_list(r["emotions"]):
            name = (emo.get("name") if isinstance(emo, dict) else str(emo)).strip().lower()
            if name:
                _add(name, "emotion", eid)
    for r in rows:
        eid = r["entry_id"]
        for theme in _json_list(r["themes"]):
            label = str(theme).strip().lower()
            if label:  # collides with an emotion of the same name → unions into it
                _add(label, concept_type.get(label, "theme"), eid)
    for r in rows:
        eid = r["entry_id"]
        text = (r["text"] or "")
        for pattern, ctype, label in _LEXICON_COMPILED:
            if pattern.search(text):
                _add(label, ctype, eid)

    # 2. Keep auto-derived theme/emotion nodes only with real support; curated
    #    lexicon nodes (person/place/goal/problem) are always kept.
    kept: dict[str, set[str]] = {}
    for label, entries in concept_entries.items():
        ctype = concept_type[label]
        if ctype in ("theme", "emotion") and len(entries) < THEME_MIN_SUPPORT:
            continue
        kept[label] = entries

    # 3. Materialise nodes with stable ids and a label → id map for the edges.
    nodes: list[GraphNode] = []
    node_id: dict[str, str] = {}
    for label in sorted(kept, key=lambda l: (-len(kept[l]), l)):
        nid = f"n-{uuid.uuid4()}"
        node_id[label] = nid
        nodes.append(
            GraphNode(
                id=nid,
                label=label,
                type=concept_type[label],
                entry_count=len(kept[label]),
                entries=sorted(kept[label]),
            )
        )

    # 4. Hypothesis edges first; record their (unordered) pairs so co-occurrence
    #    never duplicates a hypothesised link as a plain edge.
    edges: list[GraphEdge] = []
    hypothesis_pairs: set[frozenset[str]] = set()
    for src_label, tgt_label, edge_label, weight in _HYPOTHESES:
        if src_label not in node_id or tgt_label not in node_id:
            continue  # endpoint didn't survive the support filter — skip silently
        shared = kept[src_label] & kept[tgt_label]
        evidence = sorted(shared) if shared else sorted(kept[src_label])
        edges.append(
            GraphEdge(
                id=f"e-{uuid.uuid4()}",
                source=node_id[src_label],
                target=node_id[tgt_label],
                type="hypothesis",
                weight=weight,
                is_hypothesis=True,
                label=edge_label,
                entries=evidence,
            )
        )
        hypothesis_pairs.add(frozenset((src_label, tgt_label)))

    # 5. Co-occurrence candidates: every node pair sharing ≥1 entry, scored by the
    #    overlap coefficient and thresholded; then greedily kept strongest-first
    #    under a per-node degree cap so dense days don't fan out into a hairball.
    labels = list(kept)
    candidates: list[tuple[float, str, str, list[str]]] = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = labels[i], labels[j]
            if frozenset((a, b)) in hypothesis_pairs:
                continue
            shared = kept[a] & kept[b]
            if not shared:
                continue
            weight = len(shared) / min(len(kept[a]), len(kept[b]))
            if weight < EDGE_FLOOR:
                continue
            candidates.append((weight, a, b, sorted(shared)))

    candidates.sort(key=lambda c: -c[0])
    degree: dict[str, int] = defaultdict(int)
    for weight, a, b, shared in candidates:
        if degree[a] >= MAX_DEGREE or degree[b] >= MAX_DEGREE:
            continue
        degree[a] += 1
        degree[b] += 1
        edges.append(
            GraphEdge(
                id=f"e-{uuid.uuid4()}",
                source=node_id[a],
                target=node_id[b],
                type="co_occurrence",
                weight=round(weight, 2),
                is_hypothesis=False,
                label=None,
                entries=shared,
            )
        )

    return nodes, edges


def store_seed_graph(conn) -> tuple[int, int]:
    """Rebuild the seeded graph from seeded extractions and persist it. Idempotent.

    Clears any existing ``is_seeded=1`` graph rows, then writes the freshly built
    nodes and edges. Real (``is_seeded=0``) rows are never touched. Returns the
    (nodes, edges) counts written.
    """
    rows = db.seeded_extractions(conn)
    nodes, edges = build_seed_graph(rows)

    db.clear_seeded_graph(conn)
    for n in nodes:
        db.insert_graph_node(
            conn, id=n.id, label=n.label, type=n.type,
            entry_count=n.entry_count, entries=n.entries, is_seeded=True,
        )
    for e in edges:
        db.insert_graph_edge(
            conn, id=e.id, source=e.source, target=e.target, type=e.type,
            weight=e.weight, is_hypothesis=e.is_hypothesis, label=e.label,
            entries=e.entries, is_seeded=True,
        )
    conn.commit()
    log.info("stored seeded graph: %d node(s), %d edge(s)", len(nodes), len(edges))
    return len(nodes), len(edges)


def _node_payload(row) -> dict:
    """Map a ``graph_nodes`` row to the §7.4 node shape."""
    return {
        "id": row["id"],
        "label": row["label"],
        "type": row["type"],
        "entry_count": row["entry_count"],
        "entries": _json_list(row["entries"]),
    }


def _edge_payload(row) -> dict:
    """Map a ``graph_edges`` row to the §7.4 edge shape (is_hypothesis as a bool)."""
    return {
        "id": row["id"],
        "source": row["source"],
        "target": row["target"],
        "type": row["type"],
        "weight": row["weight"],
        "is_hypothesis": bool(row["is_hypothesis"]),
        "label": row["label"],
        "entries": _json_list(row["entries"]),
    }


def read_graph(conn, *, include_seeded: bool = False) -> dict:
    """Return the §7.4 ``{nodes, edges}`` payload for ``GET /insights/graph``.

    Live-only by default (``is_seeded=0``); ``include_seeded=True`` lifts that for
    the demo. Edges whose endpoints aren't in the returned node set are dropped, so
    the payload is always internally consistent (an edge never dangles) — this
    matters when the live filter returns a subset of nodes.
    """
    node_rows = db.graph_nodes_all(conn, include_seeded=include_seeded)
    edge_rows = db.graph_edges_all(conn, include_seeded=include_seeded)
    node_ids = {r["id"] for r in node_rows}
    return {
        "nodes": [_node_payload(r) for r in node_rows],
        "edges": [
            _edge_payload(r)
            for r in edge_rows
            if r["source"] in node_ids and r["target"] in node_ids
        ],
    }
