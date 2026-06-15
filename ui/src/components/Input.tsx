import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";

/**
 * Input / Textarea — the shared text-entry fields.
 *
 * They exist now (before any real form) so every later phase types into the
 * same field: same height, radius, focus ring, and disabled treatment. Visuals
 * live under `.field` in components.css.
 */

type InputProps = InputHTMLAttributes<HTMLInputElement>;

export function Input({ className, ...rest }: InputProps) {
  return <input className={["field", className].filter(Boolean).join(" ")} {...rest} />;
}

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

export function Textarea({ className, ...rest }: TextareaProps) {
  return (
    <textarea
      className={["field", "field--area", className].filter(Boolean).join(" ")}
      {...rest}
    />
  );
}
