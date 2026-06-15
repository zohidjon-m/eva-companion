import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * Button — the one button in the app, in three weights.
 *
 * `primary`  solid moss, for the single most important action on a screen.
 * `secondary` quiet outlined surface, for everything else.
 * `ghost`    no chrome until hovered, for toolbar / inline actions.
 *
 * Styling lives in components.css under `.btn`; this component only maps props
 * to class names so the visual language stays in one place.
 */

type Variant = "primary" | "secondary" | "ghost";
type Size = "sm" | "md";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  /** Optional leading element (usually an <Icon/>). */
  iconBefore?: ReactNode;
  children?: ReactNode;
};

export function Button({
  variant = "secondary",
  size = "md",
  iconBefore,
  className,
  children,
  ...rest
}: ButtonProps) {
  const classes = [
    "btn",
    `btn--${variant}`,
    `btn--${size}`,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button className={classes} {...rest}>
      {iconBefore && <span className="btn__icon">{iconBefore}</span>}
      {children}
    </button>
  );
}
