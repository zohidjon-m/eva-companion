import { useEffect, useRef, useState } from "react";
import { Icon } from "../components";
import { PERSONAS, usePersona } from "./PersonaContext";

/**
 * PersonaSelector — choose how Eva shows up: a close friend, a coach, or a
 * mentor. The choice lives in PersonaContext (persisted), and the chat send
 * includes it as `mode` on every turn, so it actually changes Eva's replies.
 *
 * The open menu marks the current choice with a check (not a heavy filled block),
 * so a selected option never looks like a stuck highlight behind its text.
 */
export function PersonaSelector() {
  const { persona, setPersona } = usePersona();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const selected = PERSONAS.find((p) => p.id === persona) ?? PERSONAS[0];

  // Close on outside-click and on Escape — basic menu hygiene.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="persona" ref={root}>
      <button
        className="persona__trigger"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="persona__dot" aria-hidden="true" />
        <span className="persona__name">{selected.name}</span>
        <Icon name="chevron-down" size={16} className="persona__chev" />
      </button>

      {open && (
        <ul className="persona__menu" role="listbox" aria-label="Eva's persona">
          {PERSONAS.map((p) => {
            const isCurrent = p.id === persona;
            return (
              <li key={p.id}>
                <button
                  role="option"
                  aria-selected={isCurrent}
                  className={
                    "persona__option" +
                    (isCurrent ? " persona__option--current" : "")
                  }
                  onClick={() => {
                    setPersona(p.id);
                    setOpen(false);
                  }}
                >
                  <span className="persona__option-text">
                    <span className="persona__option-name">{p.name}</span>
                    <span className="persona__option-hint">{p.hint}</span>
                  </span>
                  {isCurrent && (
                    <Icon
                      name="check"
                      size={16}
                      className="persona__option-check"
                    />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
