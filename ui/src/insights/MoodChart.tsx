import { useMemo, useState } from "react";
import type { MoodPoint } from "./api";
import { moodWord } from "./mood";

/**
 * MoodChart — a bespoke SVG line/area chart of mood over time.
 *
 * Hand-built (no charting dependency — the app ships offline and avoids extra
 * weight, matching its tokens-only, no-web-font ethos). It honours the one hard
 * rule from §7.1: a NULL mood is a GAP — the line breaks there and no dot is
 * drawn — it is never plotted as zero. The line is split into segments at every
 * null so a quiet, un-scored day reads as absence, not as a neutral mood.
 *
 * Each entry is a dot; hovering one shows that day's date, mood, and summary.
 * The x-axis spans the selected window (`from`..`to`) so the gaps between dots
 * are calendar-accurate, not just index spacing.
 */

const VB_W = 720;
const VB_H = 300;
const PAD = { left: 64, right: 18, top: 20, bottom: 30 };
const PLOT_W = VB_W - PAD.left - PAD.right;
const PLOT_H = VB_H - PAD.top - PAD.bottom;
const MOOD_MIN = -5;
const MOOD_MAX = 5;

/**
 * The y-axis reads in plain words, not the raw −5…5 score: "how the day felt"
 * is what a person understands, not a number. Five evenly-spaced bands label the
 * scale; `moodWord` maps any single mood value to the same vocabulary for the
 * hover tooltip, so the axis and the tooltip always speak the same language.
 */
const MOOD_BANDS: { at: number; label: string }[] = [
  { at: 4, label: "Great" },
  { at: 2, label: "Good" },
  { at: 0, label: "Okay" },
  { at: -2, label: "Low" },
  { at: -4, label: "Rough" },
];

/** Days since the unix epoch for a YYYY-MM-DD string (UTC, so no tz day-shift). */
function dayNumber(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Math.floor(Date.UTC(y, m - 1, d) / 86_400_000);
}

/** Format YYYY-MM-DD as a short axis/tooltip label, e.g. "Jun 3". */
function shortDate(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1];
  return `${month} ${d}`;
}

type Placed = MoodPoint & { x: number; y: number | null; xFrac: number; yFrac: number };

export function MoodChart({
  points,
  from,
  to,
}: {
  points: MoodPoint[];
  from: string;
  to: string;
}) {
  const [hover, setHover] = useState<number | null>(null);

  const placed: Placed[] = useMemo(() => {
    const lo = dayNumber(from);
    const hi = dayNumber(to);
    const span = Math.max(1, hi - lo);
    return points.map((p) => {
      const xFrac = hi === lo ? 0.5 : Math.min(1, Math.max(0, (dayNumber(p.date) - lo) / span));
      const x = PAD.left + xFrac * PLOT_W;
      if (p.mood === null) {
        return { ...p, x, y: null, xFrac, yFrac: 0 };
      }
      const moodFrac = (MOOD_MAX - p.mood) / (MOOD_MAX - MOOD_MIN);
      const y = PAD.top + moodFrac * PLOT_H;
      return { ...p, x, y, xFrac, yFrac: y / VB_H };
    });
  }, [points, from, to]);

  // Split into runs of consecutive non-null points; a null breaks the line.
  const segments: Placed[][] = useMemo(() => {
    const runs: Placed[][] = [];
    let cur: Placed[] = [];
    for (const p of placed) {
      if (p.y === null) {
        if (cur.length) runs.push(cur);
        cur = [];
      } else {
        cur.push(p);
      }
    }
    if (cur.length) runs.push(cur);
    return runs;
  }, [placed]);

  const yForMood = (m: number) => PAD.top + ((MOOD_MAX - m) / (MOOD_MAX - MOOD_MIN)) * PLOT_H;
  const zeroY = yForMood(0);

  // 4 evenly spaced date ticks across the window (label the calendar, not entries).
  const xTicks = useMemo(() => {
    const lo = dayNumber(from);
    const hi = dayNumber(to);
    const ticks: { x: number; label: string }[] = [];
    const n = 4;
    for (let i = 0; i <= n; i++) {
      const frac = i / n;
      const dayNum = Math.round(lo + frac * (hi - lo));
      const iso = new Date(dayNum * 86_400_000).toISOString().slice(0, 10);
      ticks.push({ x: PAD.left + frac * PLOT_W, label: shortDate(iso) });
    }
    return ticks;
  }, [from, to]);

  const dots = placed.filter((p) => p.y !== null);
  const hovered = hover !== null ? placed[hover] : null;

  return (
    <div className="moodchart">
      <svg
        className="moodchart__svg"
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        role="img"
        aria-label="Mood over time"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <linearGradient id="moodArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {/* Horizontal gridlines + word labels (Great / Good / Okay / Low / Rough). */}
        {MOOD_BANDS.map(({ at, label }) => (
          <g key={at}>
            <line
              className={at === 0 ? "moodchart__axis" : "moodchart__grid"}
              x1={PAD.left}
              x2={VB_W - PAD.right}
              y1={yForMood(at)}
              y2={yForMood(at)}
            />
            <text className="moodchart__ylabel" x={PAD.left - 10} y={yForMood(at) + 4} textAnchor="end">
              {label}
            </text>
          </g>
        ))}

        {/* x-axis date ticks. */}
        {xTicks.map((t, i) => (
          <text key={i} className="moodchart__xlabel" x={t.x} y={VB_H - 8} textAnchor="middle">
            {t.label}
          </text>
        ))}

        {/* Area + line, one path per unbroken run (gaps at nulls). */}
        {segments.map((seg, i) => {
          const line = seg.map((p, j) => `${j === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
          const area =
            `M${seg[0].x},${zeroY} ` +
            seg.map((p) => `L${p.x},${p.y}`).join(" ") +
            ` L${seg[seg.length - 1].x},${zeroY} Z`;
          return (
            <g key={i}>
              <path className="moodchart__area" d={area} fill="url(#moodArea)" />
              <path className="moodchart__line" d={line} />
            </g>
          );
        })}

        {/* Dots — one per entry. The invisible halo is the generous hover target. */}
        {dots.map((p) => {
          const idx = placed.indexOf(p);
          const active = hover === idx;
          return (
            <g key={p.entry_id}>
              <circle
                className={`moodchart__dot${p.mood! < 0 ? " moodchart__dot--low" : ""}${active ? " moodchart__dot--active" : ""}`}
                cx={p.x}
                cy={p.y!}
                r={active ? 5.5 : 4}
              />
              <circle
                className="moodchart__hit"
                cx={p.x}
                cy={p.y!}
                r={12}
                onMouseEnter={() => setHover(idx)}
                onMouseLeave={() => setHover((h) => (h === idx ? null : h))}
              />
            </g>
          );
        })}
      </svg>

      {hovered && hovered.y !== null && (
        <div
          className={`moodchart__tip${hovered.yFrac < 0.28 ? " moodchart__tip--below" : ""}`}
          style={{
            left: `${(hovered.x / VB_W) * 100}%`,
            top: `${(hovered.y / VB_H) * 100}%`,
          }}
        >
          <div className="moodchart__tip-head">
            <span className="moodchart__tip-date">{shortDate(hovered.date)}</span>
            <span className="moodchart__tip-mood">{moodWord(hovered.mood!)}</span>
          </div>
          {hovered.summary && <p className="moodchart__tip-summary">{hovered.summary}</p>}
        </div>
      )}
    </div>
  );
}
