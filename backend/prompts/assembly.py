"""System-prompt assembly — fill named slots, never hand-concatenate.

Phase 4 introduces the ONE place Eva's chat system prompt is built. The prompt is
composed from a persona block plus context slots, each kept as a *separate string*
so a phase can fill one without disturbing the others or doing prompt surgery in
the read loop:

    {persona_block}    Eva's identity + voice, loaded verbatim from eva_system.md.
                       The interim crisis-care addendum (safety/crisis_check.py) is
                       appended to THIS slot before assembly, so care travels with
                       the persona rather than as a detached instruction block.
    {episodes_context} Recent L1 episodes — the chronological "lately" baseline. R6.
    {memory_context}   Relevance-recalled past entries Eva may reference. Phase 11.
    {profile_slices}   What Eva knows about the user.                     Phase 13.
    {corpus_context}   Passages from the user's library.                  Phase 7.

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

# Phase 5 journal acknowledgment: a single, very short line offered back after a
# journal entry is saved. Capped tight because it is one reflection, not a reply.
JOURNAL_ACK_MAX_TOKENS = 120

# The instruction that turns Eva's persona into a one-line journal acknowledgment.
# It rides on top of the same persona block (so the voice is identical to chat),
# and it hard-codes the Phase-5 rule: a reflection or one soft question, never
# advice. The persona already forbids advice-unless-asked; this makes the journal
# case explicit so a saved entry is never answered with solutions or a summary.
JOURNAL_ACK_INSTRUCTION = (
    "The person has just finished writing the journal entry below. They did not "
    "ask you anything. Offer ONE short line back to them — a single gentle "
    "reflection, or one soft open question that shows you truly read it, in their "
    "own terms. Do not give advice, solutions, reassurances, lessons, or a "
    "summary, and ask at most one question. One or two sentences of plain, warm "
    "spoken prose — nothing else."
)


# ─────────────────────────────────────────────────────────────────────────────
# Chat modes (the UI's "Close friend / Coach / Mentor" selector). The mode rides
# on the persona block as a short addendum — exactly like the crisis-care text —
# so Eva's core identity and voice constraints in eva_system.md always lead and
# are never edited; the mode only shifts *how forward-leaning* she is once she has
# listened. "friend" is the resting state the base persona already embodies, so it
# adds nothing. Unknown/missing values fall back to friend.
# ─────────────────────────────────────────────────────────────────────────────
CHAT_MODES = ("friend", "coach", "mentor")
DEFAULT_CHAT_MODE = "friend"

_MODE_ADDENDA: dict[str, str] = {
    "friend": "",  # the base persona already IS the close friend — no shift.
    "coach": (
        "IMPORTANT — for this conversation the person has explicitly chosen COACH "
        "mode, so this overrides the listen-first default: they WANT you to be "
        "active, not just reflective. Briefly acknowledge how they feel in one "
        "sentence, then DO end with one specific, doable next step or a small "
        "challenge for today — even though they did not ask — because choosing "
        "coach mode IS the ask. Be upbeat, motivating, and forward-looking; this "
        "is the one time it is right to nudge them toward what they said they "
        "want. Still your short, plain, spoken voice: 2–4 sentences, no lists."
    ),
    "mentor": (
        "IMPORTANT — for this conversation the person has explicitly chosen MENTOR "
        "mode, so this overrides the listen-first default: they WANT you to "
        "challenge their thinking, not just mirror it. Acknowledge briefly, then "
        "either name the pattern or trade-off you notice and offer a frame or "
        "perspective to weigh, OR ask one pointed, harder question that makes them "
        "stop and think. Speak with calm authority, like a wise older mentor — "
        "direct and honest, never a lecture. Still your short, plain, spoken "
        "voice: 2–4 sentences, no lists."
    ),
}


def mode_addendum(mode: str | None) -> str:
    """Return the persona addendum for a chat mode (friend/coach/mentor).

    Empty for ``friend`` and for any unknown/missing value, so the resting state
    is always the base persona exactly as written in eva_system.md.
    """
    return _MODE_ADDENDA.get((mode or DEFAULT_CHAT_MODE), "")


def build_journal_ack_prompt() -> str:
    """Build the system prompt for the post-save journal acknowledgment.

    Reuses Eva's persona verbatim (so the journal voice and the chat voice are
    the same Eva) and appends :data:`JOURNAL_ACK_INSTRUCTION`. Kept here beside
    the chat assembly so every prompt Eva speaks with is composed in one place.
    """
    return f"{load_persona()}\n\n{JOURNAL_ACK_INSTRUCTION}"

# The hard grounding rule that governs the corpus slot (Phase 7; EVA_MEMORY_
# ARCHITECTURE §5.10, EVA_SYSTEM_DESIGN §7.3). It lives here, beside the slot, and
# is emitted automatically whenever corpus passages are present — so the
# no-invented-citations discipline can never be forgotten at a call site. A
# misquoted or misattributed source is a real harm, not a glitch, hence the
# emphatic wording; the passages were already gated by relevance upstream, so if
# this block is present at all, on-topic passages exist for Eva to answer from.
GROUNDING_RULE = (
    "Answer using ONLY the passages below. If they do not contain what the person "
    "is asking about, say plainly that you don't find it in their library — do not "
    "answer from your own knowledge, and never invent, guess, or paraphrase a "
    "quote, source, title, page, or citation. When you do draw on a passage, name "
    "its source as shown."
)

# The three *context* slots, in the order they are appended after the persona.
# Each entry is (slot_name, header_shown_to_the_model). The persona block is
# handled separately because it is the prompt's spine, not a context section.
# The memory/profile headers are phrased as "what a friend remembers", not as a
# data feed, so the context informs Eva's reply instead of tipping her into a
# report-reading register (the #7 "doesn't feel like a close friend" fix).
_CONTEXT_SLOTS: tuple[tuple[str, str], ...] = (
    ("episodes_context", "What's been on their mind lately, from their recent entries — so you're already caught up, the way a close friend is; don't recap it back:"),
    ("memory_context", "Things they've shared with you before — bring any of it up only if it naturally fits, the way a friend remembers, never as a recap:"),
    ("profile_slices", "What you already know about them, so you can talk like someone who actually knows them (don't list it back):"),
    ("corpus_context", f"Passages from their library. {GROUNDING_RULE}"),
)

# A short reminder appended AFTER the context slots, so the last thing the model
# reads before replying is Eva's voice — not a block of context. Only added when
# at least one context slot is present (a plain persona prompt needs no nudge).
# This counters the small E2B model's tendency to drift into "summarizing the
# notes" once context is in the window (the #7 close-friend fix).
_CLOSING_VOICE_REMINDER = (
    "Reply as Eva: warm, brief, two to five sentences, like a close friend who is "
    "really listening — not an assistant and not a summary of the notes above. Let "
    "what you know shape your reply quietly; don't recite it."
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
    episodes_context: str = "",
    memory_context: str = "",
    profile_slices: str = "",
    corpus_context: str = "",
) -> str:
    """Compose the system prompt from the context slots, dropping empty ones.

    The persona block always leads. Each non-empty context slot is appended below
    it under a short header so the model can tell recent episodes from relevance
    recall from profile from library passages. Empty slots contribute nothing — the
    assembled prompt is just the persona until a slot is filled. Slots are never
    concatenated by the caller; this function owns the glue.
    """
    parts = [persona_block.strip()]
    values = {
        "episodes_context": episodes_context,
        "memory_context": memory_context,
        "profile_slices": profile_slices,
        "corpus_context": corpus_context,
    }
    added_context = False
    for name, header in _CONTEXT_SLOTS:
        value = (values[name] or "").strip()
        if value:
            parts.append(f"{header}\n{value}")
            added_context = True
    # Close on Eva's voice when context was added, so it's the last thing read.
    if added_context:
        parts.append(_CLOSING_VOICE_REMINDER)
    return "\n\n".join(parts)


def build_chat_system_prompt(
    *,
    mode: str | None = DEFAULT_CHAT_MODE,
    persona_addendum: str = "",
    episodes_context: str = "",
    memory_context: str = "",
    profile_slices: str = "",
    corpus_context: str = "",
) -> str:
    """Build the full chat system prompt for one turn.

    Loads the persona, appends the chat-mode addendum (the UI's friend/coach/
    mentor choice) and the optional ``persona_addendum`` (the interim crisis-care
    text, when the message tripped the keyword scan), and assembles it with
    whatever context slots are populated. Both addenda ride on the persona block,
    so Eva's core identity and voice always lead. This is the entry point the
    read loop calls; ``assemble_system_prompt`` stays a pure function for easy
    testing.
    """
    persona = load_persona()
    addenda = [a for a in (mode_addendum(mode), persona_addendum.strip()) if a]
    persona_block = "\n\n".join([persona, *addenda]) if addenda else persona
    return assemble_system_prompt(
        persona_block=persona_block,
        episodes_context=episodes_context,
        memory_context=memory_context,
        profile_slices=profile_slices,
        corpus_context=corpus_context,
    )
