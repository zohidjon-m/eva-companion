import { useMemo, useState } from "react";
import { EmptyState } from "../components";
import { Icon } from "../components/Icon";
import { InsightsArt } from "../sections/illustrations";
import type { GraphEdge, GraphNode, NodeType } from "./graphApi";
import { useGraph } from "./useGraph";
import { useGraphLayout, VB_H, VB_W } from "./useGraphLayout";

/**
 * GraphView — the Phase-14 knowledge-graph surface.
 *
 * A force-directed graph of the concepts Eva has drawn from your entries: typed
 * nodes (themes, people, places, goals, problems, emotions) joined by association
 * edges. Selecting a node or edge opens an evidence panel showing the real
 * entries behind it. Hypothesis edges — Eva's *proposed* links, not established
 * fact — render dashed and carry a confirm/dismiss affordance, never appearing as
 * a plain edge (§7.4).
 *
 * Confirm/dismiss is local UI state for the demo (# DEMO-STUB): the real L4 will
 * persist a confirmation as an anchor and a dismissal as a pruned edge. Here,
 * confirming makes the edge solid and dismissing hides it — so the affordance is
 * real and reversible within the session.
 */

const NODE_TYPE_LABEL: Record<NodeType, string> = {
  theme: "Theme",
  person: "Person",
  place: "Place",
  goal: "Goal",
  problem: "Problem",
  emotion: "Emotion",
};

type Selection = { kind: "node"; id: string } | { kind: "edge"; id: string } | null;

function nodeRadius(n: GraphNode): number {
  return 7 + Math.min(7, Math.sqrt(n.entry_count) * 2.2);
}

export function GraphView() {
  const { graph, evidence, includeSeeded, loaded, error, setIncludeSeeded } = useGraph();
  const [selection, setSelection] = useState<Selection>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());

  const nodeById = useMemo(
    () => new Map(graph.nodes.map((n) => [n.id, n])),
    [graph.nodes],
  );
  const visibleEdges = useMemo(
    () => graph.edges.filter((e) => !dismissed.has(e.id)),
    [graph.edges, dismissed],
  );

  const { svgRef, positions, startDrag } = useGraphLayout(graph.nodes, visibleEdges);

  const selectedNode =
    selection?.kind === "node" ? nodeById.get(selection.id) ?? null : null;
  const selectedEdge =
    selection?.kind === "edge"
      ? graph.edges.find((e) => e.id === selection.id) ?? null
      : null;

  const isDimmed = (id: string): boolean => {
    if (!selection) return false;
    if (selection.kind === "node") {
      if (id === selection.id) return false;
      return !visibleEdges.some(
        (e) =>
          (e.source === selection.id && e.target === id) ||
          (e.target === selection.id && e.source === id),
      );
    }
    return selectedEdge ? id !== selectedEdge.source && id !== selectedEdge.target : false;
  };

  const hasGraph = graph.nodes.length > 0;

  return (
    <section className="insights__block">
      <header className="insights__head">
        <div>
          <h2 className="insights__title">Connections</h2>
          <p className="insights__sub">
            The threads Eva notices between what you write — drag a node, or tap one
            to see the entries behind it.
          </p>
        </div>
        <label className="insights__demo">
          <input
            type="checkbox"
            checked={includeSeeded}
            onChange={(e) => {
              setIncludeSeeded(e.target.checked);
              setSelection(null);
              setDismissed(new Set());
              setConfirmed(new Set());
            }}
          />
          <span>Demo data</span>
        </label>
      </header>

      {error ? (
        <p className="insights__note insights__note--error">
          Couldn't reach the journal just now. It's all still on this Mac — try again
          in a moment.
        </p>
      ) : !loaded ? (
        <div className="insights__loading" aria-hidden="true" />
      ) : !hasGraph ? (
        <EmptyState
          illustration={<InsightsArt />}
          eyebrow="Connections"
          title="The map is still drawing itself"
          description="As you write, Eva starts to see how your themes, people, and feelings connect. After a few weeks there's a web here to explore."
          footnote={includeSeeded ? undefined : <>Turn on “Demo data” to preview a sample graph.</>}
        />
      ) : (
        <div className="graph">
          <div className="graph__canvas">
            <svg
              ref={svgRef}
              className="graph__svg"
              viewBox={`0 0 ${VB_W} ${VB_H}`}
              role="img"
              aria-label="Knowledge graph of your themes, people and feelings"
              onClick={() => setSelection(null)}
            >
              {/* Edges first so nodes sit on top. */}
              {visibleEdges.map((e) => {
                const a = positions.get(e.source);
                const b = positions.get(e.target);
                if (!a || !b) return null;
                const isConfirmed = confirmed.has(e.id);
                const isHyp = e.is_hypothesis && !isConfirmed;
                const active = selectedEdge?.id === e.id;
                const dim =
                  selection !== null &&
                  !active &&
                  isDimmed(e.source) &&
                  isDimmed(e.target);
                const cls =
                  "graph__edge" +
                  (isHyp ? " graph__edge--hypothesis" : "") +
                  (active ? " graph__edge--active" : "") +
                  (dim ? " graph__edge--dim" : "");
                return (
                  <line
                    key={e.id}
                    className={cls}
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    strokeWidth={isHyp ? 1.6 : 1 + e.weight * 2}
                    onClick={(ev) => {
                      ev.stopPropagation();
                      setSelection({ kind: "edge", id: e.id });
                    }}
                  />
                );
              })}

              {/* Hypothesis markers — a clear "?" affordance at the edge midpoint. */}
              {visibleEdges.map((e) => {
                if (!e.is_hypothesis || confirmed.has(e.id)) return null;
                const a = positions.get(e.source);
                const b = positions.get(e.target);
                if (!a || !b) return null;
                const mx = (a.x + b.x) / 2;
                const my = (a.y + b.y) / 2;
                return (
                  <g
                    key={`m-${e.id}`}
                    className="graph__hypmark"
                    onClick={(ev) => {
                      ev.stopPropagation();
                      setSelection({ kind: "edge", id: e.id });
                    }}
                  >
                    <circle cx={mx} cy={my} r={8} className="graph__hypmark-bg" />
                    <text x={mx} y={my + 3.5} className="graph__hypmark-q" textAnchor="middle">
                      ?
                    </text>
                  </g>
                );
              })}

              {/* Nodes. */}
              {graph.nodes.map((n) => {
                const p = positions.get(n.id);
                if (!p) return null;
                const r = nodeRadius(n);
                const active = selectedNode?.id === n.id;
                const dim = isDimmed(n.id);
                return (
                  <g
                    key={n.id}
                    className={`graph__node graph__node--${n.type}${active ? " graph__node--active" : ""}${dim ? " graph__node--dim" : ""}`}
                    transform={`translate(${p.x} ${p.y})`}
                    onPointerDown={(ev) => startDrag(n.id, ev)}
                    onClick={(ev) => {
                      ev.stopPropagation();
                      setSelection({ kind: "node", id: n.id });
                    }}
                  >
                    <circle className="graph__node-dot" r={r} />
                    <text className="graph__node-label" x={0} y={r + 12} textAnchor="middle">
                      {n.label}
                    </text>
                  </g>
                );
              })}
            </svg>

            <Legend />
          </div>

          <EvidencePanel
            node={selectedNode}
            edge={selectedEdge}
            nodeById={nodeById}
            evidence={evidence}
            confirmed={selectedEdge ? confirmed.has(selectedEdge.id) : false}
            onConfirm={(id) => setConfirmed((s) => new Set(s).add(id))}
            onDismiss={(id) => {
              setDismissed((s) => new Set(s).add(id));
              setSelection(null);
            }}
            onClear={() => setSelection(null)}
          />
        </div>
      )}
    </section>
  );
}

const LEGEND_TYPES: NodeType[] = ["theme", "emotion", "person", "place", "goal", "problem"];

function Legend() {
  return (
    <div className="graph__legend" aria-hidden="true">
      {LEGEND_TYPES.map((t) => (
        <span key={t} className="graph__legend-item">
          <span className={`graph__legend-dot graph__node--${t}`} />
          {NODE_TYPE_LABEL[t]}
        </span>
      ))}
      <span className="graph__legend-item">
        <span className="graph__legend-dash" />
        Hypothesis
      </span>
    </div>
  );
}

function EvidencePanel({
  node,
  edge,
  nodeById,
  evidence,
  confirmed,
  onConfirm,
  onDismiss,
  onClear,
}: {
  node: GraphNode | null;
  edge: GraphEdge | null;
  nodeById: Map<string, GraphNode>;
  evidence: Map<string, import("./graphApi").EvidenceEntry>;
  confirmed: boolean;
  onConfirm: (id: string) => void;
  onDismiss: (id: string) => void;
  onClear: () => void;
}) {
  if (!node && !edge) {
    return (
      <aside className="graph__panel graph__panel--empty">
        <p className="graph__panel-hint">
          Select a node or a connection to see the entries it came from.
        </p>
      </aside>
    );
  }

  const entryIds = node ? node.entries : edge ? edge.entries : [];
  const title = node
    ? node.label
    : edge
      ? `${nodeById.get(edge.source)?.label ?? "?"} → ${nodeById.get(edge.target)?.label ?? "?"}`
      : "";

  return (
    <aside className="graph__panel">
      <header className="graph__panel-head">
        <div>
          {node && <span className={`graph__panel-tag graph__node--${node.type}`}>{NODE_TYPE_LABEL[node.type]}</span>}
          {edge && (
            <span className={`graph__panel-tag${edge.is_hypothesis && !confirmed ? " graph__panel-tag--hyp" : ""}`}>
              {edge.is_hypothesis && !confirmed ? "Hypothesis" : "Connection"}
            </span>
          )}
          <h3 className="graph__panel-title">{title}</h3>
        </div>
        <button className="graph__panel-close" onClick={onClear} aria-label="Close panel">
          <Icon name="close" size={16} />
        </button>
      </header>

      {edge?.is_hypothesis && (
        <div className="graph__hyp">
          <p className="graph__hyp-claim">
            Eva wonders whether <strong>{nodeById.get(edge.source)?.label}</strong>{" "}
            <em>{edge.label}</em> <strong>{nodeById.get(edge.target)?.label}</strong>.
          </p>
          {confirmed ? (
            <p className="graph__hyp-done">
              <Icon name="check" size={14} /> You confirmed this connection.
            </p>
          ) : (
            <>
              <p className="graph__hyp-note">
                This is a guess, not a fact — does it ring true for you?
              </p>
              <div className="graph__hyp-actions">
                <button className="graph__hyp-btn graph__hyp-btn--confirm" onClick={() => onConfirm(edge.id)}>
                  <Icon name="check" size={15} /> Confirm
                </button>
                <button className="graph__hyp-btn graph__hyp-btn--dismiss" onClick={() => onDismiss(edge.id)}>
                  <Icon name="close" size={15} /> Dismiss
                </button>
              </div>
            </>
          )}
        </div>
      )}

      <p className="graph__panel-count">
        {entryIds.length} {entryIds.length === 1 ? "entry" : "entries"} behind this
      </p>
      <ul className="graph__evidence">
        {entryIds.map((id) => {
          const ev = evidence.get(id);
          return (
            <li key={id} className="graph__evidence-item">
              <span className="graph__evidence-date">{ev?.date ?? "Entry"}</span>
              {ev?.summary && <span className="graph__evidence-text">{ev.summary}</span>}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
