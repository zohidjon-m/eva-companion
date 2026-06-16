import { EmptyState } from "../components";
import { InsightsArt } from "../sections/illustrations";
import type { GrowthReport, PeriodSummary } from "./growthApi";
import { useGrowth } from "./useGrowth";

/**
 * GrowthView — the Phase-14 growth report.
 *
 * A descriptive comparison of two stretches of time: how much you wrote, the
 * average mood you noted, and which themes ran through each. It is framed as
 * reflection, never a verdict (System Design §11/§12) — the backend computes the
 * observations and the closing question; this view only presents them, adding no
 * judgment of its own. The user is the interpreter.
 */

export function GrowthView() {
  const { report, includeSeeded, loaded, error, setIncludeSeeded } = useGrowth();

  return (
    <section className="insights__block">
      <header className="insights__head">
        <div>
          <h2 className="insights__title">Looking back</h2>
          <p className="insights__sub">
            A description of two stretches of time, side by side — not a score, just
            what you wrote.
          </p>
        </div>
        <label className="insights__demo">
          <input
            type="checkbox"
            checked={includeSeeded}
            onChange={(e) => setIncludeSeeded(e.target.checked)}
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
      ) : !report || report.empty ? (
        <EmptyState
          illustration={<InsightsArt />}
          eyebrow="Looking back"
          title="Not enough history to look back on yet"
          description="Once you've been writing for a little while, Eva can lay two stretches of time side by side and describe what changed between them."
          footnote={includeSeeded ? undefined : <>Turn on “Demo data” to preview a sample report.</>}
        />
      ) : (
        <Report report={report} />
      )}
    </section>
  );
}

function Report({ report }: { report: GrowthReport }) {
  return (
    <div className="growth">
      <div className="growth__periods">
        <PeriodCard label="Earlier" period={report.period_a} />
        <div className="growth__arrow" aria-hidden="true">
          →
        </div>
        <PeriodCard label="More recent" period={report.period_b} />
      </div>

      <div className="growth__narrative">
        {report.narrative.map((line, i) => (
          <p key={i} className="growth__line">
            {line}
          </p>
        ))}
      </div>

      {(report.theme_shifts.emerged.length > 0 ||
        report.theme_shifts.faded.length > 0 ||
        report.theme_shifts.continued.length > 0) && (
        <div className="growth__shifts">
          <ShiftRow label="Running through both" themes={report.theme_shifts.continued} kind="continued" />
          <ShiftRow label="Newer" themes={report.theme_shifts.emerged} kind="emerged" />
          <ShiftRow label="Less lately" themes={report.theme_shifts.faded} kind="faded" />
        </div>
      )}

      <blockquote className="growth__question">{report.closing_question}</blockquote>
      <p className="growth__disclaimer">
        This is a description of what you wrote, not a judgment of how you're doing.
      </p>
    </div>
  );
}

function PeriodCard({ label, period }: { label: string; period: PeriodSummary }) {
  return (
    <div className="growth__card">
      <span className="growth__card-label">{label}</span>
      <span className="growth__card-range">
        {period.from} – {period.to}
      </span>
      <div className="growth__card-stats">
        <div className="growth__stat">
          <span className="growth__stat-num">{period.entry_count}</span>
          <span className="growth__stat-lbl">{period.entry_count === 1 ? "entry" : "entries"}</span>
        </div>
        <div className="growth__stat">
          <span className="growth__stat-num">
            {period.avg_mood === null ? "—" : period.avg_mood > 0 ? `+${period.avg_mood}` : period.avg_mood}
          </span>
          <span className="growth__stat-lbl">avg. mood</span>
        </div>
      </div>
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

function ShiftRow({
  label,
  themes,
  kind,
}: {
  label: string;
  themes: string[];
  kind: "continued" | "emerged" | "faded";
}) {
  if (themes.length === 0) return null;
  return (
    <div className="growth__shift">
      <span className="growth__shift-label">{label}</span>
      <div className="growth__shift-chips">
        {themes.map((t) => (
          <span key={t} className={`growth__chip growth__chip--${kind}`}>
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}
