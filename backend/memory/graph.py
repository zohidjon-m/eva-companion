"""L4 knowledge graph computed from real L1 extraction fields.

R10 keeps the public graph schema from the earlier insights surface, but replaces
the demo lexicon with deterministic nodes and edges from the structured L1 data.
The graph is a derived view: every node and edge carries evidence entry IDs, and
the endpoint computes the view from current extractions so edited entries are
reflected without a manual graph rebuild.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from typing import Iterable

from . import db

log = logging.getLogger("eva.memory.graph")

NODE_TYPES = ("theme", "person", "place", "goal", "problem", "emotion")
EDGE_TYPES = ("co_occurrence", "temporal", "similarity", "hypothesis")

TEMPORAL_WINDOW_DAYS = 21
SIMILARITY_THRESHOLD = 0.5
HYPOTHESIS_MIN_SUPPORT = 2
MAX_EDGES_PER_TYPE = 80

_WORD_RE = re.compile(r"[a-z0-9']+")
_STOPWORDS = frozenset(
    """
    a an and are as at be but by did do does doing for from had has have i if in
    into is it its me my of on or our so that the their them then they this to up
    was we were what when which who why will with you your
    """.split()
)

ConceptKey = tuple[str, str]


@dataclass
class GraphNode:
    """One graph node: a typed concept and the entry IDs that evidence it."""

    id: str
    label: str
    type: str
    entry_count: int
    entries: list[str] = field(default_factory=list)
    is_seeded: bool = False


@dataclass
class GraphEdge:
    """One graph edge with explicit evidence and a stable edge type."""

    id: str
    source: str
    target: str
    type: str
    weight: float
    is_hypothesis: bool
    label: str | None
    entries: list[str] = field(default_factory=list)
    is_seeded: bool = False


@dataclass
class _EntryConcepts:
    """Parsed graph concepts for one L1 extraction row."""

    entry_id: str
    day: str
    concepts: set[ConceptKey]


def _json_list(raw: str | None) -> list:
    """Decode a JSON-list TEXT column, tolerating NULL and malformed values."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _get(row, key: str, default=None):
    """Read ``key`` from a dict or sqlite row without assuming all fields exist."""
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _normalise(text: str) -> str:
    """Return a stable lowercase key for concept matching and IDs."""
    return " ".join(str(text or "").strip().split()).casefold()


def _display_label(text: str, *, lower: bool = False) -> str:
    """Return the readable graph label for a stored L1 value."""
    label = " ".join(str(text or "").strip().split())
    return label.lower() if lower else label


def _stable_id(prefix: str, *parts: str) -> str:
    """Make a short deterministic ID from stable graph identity parts."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:14]
    return f"{prefix}-{digest}"


def _tokens(text: str) -> set[str]:
    """Content-word token set used by deterministic similarity scoring."""
    return {t for t in _WORD_RE.findall(str(text).lower()) if t not in _STOPWORDS}


def _overlap(a: set[str], b: set[str]) -> float:
    """Return overlap coefficient, useful for short concept labels."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _day_number(day: str) -> int | None:
    """Convert an ISO date to an ordinal, returning None for invalid dates."""
    try:
        return date.fromisoformat(day).toordinal()
    except (TypeError, ValueError):
        return None


def build_seed_graph(rows) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Build a graph from seeded rows for legacy seed scripts and tests."""
    return build_graph(rows, is_seeded=True)


def build_graph(rows, *, is_seeded: bool = False) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Build graph nodes and evidence-backed edges from L1 extraction rows."""
    concept_entries: dict[ConceptKey, set[str]] = defaultdict(set)
    concept_labels: dict[ConceptKey, str] = {}
    entry_records: list[_EntryConcepts] = []

    def add(concepts: set[ConceptKey], entry_id: str, ctype: str, label: str) -> None:
        display = _display_label(label, lower=ctype in {"theme", "emotion"})
        norm = _normalise(display)
        if not display or not norm or ctype not in NODE_TYPES:
            return
        key = (ctype, norm)
        concepts.add(key)
        concept_entries[key].add(entry_id)
        concept_labels.setdefault(key, display)

    for row in rows:
        entry_id = str(_get(row, "entry_id") or "").strip()
        if not entry_id:
            continue
        concepts: set[ConceptKey] = set()

        for theme in _json_list(_get(row, "themes")):
            add(concepts, entry_id, "theme", str(theme))

        for emotion in _json_list(_get(row, "emotions")):
            name = emotion.get("name") if isinstance(emotion, dict) else emotion
            add(concepts, entry_id, "emotion", str(name))

        for entity in _json_list(_get(row, "entities")):
            if not isinstance(entity, dict):
                continue
            raw_type = str(entity.get("type") or "").strip().lower()
            ctype = raw_type if raw_type in {"person", "place"} else "theme"
            label = entity.get("name") or entity.get("normalized") or ""
            add(concepts, entry_id, ctype, str(label))

        for goal in _json_list(_get(row, "stated_goals")):
            text = goal.get("text") if isinstance(goal, dict) else goal
            add(concepts, entry_id, "goal", str(text))

        for loop in _json_list(_get(row, "open_loops")):
            text = loop.get("description") if isinstance(loop, dict) else loop
            add(concepts, entry_id, "problem", str(text))

        entry_records.append(
            _EntryConcepts(
                entry_id=entry_id,
                day=str(_get(row, "date") or ""),
                concepts=concepts,
            )
        )

    node_id = {key: _stable_id("n", key[0], key[1]) for key in concept_entries}
    nodes = [
        GraphNode(
            id=node_id[key],
            label=concept_labels[key],
            type=key[0],
            entry_count=len(entries),
            entries=sorted(entries),
            is_seeded=is_seeded,
        )
        for key, entries in sorted(
            concept_entries.items(),
            key=lambda item: (-len(item[1]), item[0][0], concept_labels[item[0]].lower()),
        )
    ]

    edges = _build_edges(entry_records, concept_entries, concept_labels, node_id, is_seeded)
    return nodes, edges


def _build_edges(
    entries: list[_EntryConcepts],
    concept_entries: dict[ConceptKey, set[str]],
    concept_labels: dict[ConceptKey, str],
    node_id: dict[ConceptKey, str],
    is_seeded: bool,
) -> list[GraphEdge]:
    """Build deterministic co-occurrence, temporal, similarity, and hypothesis edges."""
    co_edges = _co_occurrence_edges(entries, concept_entries, node_id, is_seeded)
    temporal_counts = _temporal_counts(entries)
    temporal_edges = _temporal_edges(temporal_counts, node_id, is_seeded)
    similarity_edges = _similarity_edges(concept_entries, concept_labels, node_id, is_seeded)
    hypothesis_edges = _hypothesis_edges(temporal_counts, node_id, is_seeded)

    return (
        _limit_edges(co_edges)
        + _limit_edges(temporal_edges)
        + _limit_edges(similarity_edges)
        + _limit_edges(hypothesis_edges)
    )


def _co_occurrence_edges(
    entries: list[_EntryConcepts],
    concept_entries: dict[ConceptKey, set[str]],
    node_id: dict[ConceptKey, str],
    is_seeded: bool,
) -> list[GraphEdge]:
    """Create association edges for concepts appearing in the same entry."""
    evidence: dict[tuple[ConceptKey, ConceptKey], set[str]] = defaultdict(set)
    for rec in entries:
        for a, b in combinations(sorted(rec.concepts), 2):
            evidence[(a, b)].add(rec.entry_id)

    edges: list[GraphEdge] = []
    for (a, b), entry_ids in evidence.items():
        if not entry_ids:
            continue
        denom = min(len(concept_entries[a]), len(concept_entries[b])) or 1
        weight = round(min(1.0, len(entry_ids) / denom), 2)
        edges.append(
            _edge(
                "co_occurrence", a, b, node_id, weight, sorted(entry_ids), False,
                None, is_seeded,
            )
        )
    return edges


def _temporal_counts(
    entries: list[_EntryConcepts],
) -> dict[tuple[ConceptKey, ConceptKey], tuple[int, set[str]]]:
    """Count concept pairs where one appears before another within the time window."""
    counts: dict[tuple[ConceptKey, ConceptKey], list] = {}
    dated = [(rec, _day_number(rec.day)) for rec in entries]
    for i, (earlier, earlier_day) in enumerate(dated):
        if earlier_day is None:
            continue
        for later, later_day in dated[i + 1:]:
            if later_day is None:
                continue
            delta = later_day - earlier_day
            if delta <= 0 or delta > TEMPORAL_WINDOW_DAYS:
                continue
            for src in earlier.concepts:
                for tgt in later.concepts:
                    if src == tgt:
                        continue
                    item = counts.setdefault((src, tgt), [0, set()])
                    item[0] += 1
                    item[1].update({earlier.entry_id, later.entry_id})
    return {pair: (count, evidence) for pair, (count, evidence) in counts.items()}


def _temporal_edges(
    counts: dict[tuple[ConceptKey, ConceptKey], tuple[int, set[str]]],
    node_id: dict[ConceptKey, str],
    is_seeded: bool,
) -> list[GraphEdge]:
    """Create directed temporal edges from ordered concept appearances."""
    edges: list[GraphEdge] = []
    for (src, tgt), (count, entry_ids) in counts.items():
        if not entry_ids:
            continue
        weight = round(min(1.0, count / 4), 2)
        edges.append(
            _edge(
                "temporal", src, tgt, node_id, weight, sorted(entry_ids), False,
                None, is_seeded,
            )
        )
    return edges


def _similarity_edges(
    concept_entries: dict[ConceptKey, set[str]],
    concept_labels: dict[ConceptKey, str],
    node_id: dict[ConceptKey, str],
    is_seeded: bool,
) -> list[GraphEdge]:
    """Create deterministic lexical-similarity edges between concept labels."""
    edges: list[GraphEdge] = []
    keys = sorted(concept_entries)
    for a, b in combinations(keys, 2):
        if a[1] == b[1]:
            continue
        score = _overlap(_tokens(concept_labels[a]), _tokens(concept_labels[b]))
        if score < SIMILARITY_THRESHOLD:
            continue
        evidence = sorted(concept_entries[a] | concept_entries[b])
        if not evidence:
            continue
        edges.append(
            _edge(
                "similarity", a, b, node_id, round(score, 2), evidence, False,
                None, is_seeded,
            )
        )
    return edges


def _hypothesis_edges(
    counts: dict[tuple[ConceptKey, ConceptKey], tuple[int, set[str]]],
    node_id: dict[ConceptKey, str],
    is_seeded: bool,
) -> list[GraphEdge]:
    """Create clearly marked hypothesis edges from repeated temporal evidence."""
    edges: list[GraphEdge] = []
    for (src, tgt), (count, entry_ids) in counts.items():
        if count < HYPOTHESIS_MIN_SUPPORT or not entry_ids:
            continue
        weight = round(min(1.0, 0.45 + count / 10), 2)
        edges.append(
            _edge(
                "hypothesis", src, tgt, node_id, weight, sorted(entry_ids), True,
                "may be related to", is_seeded,
            )
        )
    return edges


def _edge(
    edge_type: str,
    src: ConceptKey,
    tgt: ConceptKey,
    node_id: dict[ConceptKey, str],
    weight: float,
    entries: list[str],
    is_hypothesis: bool,
    label: str | None,
    is_seeded: bool,
) -> GraphEdge:
    """Materialise a graph edge with stable IDs and clamped weight."""
    source = node_id[src]
    target = node_id[tgt]
    return GraphEdge(
        id=_stable_id("e", edge_type, source, target),
        source=source,
        target=target,
        type=edge_type,
        weight=max(0.0, min(1.0, weight)),
        is_hypothesis=is_hypothesis,
        label=label,
        entries=entries,
        is_seeded=is_seeded,
    )


def _limit_edges(edges: Iterable[GraphEdge]) -> list[GraphEdge]:
    """Keep the strongest edges of one type so dense journals stay readable."""
    return sorted(
        edges,
        key=lambda e: (-e.weight, e.type, e.source, e.target),
    )[:MAX_EDGES_PER_TYPE]


def _payload(nodes: list[GraphNode], edges: list[GraphEdge]) -> dict:
    """Return the public ``/insights/graph`` payload shape."""
    node_ids = {n.id for n in nodes}
    return {
        "nodes": [
            {
                "id": n.id,
                "label": n.label,
                "type": n.type,
                "entry_count": n.entry_count,
                "entries": n.entries,
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": e.id,
                "source": e.source,
                "target": e.target,
                "type": e.type,
                "weight": e.weight,
                "is_hypothesis": e.is_hypothesis,
                "label": e.label,
                "entries": e.entries,
            }
            for e in edges
            if e.source in node_ids and e.target in node_ids and e.entries
        ],
    }


def _persist(conn, nodes: list[GraphNode], edges: list[GraphEdge], *, is_seeded: bool) -> None:
    """Persist a computed graph for compatibility with seed scripts."""
    if is_seeded:
        db.clear_seeded_graph(conn)
    else:
        conn.execute("DELETE FROM graph_edges WHERE is_seeded = 0")
        conn.execute("DELETE FROM graph_nodes WHERE is_seeded = 0")
    for n in nodes:
        db.insert_graph_node(
            conn, id=n.id, label=n.label, type=n.type,
            entry_count=n.entry_count, entries=n.entries, is_seeded=is_seeded,
        )
    for e in edges:
        db.insert_graph_edge(
            conn, id=e.id, source=e.source, target=e.target, type=e.type,
            weight=e.weight, is_hypothesis=e.is_hypothesis, label=e.label,
            entries=e.entries, is_seeded=is_seeded,
        )
    conn.commit()


def store_seed_graph(conn) -> tuple[int, int]:
    """Compute and persist the seeded graph for legacy demo tooling."""
    rows = [r for r in db.graph_extractions(conn, include_seeded=True) if bool(r["is_seeded"])]
    nodes, edges = build_graph(rows, is_seeded=True)
    _persist(conn, nodes, edges, is_seeded=True)
    log.info("stored seeded graph: %d node(s), %d edge(s)", len(nodes), len(edges))
    return len(nodes), len(edges)


def store_graph(conn) -> tuple[int, int]:
    """Compute and persist the live graph for scripts that expect stored rows."""
    nodes, edges = build_graph(db.graph_extractions(conn, include_seeded=False), is_seeded=False)
    _persist(conn, nodes, edges, is_seeded=False)
    log.info("stored live graph: %d node(s), %d edge(s)", len(nodes), len(edges))
    return len(nodes), len(edges)


def read_graph(conn, *, include_seeded: bool = False) -> dict:
    """Return the current evidence-backed graph computed directly from L1 rows."""
    nodes, edges = build_graph(
        db.graph_extractions(conn, include_seeded=include_seeded),
        is_seeded=False,
    )
    return _payload(nodes, edges)
