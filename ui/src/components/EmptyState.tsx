import type { ReactNode } from "react";

/**
 * EmptyState — the intentional "nothing here yet" screen.
 *
 * In Phase 3 every section is empty, so this is the most-seen component in the
 * app. It is deliberately warm and specific (custom illustration + real copy),
 * never a generic "No data" line. Each section supplies its own illustration
 * and words; the layout and rhythm are shared here.
 */

type EmptyStateProps = {
  /** Bespoke SVG illustration for the section. */
  illustration: ReactNode;
  eyebrow?: string;
  title: string;
  description: ReactNode;
  /** Optional primary affordance (e.g. a disabled "Coming soon" button). */
  action?: ReactNode;
  /** Small footnote, e.g. which phase lights this up. */
  footnote?: ReactNode;
};

export function EmptyState({
  illustration,
  eyebrow,
  title,
  description,
  action,
  footnote,
}: EmptyStateProps) {
  return (
    <div className="empty">
      <div className="empty__art" aria-hidden="true">
        {illustration}
      </div>
      {eyebrow && <p className="eyebrow empty__eyebrow">{eyebrow}</p>}
      <h2 className="empty__title">{title}</h2>
      <p className="empty__desc">{description}</p>
      {action && <div className="empty__action">{action}</div>}
      {footnote && <p className="empty__foot">{footnote}</p>}
    </div>
  );
}
