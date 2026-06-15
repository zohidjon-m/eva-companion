/**
 * Icon — a small set of hand-tuned line icons drawn as inline SVG.
 *
 * Why inline rather than an icon package: Eva ships fully offline and we keep
 * the dependency surface tiny. Every glyph here shares one visual language —
 * 24px box, 1.6 stroke, round caps/joins — so the set feels coherent rather
 * than scraped from three different libraries. `currentColor` lets callers
 * tint an icon by setting text color.
 */

export type IconName =
  | "chat"
  | "journal"
  | "library"
  | "insights"
  | "profile"
  | "settings"
  | "sun"
  | "moon"
  | "shield-check"
  | "chevron-down"
  | "sparkle"
  | "feather";

type IconProps = {
  name: IconName;
  size?: number;
  className?: string;
  /** Decorative by default; pass a label to expose it to screen readers. */
  label?: string;
};

// Each entry is the inner markup of a 24×24 viewBox, stroked with currentColor.
const PATHS: Record<IconName, JSX.Element> = {
  chat: (
    <path d="M5 6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v6A2.5 2.5 0 0 1 16.5 15H10l-4 4v-4H7.5A2.5 2.5 0 0 1 5 12.5z" />
  ),
  journal: (
    <>
      <path d="M6 4h9a2 2 0 0 1 2 2v14l-3-2-3 2-3-2-2 1.3V6a2 2 0 0 1 2-2z" />
      <path d="M9 8.5h5M9 12h3" />
    </>
  ),
  library: (
    <>
      <path d="M5 5h4v14H5zM10.5 5h4v14h-4z" />
      <path d="M16.2 5.8l2.9.8-3 12.5-2.8-.8" />
    </>
  ),
  insights: (
    <>
      <path d="M5 19V5M5 19h14" />
      <path d="M8.5 15l3-4 2.5 2.5L19 7" />
    </>
  ),
  profile: (
    <>
      <circle cx="12" cy="9" r="3.2" />
      <path d="M5.5 19a6.5 6.5 0 0 1 13 0" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="2.6" />
      <path d="M12 3.5v2.2M12 18.3v2.2M5.5 5.5l1.6 1.6M16.9 16.9l1.6 1.6M3.5 12h2.2M18.3 12h2.2M5.5 18.5l1.6-1.6M16.9 7.1l1.6-1.6" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.5 1.5M18.3 18.3l1.5 1.5M2.5 12h2M19.5 12h2M4.2 19.8l1.5-1.5M18.3 5.7l1.5-1.5" />
    </>
  ),
  moon: <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />,
  "shield-check": (
    <>
      <path d="M12 3.5l6.5 2.4v5.1c0 4.2-2.7 7.2-6.5 8.5-3.8-1.3-6.5-4.3-6.5-8.5V5.9z" />
      <path d="M9.2 12l2 2 3.6-3.8" />
    </>
  ),
  "chevron-down": <path d="M6 9.5l6 6 6-6" />,
  sparkle: (
    <path d="M12 3.5c.4 3.4 1.6 4.6 5 5-3.4.4-4.6 1.6-5 5-.4-3.4-1.6-4.6-5-5 3.4-.4 4.6-1.6 5-5zM18.5 13.5c.2 1.5.8 2.1 2.3 2.3-1.5.2-2.1.8-2.3 2.3-.2-1.5-.8-2.1-2.3-2.3 1.5-.2 2.1-.8 2.3-2.3z" />
  ),
  feather: (
    <>
      <path d="M19 6a5 5 0 0 0-7 0l-6 6v4h4l6-6a5 5 0 0 0 3-4z" />
      <path d="M16 9l-7 7M14.5 6.5L6 15" />
    </>
  ),
};

export function Icon({ name, size = 20, className, label }: IconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
