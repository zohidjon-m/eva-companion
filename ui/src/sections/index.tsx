import { Button, EmptyState } from "../components";
import type { SectionId } from "../nav";
import { ChatScreen } from "../chat/ChatScreen";
import { JournalScreen } from "../journal/JournalScreen";
import { LibraryScreen } from "../library/LibraryScreen";
import { InsightsScreen } from "../insights/InsightsScreen";
import { SettingsScreen } from "../settings/SettingsScreen";
import { ProfileArt } from "./illustrations";

/**
 * Section screens — one per nav item. In Phase 3 every section is an intentional
 * empty state: a bespoke illustration, real copy in Eva's voice, and an honest
 * note about which phase brings it to life. No lorem ipsum, no dead links.
 *
 * Later phases replace the body of the matching component with the real screen;
 * the registry at the bottom is what the shell renders against the active id.
 */

function comingIn(phase: string) {
  return <>Arriving in {phase}.</>;
}

function ChatSection() {
  // Phase 4: the real chat surface, streaming over WS /chat. It manages its own
  // full-height layout (scroll + pinned composer), so the shell renders it flush
  // rather than inside the centered content column with a page header.
  return <ChatScreen />;
}

function JournalSection() {
  // Phase 5: the real journaling surface. Like chat, it owns a full-height,
  // flush layout (a browse rail + a calm editor), so the shell renders it
  // without the centered content column and page header.
  return <JournalScreen />;
}

function LibrarySection() {
  // Phase 6: the real Library surface — drag-and-drop upload, ingest progress,
  // the document list with chunk counts + status, and remove. Renders inside the
  // standard content column (under the page header), unlike the flush chat/journal.
  return <LibraryScreen />;
}

function InsightsSection() {
  // Phase 12: the first real Insights block — a mood chart over the data Eva
  // already extracts. Renders in the standard content column under the page
  // header (like Library), so it returns just its own surface. Later phases
  // (14) add the graph and growth views beneath it.
  return <InsightsScreen />;
}

function ProfileSection() {
  return (
    <EmptyState
      illustration={<ProfileArt />}
      eyebrow="Profile"
      title="Eva is still getting to know you"
      description="Over time Eva builds a private picture of what you care about — your goals, your values, the people who matter. You'll always be able to read it, edit it, and delete any of it."
      action={
        <Button variant="secondary" disabled>
          No profile yet
        </Button>
      }
      footnote={comingIn("Phase 13")}
    />
  );
}

function SettingsSection() {
  // Phase 10: the real configuration surface — voice (on/off, speed, whisper
  // size), privacy (live offline guard + audit), the vault location, and model
  // status. Renders in the standard content column under the page header.
  return <SettingsScreen />;
}

/** Maps the active nav id to its screen. */
export const SECTIONS: Record<SectionId, () => JSX.Element> = {
  chat: ChatSection,
  journal: JournalSection,
  library: LibrarySection,
  insights: InsightsSection,
  profile: ProfileSection,
  settings: SettingsSection,
};
