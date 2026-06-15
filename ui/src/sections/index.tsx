import { Button, EmptyState } from "../components";
import type { SectionId } from "../nav";
import { ChatScreen } from "../chat/ChatScreen";
import { JournalScreen } from "../journal/JournalScreen";
import {
  InsightsArt,
  LibraryArt,
  ProfileArt,
  SettingsArt,
} from "./illustrations";

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
  return (
    <EmptyState
      illustration={<LibraryArt />}
      eyebrow="Library"
      title="Hand Eva your books"
      description="Drop in PDFs, notes, or text and Eva can ground her answers in them — quoting the page, never inventing a source. Your library stays entirely on this Mac."
      action={
        <Button variant="primary" disabled>
          Add a document
        </Button>
      }
      footnote={comingIn("Phase 6")}
    />
  );
}

function InsightsSection() {
  return (
    <EmptyState
      illustration={<InsightsArt />}
      eyebrow="Insights"
      title="Patterns take a little time"
      description="As you write, Eva quietly notices the shape of things — your moods, the themes that recur, the threads worth pulling on. A few entries from now, this page starts to fill in."
      action={
        <Button variant="secondary" disabled>
          Nothing to show yet
        </Button>
      }
      footnote={comingIn("Phases 11–14")}
    />
  );
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
  return (
    <EmptyState
      illustration={<SettingsArt />}
      eyebrow="Settings"
      title="Make Eva yours"
      description="Vault location, voice, model, and appearance will live here. The light/dark switch in the top bar already works — the rest of the controls land alongside the features they belong to."
      action={
        <Button variant="secondary" disabled>
          More settings soon
        </Button>
      }
      footnote={comingIn("Phase 10")}
    />
  );
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
