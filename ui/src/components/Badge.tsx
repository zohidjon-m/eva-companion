import type { ReactNode } from "react";

/**
 * Badge — a small status pill (tone-colored). Used for the Offline ✓ badge in
 * the top bar and for "Coming soon" markers on empty-state screens.
 */

type Tone = "neutral" | "ok" | "warn" | "danger" | "accent";

type BadgeProps = {
  tone?: Tone;
  children: ReactNode;
  /** Optional leading element (usually a small <Icon/> or a status dot). */
  iconBefore?: ReactNode;
  title?: string;
};

export function Badge({ tone = "neutral", children, iconBefore, title }: BadgeProps) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {iconBefore && <span className="badge__icon">{iconBefore}</span>}
      {children}
    </span>
  );
}
