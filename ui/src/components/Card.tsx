import type { HTMLAttributes, ReactNode } from "react";

/**
 * Card — a raised surface that groups related content.
 *
 * `padding` defaults to comfortable; pass "none" when the card hosts its own
 * edge-to-edge content (e.g. a list). `as` lets a card render as <section> or
 * <article> for correct document semantics. Visuals live under `.card`.
 */

type CardProps = HTMLAttributes<HTMLElement> & {
  padding?: "none" | "sm" | "md" | "lg";
  as?: "div" | "section" | "article";
  children?: ReactNode;
};

export function Card({
  padding = "md",
  as: Tag = "div",
  className,
  children,
  ...rest
}: CardProps) {
  const classes = ["card", `card--pad-${padding}`, className]
    .filter(Boolean)
    .join(" ");
  return (
    <Tag className={classes} {...rest}>
      {children}
    </Tag>
  );
}
