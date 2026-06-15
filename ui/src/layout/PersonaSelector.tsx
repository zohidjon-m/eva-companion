import { useEffect, useRef, useState } from "react";
import { Icon } from "../components";

/**
 * PersonaSelector — choose how Eva shows up: a close friend, a coach, or a
 * mentor. Visual only in Phase 3; the choice is held in local state and not yet
 * sent to the backend. The persona system (Group D) wires this up in a later
 * phase, at which point this component just gains an onChange that calls the API.
 */

type Persona = {
  id: string;
  name: string;
  hint: string;
};

const PERSONAS: Persona[] = [
  { id: "friend", name: "Close friend", hint: "Warm, casual, listens first." },
  { id: "coach", name: "Coach", hint: "Encouraging, nudges you forward." },
  { id: "mentor", name: "Mentor", hint: "Measured, asks the hard question." },
];

export function PersonaSelector() {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(PERSONAS[0]);
  const root = useRef<HTMLDivElement>(null);

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
          {PERSONAS.map((p) => (
            <li key={p.id}>
              <button
                role="option"
                aria-selected={p.id === selected.id}
                className={
                  "persona__option" +
                  (p.id === selected.id ? " persona__option--active" : "")
                }
                onClick={() => {
                  setSelected(p);
                  setOpen(false);
                }}
              >
                <span className="persona__option-name">{p.name}</span>
                <span className="persona__option-hint">{p.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
