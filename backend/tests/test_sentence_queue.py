"""Phase 9 — the §7.5 sentence splitter and the VoiceStream ordering.

The splitter is the trickiest piece in the voice path, so it is tested directly
and exhaustively against every rule in ``EVA_MEMORY_ARCHITECTURE.md §7.5``,
including the two exact strings the plan's Phase 9 checks name:

* ``"He saw Dr. Smith, who paid $3.50 for it. Then left."`` → exactly two chunks,
  split before "Then" (abbreviation + decimal must NOT split; the real boundary
  must).
* ``"She said 'I'll be there'"`` → one chunk, no split inside the open quote.

A key invariant also checked: splitting a string all-at-once and feeding it one
character at a time must produce the **same** chunks — streaming must not change
the boundaries. VoiceStream is tested with a fake synth (no Kokoro) to confirm it
emits one ordered ``audio`` frame per sentence and degrades on unavailability.
"""

from __future__ import annotations

import asyncio

from voice.sentence_queue import MAX_WORDS, SentenceSplitter, VoiceStream
from voice import tts


def split_all(text: str) -> list[str]:
    """Whole-string split via a fresh splitter."""
    return SentenceSplitter().split(text)


def split_streamed(text: str, *, step: int = 1) -> list[str]:
    """Split by feeding ``text`` in ``step``-character pieces, then flushing."""
    sp = SentenceSplitter()
    out: list[str] = []
    for i in range(0, len(text), step):
        out += sp.push(text[i : i + step])
    out += sp.flush()
    return out


# --- the two named plan checks ------------------------------------------- #

def test_abbreviation_and_decimal_do_not_split_but_real_boundary_does():
    text = "He saw Dr. Smith, who paid $3.50 for it. Then left."
    chunks = split_all(text)
    assert chunks == [
        "He saw Dr. Smith, who paid $3.50 for it.",
        "Then left.",
    ]
    # Exactly two — not three (a split after "Dr.") or four (a split in "$3.50").
    assert len(chunks) == 2


def test_open_quote_is_one_chunk_no_internal_split():
    text = "She said 'I'll be there'"
    chunks = split_all(text)
    assert chunks == ["She said 'I'll be there'"]


def test_streaming_matches_whole_string_for_the_named_cases():
    for text in (
        "He saw Dr. Smith, who paid $3.50 for it. Then left.",
        "She said 'I'll be there'",
    ):
        assert split_streamed(text) == split_all(text)
        # ...and that holds for a few odd chunk sizes too (boundary lands mid-step).
        assert split_streamed(text, step=3) == split_all(text)
        assert split_streamed(text, step=7) == split_all(text)


# --- rule-by-rule coverage ------------------------------------------------ #

def test_rule1_each_abbreviation_blocks_a_split():
    for abbr in ("Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "Sr.", "Jr.", "vs.",
                 "etc.", "e.g.", "i.e.", "approx.", "est.", "fig.", "vol."):
        text = f"I met with {abbr} Adams today about it."
        # The abbreviation period must not end the sentence; the only boundary is
        # the final period → one chunk containing the whole line.
        assert split_all(text) == [text], abbr


def test_rule2_decimals_and_versions_do_not_split():
    assert split_all("The total was $3.50 in the end.") == [
        "The total was $3.50 in the end."
    ]
    assert split_all("We shipped v1.2 to the team.") == ["We shipped v1.2 to the team."]
    assert split_all("Pi is 3.14 to two places.") == ["Pi is 3.14 to two places."]


def test_rule3_period_inside_open_double_quote_does_not_split():
    text = '"Wait. Stop." he told me firmly.'
    # The ". " after "Wait" sits inside the open double quote → no split there; the
    # sentence ends only at the final period.
    assert split_all(text) == [text]


def test_rule4_splits_on_punct_space_uppercase_and_on_newline():
    assert split_all("This is the first one. Here is the second one.") == [
        "This is the first one.",
        "Here is the second one.",
    ]
    # Lowercase after the space is NOT a boundary (rule 4 needs an uppercase start).
    assert split_all("eat at 5 p.m. tomorrow if you can make it") == [
        "eat at 5 p.m. tomorrow if you can make it"
    ]
    # A newline right after the punctuation is a hard boundary.
    assert split_all("First line ends here.\nSecond line starts now.") == [
        "First line ends here.",
        "Second line starts now.",
    ]
    # ? and ! terminate too.
    assert split_all("Are you sure about this? Yes, completely sure.") == [
        "Are you sure about this?",
        "Yes, completely sure.",
    ]


def test_rule5_short_chunk_is_absorbed_into_the_next_sentence():
    # "Go now." is only two words (< 4) so it must buffer forward and ride out with
    # the following sentence rather than being emitted alone.
    text = "Go now. I really need to leave the house right away."
    assert split_all(text) == [
        "Go now. I really need to leave the house right away."
    ]


def test_rule5_tail_under_minimum_still_emitted_at_eos():
    # At end-of-stream there is no next sentence to absorb into, so a short final
    # chunk is emitted as-is (this is what makes "Then left." its own chunk above).
    text = "This first sentence is plenty long here. Then left."
    assert split_all(text) == [
        "This first sentence is plenty long here.",
        "Then left.",
    ]


def test_rule6_flushes_at_80_words_without_a_boundary():
    text = " ".join(f"word{i}" for i in range(200))  # 200 words, no punctuation
    chunks = split_streamed(text, step=5)
    # First chunks are exactly 80 words; the remainder follows.
    assert len(chunks[0].split()) == MAX_WORDS
    assert len(chunks[1].split()) == MAX_WORDS
    assert len(chunks[2].split()) == 200 - 2 * MAX_WORDS
    assert sum(len(c.split()) for c in chunks) == 200


def test_multi_sentence_stream_starts_emitting_before_the_end():
    # The first sentence must be emittable before the final token arrives — that's
    # what lets Eva start speaking early. push() returns it; flush() isn't needed.
    sp = SentenceSplitter()
    emitted: list[str] = []
    for ch in "The first sentence is comfortably long. The second":
        emitted += sp.push(ch)
    assert emitted == ["The first sentence is comfortably long."]


# --- VoiceStream (ordering + graceful degradation) ------------------------ #

def test_voicestream_emits_one_ordered_audio_frame_per_sentence():
    frames: list[dict] = []

    async def emit(frame: dict) -> None:
        frames.append(frame)

    def fake_synth(text: str) -> bytes:
        return f"wav:{text}".encode()

    async def run() -> None:
        vs = VoiceStream(fake_synth, emit)
        for piece in ["The first sentence is long enough. ", "The second one is also quite long."]:
            await vs.feed(piece)
        await vs.finish()

    asyncio.run(run())

    audio = [f for f in frames if f["type"] == "audio"]
    assert [f["text"] for f in audio] == [
        "The first sentence is long enough.",
        "The second one is also quite long.",
    ]
    # seq is monotonically increasing so the client can play in order.
    assert [f["seq"] for f in audio] == [1, 2]
    # data is base64 of the fake synth output.
    import base64
    assert base64.b64decode(audio[0]["data"]) == b"wav:The first sentence is long enough."


def test_voicestream_unavailable_emits_one_notice_then_text_only():
    frames: list[dict] = []

    async def emit(frame: dict) -> None:
        frames.append(frame)

    def boom(text: str) -> bytes:
        raise tts.TTSUnavailable("kokoro not installed")

    async def run() -> None:
        vs = VoiceStream(boom, emit)
        await vs.feed("The first sentence is long enough here. ")
        await vs.feed("And the second sentence is also long enough.")
        await vs.finish()

    asyncio.run(run())

    notices = [f for f in frames if f["type"] == "voice_unavailable"]
    assert len(notices) == 1  # told the client exactly once
    assert not [f for f in frames if f["type"] == "audio"]  # no audio after that


def test_voicestream_stop_skips_pending_synthesis():
    frames: list[dict] = []

    async def emit(frame: dict) -> None:
        frames.append(frame)

    def fake_synth(text: str) -> bytes:
        return b"wav"

    async def run() -> None:
        vs = VoiceStream(fake_synth, emit)
        vs.stop()  # cancel before any synthesis
        await vs.feed("This sentence is plenty long to emit. Another long one here too.")
        await vs.finish()

    asyncio.run(run())
    assert not [f for f in frames if f["type"] == "audio"]
