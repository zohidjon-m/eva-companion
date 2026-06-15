import type { IconName } from "./components/Icon";

/**
 * The six sections of the app, in nav order. This is the single source of truth
 * for the sidebar, the routing state, and the document <title>. Adding the real
 * screens in later phases means editing the matching section component, never
 * this list.
 */

export type SectionId =
  | "chat"
  | "journal"
  | "library"
  | "insights"
  | "profile"
  | "settings";

export type NavItem = {
  id: SectionId;
  label: string;
  icon: IconName;
  /** One-line description shown under the section title in the content header. */
  blurb: string;
};

export const NAV_ITEMS: NavItem[] = [
  { id: "chat", label: "Chat", icon: "chat", blurb: "Talk things through with Eva." },
  { id: "journal", label: "Journal", icon: "journal", blurb: "A quiet place to write your day." },
  { id: "library", label: "Library", icon: "library", blurb: "Books and notes Eva can draw on." },
  { id: "insights", label: "Insights", icon: "insights", blurb: "Patterns Eva notices over time." },
  { id: "profile", label: "Profile", icon: "profile", blurb: "What Eva understands about you." },
  { id: "settings", label: "Settings", icon: "settings", blurb: "Make Eva yours." },
];
