"""Phase 4 — system-prompt assembly: the four template slots.

Verifies the persona loads verbatim, empty slots are dropped (the resting state),
populated slots are appended in order, the crisis addendum rides on the persona
block, and the reply cap constant is what the plan specifies (450).
"""

from __future__ import annotations

from prompts import assembly


def test_persona_loads_verbatim():
    persona = assembly.load_persona()
    assert persona.startswith("You are Eva")
    # Loaded as-is from eva_system.md — the persona's voice rules are present.
    assert "listen first" in persona.lower()


def test_reply_cap_is_450():
    assert assembly.REPLY_MAX_TOKENS == 450


def test_empty_slots_are_dropped():
    # The Phase-4 resting state: only the persona, nothing concatenated after it.
    out = assembly.assemble_system_prompt(persona_block="PERSONA")
    assert out == "PERSONA"


def test_populated_slots_appended_in_order():
    out = assembly.assemble_system_prompt(
        persona_block="PERSONA",
        memory_context="MEM",
        profile_slices="PROF",
        corpus_context="CORP",
    )
    assert out.startswith("PERSONA")
    # Order is persona -> memory -> profile -> corpus.
    assert out.index("MEM") < out.index("PROF") < out.index("CORP")
    # Each context slot carries an explanatory header, not a bare paste.
    assert "past journal entries" in out
    assert "profile" in out
    assert "library" in out


def test_blank_slot_strings_contribute_nothing():
    out = assembly.assemble_system_prompt(
        persona_block="PERSONA", memory_context="   ", profile_slices=""
    )
    assert out == "PERSONA"


def test_crisis_addendum_rides_on_persona_block():
    out = assembly.build_chat_system_prompt(persona_addendum="CRISIS-CARE — stay with them.")
    assert out.startswith(assembly.load_persona())
    assert "CRISIS-CARE" in out


def test_no_addendum_is_just_the_persona():
    out = assembly.build_chat_system_prompt()
    assert out == assembly.load_persona()
