import { useMemo } from "react";
import type { MoodPoint } from "./api";
import { moodWord } from "./mood";

/**
 * MoodArc — the hero visual of "Looking back": the real mood line across the
 * whole window, drawn in the same hand-built SVG style as the mood chart, but
 * split at the midpoint into an "earlier" and a "recent" half. Each half's
 * average is drawn as a dashed level with its plain word, so the lift (or dip)
 * between the two stretches is something you SEE, not read. Decorative-but-honest:
 * every point is a real entry; nulls are simply skipped so the arc stays smooth.
 */

const VB_W = 720;
const VB_H = 210;
const PAD = { left: 58, right: 18, top: 18, bottom: 24 };
const PW = VB_W - PAD.left - PAD.right;
const PH = VB_H - PAD.top - PAD.bottom;
const MIN = -5;
const MAX = 5;

const BANDS = [
  { at: 4, label: "Great" },
  { at: 0, label: "Okay" },
  { at: -4, label: "Rough" },
];

function dayNum(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Math.floor(Date.UTC(y, m - 1, d) / 86_400_000);
}

export function MoodArc({
  points,
  from,
  to,
  splitDate,
  avgA,
  avgB,
}: {
  points: MoodPoint[];
  from: string;
  to: string;
  splitDate: string;
  avgA: number | null;
  avgB: number | null;
}) {
  const lo = dayNum(from);
  const hi = dayNum(to);
  const span = Math.max(1, hi - lo);

  const xOf = (iso: string) => PAD.left + (Math.min(1, Math.max(0, (dayNum(iso) - lo) / span))) * PW;
  const yOf = (m: number) => PAD.top + ((MAX - m) / (MAX - MIN)) * PH;

  // Real, scored points in date order — nulls dropped so the hero line stays whole.
  const placed = useMemo(() => {
    return points
      .filter((p) => p.mood !== null)
      .map((p) => ({ x: xOf(p.date), y: yOf(p.mood as number) }))
      .sort((a, b) => a.x - b.x);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, from, to]);

  const splitX = xOf(splitDate);
  const baseline = PAD.top + PH;

  const line = placed.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = placed.length
    ? `M${placed[0].x.toFixed(1)},${baseline} ` +
      placed.map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ") +
      ` L${placed[placed.length - 1].x.toFixed(1)},${baseline} Z`
    : "";

  return (
    <svg
      className="growth__arc-svg"
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      role="img"
      aria-label="Your mood across the period, earlier half beside the recent half"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id="growthArcFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.26" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {/* Faint word bands. */}
      {BANDS.map((b) => (
        <g key={b.at}>
          <line className="growth__arc-grid" x1={PAD.left} x2={VB_W - PAD.right} y1={yOf(b.at)} y2={yOf(b.at)} />
          <text className="growth__arc-yl" x={PAD.left - 10} y={yOf(b.at) + 4} textAnchor="end">
            {b.label}
          </text>
        </g>
      ))}

      {/* The recent half gets a soft wash so the two stretches read as distinct. */}
      <rect
        className="growth__arc-recent"
        x={splitX}
        y={PAD.top}
        width={Math.max(0, VB_W - PAD.right - splitX)}
        height={PH}
      />

      {/* The mood arc itself. */}
      {area && <path className="growth__arc-area" d={area} fill="url(#growthArcFill)" />}
      {line && <path className="growth__arc-line" d={line} />}

      {/* Each half's average, drawn as a dashed level with its word. */}
      {avgA !== null && (
        <AvgLevel x1={PAD.left} x2={splitX} y={yOf(avgA)} word={moodWord(avgA)} anchor="start" labelX={PAD.left + 4} />
      )}
      {avgB !== null && (
        <AvgLevel x1={splitX} x2={VB_W - PAD.right} y={yOf(avgB)} word={moodWord(avgB)} anchor="end" labelX={VB_W - PAD.right - 4} accent />
      )}

      {/* Midpoint divider + the two stretch captions. */}
      <line className="growth__arc-split" x1={splitX} x2={splitX} y1={PAD.top} y2={baseline} />
      <text className="growth__arc-cap" x={(PAD.left + splitX) / 2} y={PAD.top - 5} textAnchor="middle">
        Earlier
      </text>
      <text className="growth__arc-cap" x={(splitX + VB_W - PAD.right) / 2} y={PAD.top - 5} textAnchor="middle">
        Recent
      </text>
    </svg>
  );
}

function AvgLevel({
  x1,
  x2,
  y,
  word,
  anchor,
  labelX,
  accent,
}: {
  x1: number;
  x2: number;
  y: number;
  word: string;
  anchor: "start" | "end";
  labelX: number;
  accent?: boolean;
}) {
  return (
    <g>
      <line
        className={`growth__arc-avg${accent ? " growth__arc-avg--now" : ""}`}
        x1={x1}
        x2={x2}
        y1={y}
        y2={y}
      />
      <text
        className={`growth__arc-avgl${accent ? " growth__arc-avgl--now" : ""}`}
        x={labelX}
        y={y - 6}
        textAnchor={anchor}
      >
        {word} on average
      </text>
    </g>
  );
}
