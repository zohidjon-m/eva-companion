import { useEffect, useState } from "react";
import { EmptyState } from "../components";
import { InsightsArt } from "../sections/illustrations";
import { fetchMood, type MoodPoint } from "./api";
import type { GrowthReport, PeriodSummary } from "./growthApi";
import { moodWord } from "./mood";
import { MoodArc } from "./MoodArc";
import { useGrowth } from "./useGrowth";

/**
 * GrowthView — the "Looking back" report.
 *
 * It lays the first half of your history beside the more recent half and says, in
 * plain words, how your mood moved and which themes came and went. It is framed as
 * reflection, never a verdict (System Design §11/§12): the headline describes the
 * direction of change, the cards describe each stretch, and it closes on an open
 * question — the user is the interpreter. Mood is shown in the same words as the
 * mood chart, never a raw score, so the two screens read consistently.
 */

export function GrowthView() {
  const { report, loaded, error } = useGrowth();

  // The real mood points across the report's window, for the hero arc. Fetched
  // once the report (and thus its date bounds) is known; a failure just leaves the
  // arc empty (the rest of the report still renders).
  const [points, setPoints] = useState<MoodPoint[]>([]);
  useEffect(() => {
    if (!report || report.empty) return;
    let cancelled = false;
    fetchMood({ from: report.period_a.from, to: report.period_b.to, includeSeeded: false })
      .then((d) => {
        if (!cancelled) setPoints(d.points);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [report]);

  return (
    <section className="insights__block">
      <header className="insights__head">
        <div>
          <h2 className="insights__title">Looking back</h2>
          <p className="insights__sub">
            Your earlier weeks beside your recent ones — how your mood moved, and
            what you were writing about in each.
          </p>
        </div>
      </header>

      {error ? (
        <p className="insights__note insights__note--error">
          Couldn't reach the journal just now. It's all still on this computer — try again
          in a moment.
        </p>
      ) : !loaded ? (
        <div className="insights__loading" aria-hidden="true" />
      ) : !report || report.empty ? (
        <EmptyState
          illustration={<InsightsArt />}
          eyebrow="Looking back"
          title="Not enough history to look back on yet"
          description="Once you've been writing for a little while, Eva can lay two stretches of time side by side and describe what changed between them."
        />
      ) : (
        <Report report={report} points={points} />
      )}
    </section>
  );
}

/** Direction words for the mood headline — descriptive, never a verdict. */
function moodJourney(a: number | null, b: number | null, change: number | null) {
  if (a === null || b === null || change === null) {
    return {
      verb: "is hard to compare",
      note: "Some days in one stretch had no noted mood, so there isn't a clear average to set side by side.",
    };
  }
  if (change >= 0.5) return { verb: "lifted", note: "Your noted mood trended up across this stretch." };
  if (change <= -0.5) return { verb: "dipped", note: "Your noted mood trended down across this stretch." };
  return { verb: "held about steady", note: "Your noted mood stayed roughly level across this stretch." };
}

function Report({ report, points }: { report: GrowthReport; points: MoodPoint[] }) {
  const a = report.period_a.avg_mood;
  const b = report.period_b.avg_mood;
  const journey = moodJourney(a, b, report.mood_delta.change);
  const hasShifts =
    report.theme_shifts.emerged.length > 0 ||
    report.theme_shifts.faded.length > 0 ||
    report.theme_shifts.continued.length > 0;

  return (
    <div className="growth">
      {/* Hero: the headline + the real mood arc, split into earlier vs recent. */}
      <div className="growth__hero">
        <div className="growth__hero-head">
          <p className="growth__hero-kicker">Over the last {spanDays(report)} days</p>
          <h3 className="growth__hero-title">
            Your mood <span className="growth__hero-verb">{journey.verb}</span>
          </h3>
          {a !== null && b !== null && (
            <div className="growth__journey-track">
              <span className="growth__journey-word">{moodWord(a)}</span>
              <span className="growth__journey-arrow" aria-hidden="true">→</span>
              <span className="growth__journey-word growth__journey-word--now">{moodWord(b)}</span>
            </div>
          )}
          <p className="growth__hero-note">{journey.note}</p>
        </div>
        <MoodArc
          points={points}
          from={report.period_a.from}
          to={report.period_b.to}
          splitDate={report.period_b.from}
          avgA={a}
          avgB={b}
        />
      </div>

      <div className="growth__periods">
        <PeriodCard label="Earlier weeks" period={report.period_a} />
        <PeriodCard label="Recent weeks" period={report.period_b} accent />
      </div>

      {hasShifts && (
        <div className="growth__themes">
          <p className="growth__themes-title">What you were writing about</p>
          <div className="growth__lanes">
            <ThemeLane
              label="Faded back"
              hint="more earlier"
              themes={report.theme_shifts.faded}
              kind="faded"
            />
            <ThemeLane
              label="Stayed with you"
              hint="both stretches"
              themes={report.theme_shifts.continued}
              kind="continued"
            />
            <ThemeLane
              label="Came forward"
              hint="more lately"
              themes={report.theme_shifts.emerged}
              kind="emerged"
            />
          </div>
        </div>
      )}

      <blockquote className="growth__question">{report.closing_question}</blockquote>
      <p className="growth__disclaimer">
        This is a description of what you wrote, not a judgment of how you're doing.
      </p>
    </div>
  );
}

/** A horizontal Rough→Great meter with a marker at the average mood. */
function MoodMeter({ mood, accent }: { mood: number | null; accent?: boolean }) {
  if (mood === null) {
    return <div className="growth__meter growth__meter--empty">no mood noted</div>;
  }
  // Map −5…5 → 0…100% along the track.
  const pct = Math.min(100, Math.max(0, ((mood - -5) / 10) * 100));
  return (
    <div className="growth__meter" role="img" aria-label={`Average mood: ${moodWord(mood)}`}>
      <div className="growth__meter-track">
        <span
          className={`growth__meter-dot${accent ? " growth__meter-dot--now" : ""}`}
          style={{ left: `${pct}%` }}
        />
      </div>
      <div className="growth__meter-scale" aria-hidden="true">
        <span>Rough</span>
        <span>Great</span>
      </div>
    </div>
  );
}

function PeriodCard({
  label,
  period,
  accent,
}: {
  label: string;
  period: PeriodSummary;
  accent?: boolean;
}) {
  const mood = period.avg_mood;
  return (
    <div className={`growth__card${accent ? " growth__card--now" : ""}`}>
      <div className="growth__card-top">
        <span className="growth__card-label">{label}</span>
        <span className="growth__card-range">
          {prettyDate(period.from)} – {prettyDate(period.to)}
        </span>
      </div>
      <span className="growth__card-mood">{mood === null ? "—" : moodWord(mood)}</span>
      <MoodMeter mood={mood} accent={accent} />
      <span className="growth__card-count">
        {period.entry_count} {period.entry_count === 1 ? "entry" : "entries"}
      </span>
      {period.top_themes.length > 0 && (
        <div className="growth__card-themes">
          {period.top_themes.map((t) => (
            <span key={t.theme} className="growth__chip">
              {t.theme}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ThemeLane({
  label,
  hint,
  themes,
  kind,
}: {
  label: string;
  hint: string;
  themes: string[];
  kind: "continued" | "emerged" | "faded";
}) {
  return (
    <div className={`growth__lane growth__lane--${kind}`}>
      <div className="growth__lane-head">
        <span className="growth__lane-label">{label}</span>
        <span className="growth__lane-hint">{hint}</span>
      </div>
      <div className="growth__lane-chips">
        {themes.length === 0 ? (
          <span className="growth__lane-none">—</span>
        ) : (
          themes.map((t) => (
            <span key={t} className={`growth__chip growth__chip--${kind}`}>
              {t}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

/** Inclusive day-count of the whole window, for the hero kicker. */
function spanDays(report: GrowthReport): number {
  const d = (iso: string) => {
    const [y, m, dd] = iso.split("-").map(Number);
    return Math.floor(Date.UTC(y, m - 1, dd) / 86_400_000);
  };
  return d(report.period_b.to) - d(report.period_a.from) + 1;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-05-16" → "May 16" — a friendlier range label than the ISO date. */
function prettyDate(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return m && d ? `${MONTHS[m - 1]} ${d}` : iso;
}
