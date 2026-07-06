import { useState } from "react";
import { EmptyState } from "../components";
import { InsightsArt } from "../sections/illustrations";
import { GraphView } from "./GraphView";
import { GrowthView } from "./GrowthView";
import { MoodChart } from "./MoodChart";
import { useInsightsMood, type MoodRange } from "./useInsightsMood";

/**
 * InsightsScreen — the Insights surface, now in three blocks (Phase 12 + 14):
 *
 *   • Mood   — the Phase-12 line/area chart (real plumbing: SQL over mood_series).
 *   - Connections - the R10 force-directed knowledge graph (§7.4).
 *   - Looking back - the R10 descriptive growth report (§11), never a verdict.
 *
 * A segmented control switches between them so the screen stays calm rather than a
 * long scroll. Each block owns its own data, demo toggle, and empty state, so a
 * fresh vault shows tasteful empty states throughout.
 */

const RANGES: MoodRange[] = [7, 30];

type Tab = "mood" | "graph" | "growth";
const TABS: { id: Tab; label: string }[] = [
  { id: "mood", label: "Mood" },
  { id: "graph", label: "Connections" },
  { id: "growth", label: "Looking back" },
];

export function InsightsScreen() {
  const [tab, setTab] = useState<Tab>("mood");

  return (
    <div className="insights">
      <nav className="seg insights__tabs" role="tablist" aria-label="Insights views">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`seg__btn${tab === t.id ? " seg__btn--on" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {/* All three blocks stay mounted; we only hide the inactive ones. This keeps
          the Connections force-layout settled instead of restarting (and "shaking")
          every time you switch back to it, and avoids refetching on each tab flip. */}
      <div hidden={tab !== "mood"}>
        <MoodBlock />
      </div>
      <div hidden={tab !== "graph"}>
        <GraphView />
      </div>
      <div hidden={tab !== "growth"}>
        <GrowthView />
      </div>
    </div>
  );
}

/**
 * MoodBlock — the Phase-12 mood chart, unchanged, extracted into its own component
 * so InsightsScreen can switch between the three blocks.
 */
function MoodBlock() {
  const mood = useInsightsMood();
  const hasPoints = mood.points.length > 0;
  const withMood = mood.points.filter((p) => p.mood !== null).length;

  return (
    <section className="insights__block">
      <header className="insights__head">
        <div>
          <h2 className="insights__title">Mood</h2>
          <p className="insights__sub">How the days have felt, drawn from what you've written.</p>
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
        </div>
      </header>

      {mood.error ? (
        <p className="insights__note insights__note--error">
          Couldn't reach the journal just now. It's all still on this computer — try again in a moment.
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
        />
      )}
    </section>
  );
}
