#!/usr/bin/env python3
"""Validate GET /insights/graph against the EVA_MEMORY_ARCHITECTURE §7.4 schema.

Phase 14 check: the graph endpoint is a contract the real L4 must satisfy later,
so this asserts the *endpoint's actual JSON* — not a hand-built dict — conforms to
§7.4 exactly:

  * top-level ``{"nodes": [...], "edges": [...]}``;
  * each node: id (str), label (str), type ∈ {theme, person, place, goal, problem,
    emotion}, entry_count (int ≥ 0), entries (list[str]);
  * each edge: id (str), source/target (str, referencing existing node ids), type ∈
    {co_occurrence, temporal, similarity, hypothesis}, weight (number in 0..1),
    is_hypothesis (bool), label (str|null), entries (list[str]);
  * the §7.4 rule that ties them together: ``type == "hypothesis"`` *iff*
    ``is_hypothesis is True`` (a hypothesis edge carries is_hypothesis: true and a
    non-null label; a non-hypothesis edge does not).

It runs hermetically: a throwaway temp vault is seeded so nothing touches the
user's data, the real FastAPI endpoint is called in-process (no server needed),
and BOTH the seeded graph and the empty (fresh-vault) response are validated. No
third-party schema library — the app ships offline, so the checks are hand-rolled.

Usage:
    backend/.venv/bin/python scripts/validate_graph.py
Exit code 0 = PASS, 1 = FAIL (with the offending paths printed).
"""

from __future__ import annotations

import os
import sys
import tempfile
from numbers import Number
from pathlib import Path

# Run from anywhere: make the backend package importable.
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND))

NODE_TYPES = {"theme", "person", "place", "goal", "problem", "emotion"}
EDGE_TYPES = {"co_occurrence", "temporal", "similarity", "hypothesis"}


class _Errors:
    """Collects validation failures so one run reports every problem at once."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, ok: bool, msg: str) -> bool:
        if not ok:
            self.items.append(msg)
        return ok


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def validate_graph(payload, *, label: str) -> list[str]:
    """Validate one ``/insights/graph`` payload against §7.4. Returns error strings."""
    err = _Errors()
    p = f"[{label}]"

    if not err.check(isinstance(payload, dict), f"{p} payload is not an object"):
        return err.items
    if not err.check("nodes" in payload and "edges" in payload, f"{p} missing 'nodes'/'edges'"):
        return err.items
    nodes, edges = payload["nodes"], payload["edges"]
    if not err.check(isinstance(nodes, list), f"{p} 'nodes' is not a list"):
        return err.items
    if not err.check(isinstance(edges, list), f"{p} 'edges' is not a list"):
        return err.items

    node_ids: set[str] = set()
    for i, n in enumerate(nodes):
        np = f"{p} nodes[{i}]"
        if not err.check(isinstance(n, dict), f"{np} is not an object"):
            continue
        err.check(isinstance(n.get("id"), str) and n["id"], f"{np}.id must be a non-empty string")
        err.check(isinstance(n.get("label"), str) and n["label"], f"{np}.label must be a non-empty string")
        err.check(n.get("type") in NODE_TYPES, f"{np}.type {n.get('type')!r} not in {sorted(NODE_TYPES)}")
        err.check(
            isinstance(n.get("entry_count"), int) and not isinstance(n.get("entry_count"), bool) and n["entry_count"] >= 0,
            f"{np}.entry_count must be a non-negative int",
        )
        err.check(_is_str_list(n.get("entries")), f"{np}.entries must be a list of strings")
        if isinstance(n.get("id"), str):
            node_ids.add(n["id"])

    for i, e in enumerate(edges):
        ep = f"{p} edges[{i}]"
        if not err.check(isinstance(e, dict), f"{ep} is not an object"):
            continue
        err.check(isinstance(e.get("id"), str) and e["id"], f"{ep}.id must be a non-empty string")
        err.check(e.get("source") in node_ids, f"{ep}.source {e.get('source')!r} is not a known node id")
        err.check(e.get("target") in node_ids, f"{ep}.target {e.get('target')!r} is not a known node id")
        err.check(e.get("type") in EDGE_TYPES, f"{ep}.type {e.get('type')!r} not in {sorted(EDGE_TYPES)}")
        weight = e.get("weight")
        err.check(
            isinstance(weight, Number) and not isinstance(weight, bool) and 0.0 <= float(weight) <= 1.0,
            f"{ep}.weight must be a number in 0..1 (got {weight!r})",
        )
        is_hyp = e.get("is_hypothesis")
        err.check(isinstance(is_hyp, bool), f"{ep}.is_hypothesis must be a bool")
        label_val = e.get("label")
        err.check(label_val is None or isinstance(label_val, str), f"{ep}.label must be a string or null")
        err.check(_is_str_list(e.get("entries")), f"{ep}.entries must be a list of strings")
        # §7.4: hypothesis type and the flag are two sides of the same coin.
        if isinstance(is_hyp, bool):
            err.check(
                (e.get("type") == "hypothesis") == is_hyp,
                f"{ep}: type=={e.get('type')!r} but is_hypothesis=={is_hyp} (must agree)",
            )
            if is_hyp:
                err.check(
                    isinstance(label_val, str) and label_val.strip(),
                    f"{ep}: a hypothesis edge must carry a human-readable label",
                )

    return err.items


def main() -> int:
    # Hermetic: a throwaway vault so the user's real data is never touched.
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["EVA_VAULT_DIR"] = str(Path(tmp) / "local_vault")

        from memory import db, graph

        conn = db.get_or_create_db()
        empty_payload = graph.read_graph(conn, include_seeded=True)  # fresh vault → empty

        # Seed believable demo data (John: real journals + profile + graph).
        seed_path = _BACKEND.parent / "scripts" / "seed_john.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("seed_john", seed_path)
        seed_mod = importlib.util.module_from_spec(spec)
        sys.argv = ["seed_john.py", "--no-embed"]
        spec.loader.exec_module(seed_mod)
        seed_mod.main()
        conn.close()

        # Validate the REAL endpoint output (in-process, no running server).
        from fastapi.testclient import TestClient
        from app import app

        client = TestClient(app)
        seeded = client.get("/insights/graph", params={"include_seeded": "true"}).json()
        live_empty = client.get("/insights/graph").json()  # default live-only on fresh seed-only vault

    errors: list[str] = []
    errors += validate_graph(empty_payload, label="fresh vault (read_graph)")
    errors += validate_graph(seeded, label="seeded endpoint")
    errors += validate_graph(live_empty, label="live-only endpoint")

    n_nodes, n_edges = len(seeded.get("nodes", [])), len(seeded.get("edges", []))
    n_hyp = sum(1 for e in seeded.get("edges", []) if e.get("is_hypothesis"))
    node_types = sorted({n.get("type") for n in seeded.get("nodes", [])})

    if errors:
        print("FAIL — /insights/graph does not conform to §7.4:")
        for e in errors:
            print(f"  • {e}")
        return 1

    print("PASS — /insights/graph conforms to EVA_MEMORY_ARCHITECTURE §7.4.")
    print(f"  seeded graph: {n_nodes} nodes ({', '.join(node_types)}), "
          f"{n_edges} edges ({n_hyp} hypothesis).")
    print("  fresh-vault and live-only responses validate as empty graphs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
