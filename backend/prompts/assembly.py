"""System-prompt assembly — fill four named slots, never hand-concatenate.

Phase 4 introduces the ONE place Eva's chat system prompt is built. The prompt is
composed from four slots, each kept as a *separate string* so a later phase can
fill one without disturbing the others or doing prompt surgery in the WebSocket
handler:

    {persona_block}   Eva's identity + voice, loaded verbatim from eva_system.md.
                      The interim crisis-care addendum (safety/crisis_check.py) is
                      appended to THIS slot before assembly, so care travels with
                      the persona rather than as a detached instruction block.
    {memory_context}  Past journal entries Eva may reference.  Empty until Phase 11.
    {profile_slices}  What Eva knows about the user.            Empty until Phase 13.
    {corpus_context}  Passages from the user's library.         Empty until Phase 7.

Because the slots are explicit parameters (and empty ones are simply dropped),
"give Eva memory" or "give Eva the profile" later is a one-line change here — not
a rewrite of how the prompt is glued together.

Reply length is capped at :data:`REPLY_MAX_TOKENS` (450), the §9 default that
keeps Eva to the 2–5 sentence voice the persona is tuned for. The cap is applied
by the client per request; it lives here as a named constant so the chat handler
sets it from one obvious place.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# eva_system.md sits next to this module (backend/prompts/). It is loaded as-is.
PERSONA_PATH = Path(__file__).resolve().parent / "eva_system.md"

# §9 default reply length. The persona aims for 2–5 sentences; 450 tokens is the
# generous ceiling that bounds the per-request context budget without clipping a
# normal reply mid-sentence.
REPLY_MAX_TOKENS = 450

# The three *context* slots, in the order they are appended after the persona.
# Each entry is (slot_name, header_shown_to_the_model). The persona block is
# handled separately because it is the prompt's spine, not a context section.
_CONTEXT_SLOTS: tuple[tuple[str, str], ...] = (
    ("memory_context", "Context from past journal entries (reference only if relevant):"),
    ("profile_slices", "What you know about this person (from their profile):"),
    ("corpus_context", "Passages from their library (quote only from these, and name the source):"),
)


@lru_cache(maxsize=1)
def load_persona() -> str:
    """Return Eva's persona block, read verbatim from ``eva_system.md``.

    Cached because the file never changes within a run and is read on every chat
    turn. The text is used as the ``{persona_block}`` slot exactly as written —
    Phase 4 does not template or edit it.
    """
    return PERSONA_PATH.read_text(encoding="utf-8").strip()


def assemble_system_prompt(
    *,
    persona_block: str,
    memory_context: str = "",
    profile_slices: str = "",
    corpus_context: str = "",
) -> str:
    """Compose the system prompt from the four slots, dropping empty ones.

    The persona block always leads. Each non-empty context slot is appended below
    it under a short header so the model can tell memories from profile from
    library passages. Empty slots (the resting state in Phase 4) contribute
    nothing — the assembled prompt is just the persona until later phases fill a
    slot. Slots are never concatenated by the caller; this function owns the glue.
    """
    parts = [persona_block.strip()]
    values = {
        "memory_context": memory_context,
        "profile_slices": profile_slices,
        "corpus_context": corpus_context,
    }
    for name, header in _CONTEXT_SLOTS:
        value = (values[name] or "").strip()
        if value:
            parts.append(f"{header}\n{value}")
    return "\n\n".join(parts)


def build_chat_system_prompt(
    *,
    persona_addendum: str = "",
    memory_context: str = "",
    profile_slices: str = "",
    corpus_context: str = "",
) -> str:
    """Build the full chat system prompt for one turn.

    Loads the persona, appends the optional ``persona_addendum`` (the interim
    crisis-care text, when the message tripped the keyword scan), and assembles it
    with whatever context slots are populated. This is the entry point the
    ``/chat`` handler calls; ``assemble_system_prompt`` stays a pure function for
    easy testing.
    """
    persona = load_persona()
    persona_block = persona if not persona_addendum else f"{persona}\n\n{persona_addendum.strip()}"
    return assemble_system_prompt(
        persona_block=persona_block,
        memory_context=memory_context,
        profile_slices=profile_slices,
        corpus_context=corpus_context,
    )
