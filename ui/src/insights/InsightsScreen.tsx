import { EmptyState } from "../components";
import { InsightsArt } from "../sections/illustrations";
import { MoodChart } from "./MoodChart";
import { useInsightsMood, type MoodRange } from "./useInsightsMood";

/**
 * InsightsScreen — the Phase-12 mood block, the first real Insights surface.
 *
 * A clean mood line/area chart with a 7-/30-day toggle and a hover summary per
 * entry. The data is real plumbing: the moods were extracted at capture time
 * (Phase 2) and read back with pure SQL (no model). A short history shows a
 * tasteful empty state rather than a lonely axis.
 *
 * The "Demo data" toggle flips the backend's ?include_seeded flag so a presenter
 * can show the believable seeded month (scripts/seed_demo.py); it's off by
 * default, so ordinary use only ever charts the user's own entries.
 */

const RANGES: MoodRange[] = [7, 30];

export function InsightsScreen() {
  const mood = useInsightsMood();
  const hasPoints = mood.points.length > 0;
  const withMood = mood.points.filter((p) => p.mood !== null).length;

  return (
    <div className="insights">
      <section className="insights__block">
        <header className="insights__head">
          <div>
            <h2 className="insights__title">Mood</h2>
            <p className="insights__sub">
              How the days have felt, drawn from what you've written.
            </p>
          </div>

          <div className="insights__controls">
            <div className="seg" role="group" aria-label="Time range">
              {RANGES.map((r) => (
                <button
                  key={r}
                  className={`seg__btn${mood.range === r ? " seg__btn--on" : ""}`}
                  onClick={() => mood.setRange(r)}
                  aria-pressed={mood.range === r}
                >
                  {r} days
                </button>
              ))}
            </div>

            <label className="insights__demo">
              <input
                type="checkbox"
                checked={mood.includeSeeded}
                onChange={(e) => mood.setIncludeSeeded(e.target.checked)}
              />
              <span>Demo data</span>
            </label>
          </div>
        </header>

        {mood.error ? (
          <p className="insights__note insights__note--error">
            Couldn't reach the journal just now. It's all still on this Mac — try
            again in a moment.
          </p>
        ) : !mood.loaded ? (
          <div className="insights__loading" aria-hidden="true" />
        ) : hasPoints ? (
          <>
            <MoodChart points={mood.points} from={mood.from} to={mood.to} />
            <p className="insights__note">
              {withMood} {withMood === 1 ? "entry" : "entries"} in the last {mood.range} days.
              {withMood < mood.points.length && " Days without a clear mood show as gaps."}
            </p>
          </>
        ) : (
          <EmptyState
            illustration={<InsightsArt />}
            eyebrow="Mood"
            title="Not much to chart just yet"
            description="As you write a few more entries, the shape of your moods starts to show here — the dips, the lifts, and the quiet stretches in between."
            footnote={
              mood.includeSeeded ? undefined : <>Turn on “Demo data” to preview a sample month.</>
            }
          />
        )}
      </section>
    </div>
  );
}
