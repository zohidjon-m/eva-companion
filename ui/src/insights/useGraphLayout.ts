import { useCallback, useEffect, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "./graphApi";

/**
 * useGraphLayout — a tiny, dependency-free force-directed layout (Phase 14).
 *
 * The app ships fully offline and avoids extra weight (the same reason MoodChart
 * is hand-built), so rather than pull in d3-force or Cytoscape we run a compact
 * Fruchterman–Reingold simulation here: nodes repel, edges pull, a gentle gravity
 * keeps everything on screen, and a cooling "temperature" lets the graph settle
 * and then stop (no perpetual animation eating CPU). Nodes are draggable; a
 * dragged node pins where you drop it. Layout is deterministic — initial
 * positions are a circle by index, no randomness — so the graph looks the same
 * each open and never jitters on re-render.
 */

export const VB_W = 640;
export const VB_H = 460;
const PAD = 28; // keep nodes off the very edge so labels stay readable

type Sim = { x: number; y: number; pinned: boolean };

export type GraphLayout = {
  svgRef: React.RefObject<SVGSVGElement>;
  /** Live node positions, keyed by node id (read during render). */
  positions: Map<string, Sim>;
  /** Begin dragging a node (call from its pointerdown handler). */
  startDrag: (id: string, e: React.PointerEvent) => void;
};

export function useGraphLayout(nodes: GraphNode[], edges: GraphEdge[]): GraphLayout {
  const svgRef = useRef<SVGSVGElement>(null);
  const sim = useRef<Map<string, Sim>>(new Map());
  const raf = useRef(0);
  const running = useRef(false);
  const restart = useRef<() => void>(() => {});
  const temp = useRef(VB_W * 0.1);
  const dragId = useRef<string | null>(null);
  const [, setTick] = useState(0);

  // Re-seed only when the node set changes (dismissing an edge must not relayout).
  const nodeKey = nodes.map((n) => n.id).join(",");
  const edgeKey = edges.map((e) => `${e.source}-${e.target}-${e.weight}`).join(",");

  // Map a pointer event to SVG user-space coordinates (handles viewBox + scaling).
  const toSvg = useCallback((e: React.PointerEvent | PointerEvent) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const local = pt.matrixTransform(ctm.inverse());
    return { x: local.x, y: local.y };
  }, []);

  const startDrag = useCallback(
    (id: string, e: React.PointerEvent) => {
      e.preventDefault();

      // Only treat this as a drag (and pin the node) once the pointer actually
      // moves — a plain click should still select without freezing the node.
      const onMove = (ev: PointerEvent) => {
        const node = sim.current.get(id);
        const loc = toSvg(ev);
        if (!node || !loc) return;
        dragId.current = id;
        node.pinned = true;
        node.x = Math.max(PAD, Math.min(VB_W - PAD, loc.x));
        node.y = Math.max(PAD, Math.min(VB_H - PAD, loc.y));
        temp.current = Math.max(temp.current, VB_W * 0.04); // reheat so neighbours adjust
        restart.current(); // resume the sim if it had cooled to a stop
        setTick((t) => t + 1);
      };
      const onUp = () => {
        dragId.current = null;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [toSvg],
  );

  useEffect(() => {
    if (nodes.length === 0) {
      sim.current = new Map();
      setTick((t) => t + 1);
      return;
    }

    // Deterministic initial placement: a circle by index.
    const m = new Map<string, Sim>();
    const r = Math.min(VB_W, VB_H) * 0.34;
    nodes.forEach((node, i) => {
      const ang = (i / nodes.length) * Math.PI * 2;
      m.set(node.id, { x: VB_W / 2 + Math.cos(ang) * r, y: VB_H / 2 + Math.sin(ang) * r, pinned: false });
    });
    sim.current = m;
    temp.current = VB_W * 0.1;

    const k = Math.sqrt((VB_W * VB_H) / nodes.length) * 0.62; // ideal node spacing
    const ids = nodes.map((n) => n.id);

    const step = () => {
      const pos = sim.current;
      const disp = new Map<string, { dx: number; dy: number }>();
      ids.forEach((id) => disp.set(id, { dx: 0, dy: 0 }));

      // Repulsion between every pair.
      for (let i = 0; i < ids.length; i++) {
        for (let j = i + 1; j < ids.length; j++) {
          const a = pos.get(ids[i])!;
          const b = pos.get(ids[j])!;
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let d = Math.hypot(dx, dy) || 0.01;
          const f = (k * k) / d;
          dx = (dx / d) * f;
          dy = (dy / d) * f;
          const da = disp.get(ids[i])!;
          const db = disp.get(ids[j])!;
          da.dx += dx;
          da.dy += dy;
          db.dx -= dx;
          db.dy -= dy;
        }
      }

      // Attraction along edges (stronger for heavier weights).
      for (const e of edges) {
        const u = pos.get(e.source);
        const v = pos.get(e.target);
        if (!u || !v) continue;
        let dx = u.x - v.x;
        let dy = u.y - v.y;
        const d = Math.hypot(dx, dy) || 0.01;
        const f = ((d * d) / k) * (0.4 + e.weight);
        dx = (dx / d) * f;
        dy = (dy / d) * f;
        disp.get(e.source)!.dx -= dx;
        disp.get(e.source)!.dy -= dy;
        disp.get(e.target)!.dx += dx;
        disp.get(e.target)!.dy += dy;
      }

      // Gravity toward the centre keeps detached clusters from drifting away.
      for (const id of ids) {
        const p = pos.get(id)!;
        disp.get(id)!.dx += (VB_W / 2 - p.x) * 0.012;
        disp.get(id)!.dy += (VB_H / 2 - p.y) * 0.012;
      }

      // Move each node, capped by the current temperature; pinned nodes hold.
      const t = temp.current;
      for (const id of ids) {
        const p = pos.get(id)!;
        if (p.pinned) continue;
        const dsp = disp.get(id)!;
        const len = Math.hypot(dsp.dx, dsp.dy) || 0.01;
        p.x += (dsp.dx / len) * Math.min(len, t);
        p.y += (dsp.dy / len) * Math.min(len, t);
        p.x = Math.max(PAD, Math.min(VB_W - PAD, p.x));
        p.y = Math.max(PAD, Math.min(VB_H - PAD, p.y));
      }

      temp.current = t * 0.96;
      setTick((tick) => tick + 1);
      if (temp.current > 0.6 || dragId.current) {
        raf.current = requestAnimationFrame(step);
      } else {
        running.current = false;
      }
    };

    restart.current = () => {
      if (!running.current) {
        running.current = true;
        raf.current = requestAnimationFrame(step);
      }
    };
    running.current = true;
    raf.current = requestAnimationFrame(step);
    return () => {
      running.current = false;
      cancelAnimationFrame(raf.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeKey, edgeKey]);

  return { svgRef, positions: sim.current, startDrag };
}
