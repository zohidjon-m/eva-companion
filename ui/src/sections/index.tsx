import type { SectionId } from "../nav";
import { ChatScreen } from "../chat/ChatScreen";
import { JournalScreen } from "../journal/JournalScreen";
import { LibraryScreen } from "../library/LibraryScreen";
import { InsightsScreen } from "../insights/InsightsScreen";
import { SettingsScreen } from "../settings/SettingsScreen";
import { ProfileScreen } from "../profile/ProfileScreen";

/**
 * Section screens — one per nav item. In Phase 3 every section is an intentional
 * empty state: a bespoke illustration, real copy in Eva's voice, and an honest
 * note about which phase brings it to life. No lorem ipsum, no dead links.
 *
 * Later phases replace the body of the matching component with the real screen;
 * the registry at the bottom is what the shell renders against the active id.
 */

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
  // Phase 13: the real Profile surface — renders profile.md (the human view of
  // the structured profile.json behind the L3 seam), with an edit affordance that
  // saves corrections back via the §7.2 sync. Renders in the standard content
  // column under the page header (like Library/Insights/Settings). It owns its own
  // empty state when there is no profile yet, so it returns just its surface.
  return <ProfileScreen />;
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
