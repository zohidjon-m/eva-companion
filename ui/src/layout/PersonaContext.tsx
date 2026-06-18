import { createContext, useCallback, useContext, useEffect, useState } from "react";

/**
 * PersonaContext — the one source of truth for how Eva shows up: a close friend,
 * a coach, or a mentor. The top-bar selector writes it; the chat send reads it
 * and includes it as `mode` on every turn, so the choice actually changes Eva's
 * replies (the backend folds a matching addendum into her system prompt).
 *
 * The choice is remembered between sessions in localStorage — it's a stable
 * preference, not per-conversation state.
 */

export type PersonaId = "friend" | "coach" | "mentor";

export type Persona = {
  id: PersonaId;
  name: string;
  hint: string;
};

export const PERSONAS: Persona[] = [
  { id: "friend", name: "Close friend", hint: "Warm, casual, listens first." },
  { id: "coach", name: "Coach", hint: "Encouraging, nudges you forward." },
  { id: "mentor", name: "Mentor", hint: "Measured, asks the hard question." },
];

const STORAGE_KEY = "eva.persona";
const DEFAULT: PersonaId = "friend";

function loadPersona(): PersonaId {
  const v = localStorage.getItem(STORAGE_KEY);
  return PERSONAS.some((p) => p.id === v) ? (v as PersonaId) : DEFAULT;
}

type PersonaState = {
  persona: PersonaId;
  setPersona: (id: PersonaId) => void;
};

const PersonaCtx = createContext<PersonaState>({
  persona: DEFAULT,
  setPersona: () => {},
});

export function PersonaProvider({ children }: { children: React.ReactNode }) {
  const [persona, setPersonaState] = useState<PersonaId>(loadPersona);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, persona);
  }, [persona]);

  const setPersona = useCallback((id: PersonaId) => setPersonaState(id), []);

  return (
    <PersonaCtx.Provider value={{ persona, setPersona }}>
      {children}
    </PersonaCtx.Provider>
  );
}

/** Read (and set) the current persona from anywhere in the tree. */
export function usePersona(): PersonaState {
  return useContext(PersonaCtx);
}
