/**
 * Section illustrations — one bespoke line drawing per empty state.
 *
 * Larger and softer than the nav glyphs (Icon.tsx): a 96×96 viewBox, accent
 * stroke, and faint accent-soft fills for depth. They share a visual language
 * so the six empty screens feel like one product, not stock art. All inline
 * SVG — nothing loads over the network.
 */

const common = {
  viewBox: "0 0 96 96",
  fill: "none" as const,
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function ChatArt() {
  return (
    <svg {...common} width="100%" height="100%">
      <path
        d="M16 24a8 8 0 0 1 8-8h34a8 8 0 0 1 8 8v16a8 8 0 0 1-8 8H34l-12 11V48a8 8 0 0 1-6-8z"
        fill="var(--accent-soft)"
      />
      <path d="M28 28h22M28 36h14" />
      <circle cx="70" cy="60" r="14" fill="var(--surface-raised)" />
      <path d="M64 60h12M70 54v12" />
    </svg>
  );
}

export function JournalArt() {
  return (
    <svg {...common} width="100%" height="100%">
      <path d="M20 20h30a8 8 0 0 1 8 8v44l-8-5-8 5V28a8 8 0 0 0-8-8H20z" fill="var(--accent-soft)" />
      <path d="M20 20a8 8 0 0 1 8 8v44a8 8 0 0 0-8-5" />
      <path d="M30 34h16M30 44h12" />
      <path d="M72 24c4 1 7 4 7 9s-4 8-9 13l-8 7 2-10c1-6 4-12 8-19z" fill="var(--surface-raised)" />
    </svg>
  );
}

export function LibraryArt() {
  return (
    <svg {...common} width="100%" height="100%">
      <rect x="20" y="26" width="13" height="46" rx="2" fill="var(--accent-soft)" />
      <rect x="35" y="20" width="13" height="52" rx="2" />
      <rect x="50" y="30" width="13" height="42" rx="2" fill="var(--accent-soft)" />
      <path d="M66 32l9 2-9 40-9-2z" />
      <path d="M24 36h5M39 30h5M54 40h5" />
    </svg>
  );
}

export function InsightsArt() {
  return (
    <svg {...common} width="100%" height="100%">
      <path d="M20 20v52h56" />
      <path d="M28 60l12-16 10 8 18-26" />
      <path d="M68 26l-9 0M68 26l0 9" />
      <circle cx="40" cy="44" r="3" fill="var(--surface-raised)" />
      <circle cx="50" cy="52" r="3" fill="var(--surface-raised)" />
      <path d="M30 78c8-3 16-3 24 0s16 3 24 0" stroke="var(--accent-soft)" strokeWidth="6" />
    </svg>
  );
}

export function ProfileArt() {
  return (
    <svg {...common} width="100%" height="100%">
      <circle cx="48" cy="48" r="30" stroke="var(--accent-soft)" strokeWidth="6" />
      <circle cx="48" cy="40" r="11" fill="var(--accent-soft)" />
      <path d="M30 70a18 18 0 0 1 36 0" />
      <path d="M48 14v6M82 48h-6M48 82v-6M14 48h6" stroke="var(--accent-soft)" strokeWidth="3" />
    </svg>
  );
}

export function SettingsArt() {
  return (
    <svg {...common} width="100%" height="100%">
      <path d="M22 32h52M22 48h52M22 64h52" stroke="var(--accent-soft)" strokeWidth="6" />
      <circle cx="38" cy="32" r="7" fill="var(--surface-raised)" />
      <circle cx="62" cy="48" r="7" fill="var(--surface-raised)" />
      <circle cx="34" cy="64" r="7" fill="var(--surface-raised)" />
    </svg>
  );
}
