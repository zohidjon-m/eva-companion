"""Sentence-boundary splitter + audio streamer for voice-out (Phase 9).

This module has two parts:

1. :class:`SentenceSplitter` — a *stateful, character-level* scanner that turns a
   streamed token sequence into whole sentences, chunked at the boundaries Eva
   should pause on. It implements ``EVA_MEMORY_ARCHITECTURE.md §7.5`` **exactly**:
   the abbreviation list, the number-period rule, the open-quote rule, the
   ``. `` / ``! `` / ``? `` (+uppercase/EOS) and ``.\\n`` boundary triggers, the
   4-word minimum, and the 80-word maximum flush. It deliberately does **not** use
   ``nltk.sent_tokenize`` — that library's import latency and batch design are
   unsuitable for token-by-token streaming (§7.5 implementation note).

2. :class:`VoiceStream` — the concurrency piece. It feeds tokens into the splitter
   as they stream from the model, hands each completed sentence to Kokoro on a
   worker (off the event loop, so token streaming never stalls), and emits the
   resulting audio chunks **in order** over the chat WebSocket alongside the text.

Why a hand-written scanner: streaming means we see the text a few characters at a
time and must decide "is *this* period a sentence end?" with only the lookahead we
have so far. When the lookahead isn't there yet (a period at the very end of the
buffer), the scanner reports *wait* and re-decides once more text arrives — only
:meth:`SentenceSplitter.flush` (end-of-stream) forces those pending boundaries.
Every such state transition is commented inline, per the Phase 9 instructions.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Awaitable, Callable

from voice import tts

log = logging.getLogger("eva.voice.sentence_queue")

# ─────────────────────────────────────────────────────────────────────────────
# §7.5 constants — the locked splitter contract.
# ─────────────────────────────────────────────────────────────────────────────

#: Rule 1 — tokens that end in a period but are *not* a sentence end. Stored lower-
#: cased and matched case-insensitively against the whole word preceding the period
#: (the trailing period included), so "Dr." / "e.g." / "vol." never trigger a split.
ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "dr.", "mr.", "mrs.", "ms.", "prof.", "sr.", "jr.", "vs.",
        "etc.", "e.g.", "i.e.", "approx.", "est.", "fig.", "vol.",
    }
)

#: Rule 5 — never emit a chunk shorter than this many words; buffer it forward into
#: the next sentence instead (short chunks sound clipped/robotic through TTS).
MIN_WORDS = 4

#: Rule 6 — flush at this many words even with no boundary in sight, splitting at
#: the last word, so one very long model "sentence" can't stall the audio.
MAX_WORDS = 80

#: Closing punctuation that belongs to the sentence it trails — a closing quote or
#: bracket sits *after* the terminal ``.``/``!``/``?`` ( ``He said "stop." Then…`` ).
#: We let these ride with the sentence before testing for the separating space.
_CLOSERS = '"”\'’)]'

#: Opening punctuation that can sit between the separating space and the first
#: letter of the next sentence ( ``He left.  "Stop!"`` ). Skipped before the
#: uppercase check so a quoted next sentence still splits.
_OPENERS = '"“\'‘(['


def _word_count(text: str) -> int:
    """Words in ``text`` by whitespace split — the unit rules 5 and 6 measure in."""
    return len(text.split())


class SentenceSplitter:
    """Incrementally split a streamed character sequence into TTS-ready sentences.

    Feed text as it arrives with :meth:`push` (returns any sentences that became
    complete *and* meet the 4-word minimum); call :meth:`flush` once at end-of-
    stream to drain whatever remains. The whole-string helper :meth:`split` runs
    both for non-streaming callers and tests.

    Internal state is just two fields:

    * ``_buf`` — the text accepted so far that has **not** yet been emitted as a
      sentence (the in-progress chunk, plus any short sentences absorbed forward).
    * ``_scan`` — how far into ``_buf`` we have already searched for a boundary.
      Characters before ``_scan`` are settled (given the lookahead we had); a
      *wait* leaves ``_scan`` parked on the undecided period until more text comes.
    """

    def __init__(self) -> None:
        self._buf: str = ""
        self._scan: int = 0

    # -- public API -------------------------------------------------------- #

    def push(self, text: str) -> list[str]:
        """Accept the next streamed piece; return any newly completed sentences.

        Appends ``text`` to the buffer and runs the scanner. Returns the list of
        sentences that closed on a boundary and cleared the 4-word minimum (often
        empty — most tokens don't end a sentence), in order. A boundary whose chunk
        is too short is *absorbed* into the following sentence rather than emitted.
        """
        if not text:
            return []
        self._buf += text
        return self._drain(eos=False)

    def flush(self) -> list[str]:
        """End-of-stream: emit every remaining sentence, relaxing the soft rules.

        At EOS a trailing period *is* a boundary (rule 4's "end-of-stream"), and
        the final chunk is emitted even if it's under the 4-word minimum — there is
        no "next sentence" left to absorb it into. The 80-word cap still applies so
        a giant unpunctuated tail is broken up. Leaves the splitter empty and
        reusable.
        """
        out = self._drain(eos=True)
        # Whatever survived the EOS scan is the last sentence (or an absorbed run of
        # short ones). Emit it verbatim — min-word no longer applies at end-of-stream.
        tail = self._buf.strip()
        if tail:
            out.extend(self._split_overlong(tail))
        self._buf = ""
        self._scan = 0
        return out

    def split(self, text: str) -> list[str]:
        """Split a complete string in one go (non-streaming callers and tests)."""
        return self.push(text) + self.flush()

    # -- the scanner ------------------------------------------------------- #

    def _drain(self, *, eos: bool) -> list[str]:
        """Scan the buffer from ``_scan``, emitting every sentence that closes.

        This is the core loop. At each ``.``/``!``/``?`` it asks
        :meth:`_boundary_at` for one of three verdicts and transitions accordingly:

        * **wait**     — not enough lookahead yet; stop scanning and keep ``_scan``
                         parked on this period so the next :meth:`push` re-decides.
        * **boundary** — a real sentence end. If the chunk is long enough, emit it
                         and restart the scan on the remainder; if it's too short,
                         *absorb* it (rule 5) by stepping ``_scan`` past the period
                         and continuing, so it merges into the next sentence.
        * **not**      — a period that isn't a boundary (abbreviation, decimal,
                         inside an open quote, mid-word). Step past and keep going.

        After the scan, rule 6 force-flushes any 80-word overflow.
        """
        out: list[str] = []
        i = self._scan
        while i < len(self._buf):
            ch = self._buf[i]
            if ch not in ".!?":
                # Plain character: nothing to decide. Advance the settled cursor.
                i += 1
                self._scan = i
                continue

            verdict, end = self._boundary_at(i, eos=eos)

            if verdict == "wait":
                # Undecided for lack of lookahead. Park _scan here and bail — the
                # next push() appends more text and we resume from this very period.
                self._scan = i
                break

            if verdict == "not":
                # Confirmed non-boundary. ``end`` is the next index to examine.
                i = end
                self._scan = i
                continue

            # verdict == "boundary": ``end`` is the index just past the sentence
            # (terminal punctuation + any trailing closers), before the separator.
            candidate = self._buf[:end].strip()
            if _word_count(candidate) >= MIN_WORDS:
                # Long enough → emit it and drop it from the buffer, skipping the
                # separating whitespace so the next sentence starts clean. Restart
                # the scan at the front of the remainder.
                out.append(candidate)
                self._buf = self._buf[self._skip_separators(end):]
                self._scan = 0
                i = 0
                continue
            # Too short (rule 5): do NOT emit. Step the settled cursor past this
            # period and keep scanning — this sentence is absorbed into the next.
            i = end
            self._scan = i

        # Rule 6 — even with no boundary, never let the buffer grow past 80 words.
        out.extend(self._flush_overlong_buffer())
        return out

    def _boundary_at(self, i: int, *, eos: bool) -> tuple[str, int]:
        """Classify the ``.``/``!``/``?`` at ``self._buf[i]``.

        Returns ``(verdict, end)`` where verdict is ``"wait"``, ``"not"`` or
        ``"boundary"``. For ``"not"`` and ``"boundary"`` ``end`` is the next index
        to look at / the index just past the sentence respectively; for ``"wait"``
        ``end`` is ``-1``. The rule order matches §7.5 priority.
        """
        buf = self._buf
        n = len(buf)
        ch = buf[i]

        # Need at least one character of lookahead after the punctuation to apply
        # the number / boundary rules. Without it: at EOS the period terminates the
        # stream (boundary, rule 4); mid-stream we must wait for the next token.
        if i + 1 >= n:
            return ("boundary", n) if eos else ("wait", -1)

        nxt = buf[i + 1]

        # Rule 2 — a period immediately before a digit is a decimal point, never a
        # sentence end ( ``$3.50`` ``v1.2`` ``3.14`` ). Step over just the period.
        if ch == "." and nxt.isdigit():
            return ("not", i + 1)

        # Rule 1 — a known abbreviation token ending in this period is not a stop
        # ( ``Dr.`` ``e.g.`` ``vol.`` ). Step over just the period.
        if ch == "." and self._preceding_is_abbrev(i):
            return ("not", i + 1)

        # Let any closing quote/bracket ride with the sentence before we look for
        # the separating space ( ``stop." Then`` ): scan past trailing closers.
        j = i + 1
        while j < n and buf[j] in _CLOSERS:
            j += 1
        if j >= n:
            # The closers run to the end of the buffer. At EOS that's a sentence
            # end; mid-stream wait for whatever follows the closing quote.
            return ("boundary", j) if eos else ("wait", -1)

        # Rule 3 — if a quotation is still open at this point, the sentence is not
        # over even though we hit terminal punctuation ( ``"Wait. Stop." he said`` ).
        if self._quote_open(j):
            return ("not", i + 1)

        after = buf[j]

        # Rule 4 — ``.\n`` / ``!\n`` / ``?\n``: a newline right after the
        # punctuation is a hard boundary regardless of case.
        if after == "\n":
            return ("boundary", j)

        # Rule 4 — punctuation + space(s) + uppercase (or end-of-stream). Consume
        # the run of spaces, then any opening quote/bracket, then test the letter.
        if after in " \t":
            k = j
            while k < n and buf[k] in " \t":
                k += 1
            if k >= n:
                # Trailing space with nothing after it yet: at EOS this closes the
                # sentence (rule 4 "end-of-stream"); mid-stream we wait.
                return ("boundary", j) if eos else ("wait", -1)
            # A space then a newline is still a paragraph break → boundary.
            if buf[k] == "\n":
                return ("boundary", j)
            # Skip an opening quote/bracket so a quoted next sentence still splits.
            while k < n and buf[k] in _OPENERS:
                k += 1
            if k >= n:
                return ("boundary", j) if eos else ("wait", -1)
            if buf[k].isupper():
                # Capitalised next word → a real sentence boundary.
                return ("boundary", j)
            # Space then lowercase/digit/punctuation: rule 4 requires an uppercase
            # start, so this is mid-sentence (e.g. "the U.S. dollar"). Not a stop.
            return ("not", i + 1)

        # Punctuation glued straight to the next character ( ``v1`` ``U.S.A`` or a
        # ``...`` ellipsis): not a boundary. Step over the period only.
        return ("not", i + 1)

    # -- helpers ----------------------------------------------------------- #

    def _preceding_is_abbrev(self, i: int) -> bool:
        """Whether the word ending at the period ``self._buf[i]`` is an abbreviation.

        Walks back to the previous whitespace to grab the whole token (period
        included), strips a leading opening quote/bracket, and checks the §7.5 list.
        """
        buf = self._buf
        start = i
        while start > 0 and not buf[start - 1].isspace():
            start -= 1
        token = buf[start : i + 1].lower().lstrip(_OPENERS)
        return token in ABBREVIATIONS

    def _quote_open(self, j: int) -> bool:
        """Whether a straight quote is unbalanced in ``self._buf[:j]`` (rule 3).

        Double quotes simply toggle. Single quotes are ambiguous — an apostrophe in
        a contraction ( ``I'll`` ``don't`` ) must NOT count as a quote — so a ``'``
        toggles only when it sits at a word boundary: opening when it follows a
        space/start, closing when it precedes a space/end. A ``'`` flanked by two
        letters is an apostrophe and is ignored. Curly quotes are left to the
        spec's straight-quote rule.
        """
        buf = self._buf
        dq = False  # inside a double-quoted span
        sq = False  # inside a single-quoted span
        for idx in range(j):
            c = buf[idx]
            if c == '"':
                # Double quote: unconditional toggle (no contraction ambiguity).
                dq = not dq
            elif c == "'":
                prev_alnum = idx > 0 and buf[idx - 1].isalnum()
                next_alnum = idx + 1 < len(buf) and buf[idx + 1].isalnum()
                if not prev_alnum:
                    # Preceded by space/start/punct → an *opening* single quote.
                    sq = True
                elif not next_alnum:
                    # Letter before, non-letter after → a *closing* single quote.
                    sq = False
                # else: letter on both sides → apostrophe; leave the flag untouched.
        return dq or sq

    def _skip_separators(self, end: int) -> int:
        """Index of the next sentence's first character, skipping the separator.

        After a sentence ends at ``end`` the buffer continues with the whitespace
        (and at most one trailing newline) that separated it from the next; step
        over that so the remainder begins on real content.
        """
        buf = self._buf
        k = end
        while k < len(buf) and buf[k] in " \t":
            k += 1
        if k < len(buf) and buf[k] == "\n":
            k += 1
        while k < len(buf) and buf[k] in " \t":
            k += 1
        return k

    def _flush_overlong_buffer(self) -> list[str]:
        """Rule 6 — while the unemitted buffer exceeds 80 words, emit 80 at a time.

        Only fires when no boundary has drained the buffer (one very long model
        sentence). Splits at the 80th word boundary, keeps the rest buffered, and
        resets the scan cursor so the remainder is re-examined for real boundaries.

        The guard is strictly ``> MAX_WORDS``, not ``>=``: mid-stream the buffer can
        end in a half-arrived word ( ``"… word79 wo"`` ), and whitespace-counting
        that partial token as the 80th would cut a word in half. Waiting until an
        81st token has appeared guarantees the 80th word is complete before we cut.
        """
        out: list[str] = []
        while _word_count(self._buf) > MAX_WORDS:
            head, rest = self._cut_at_word(self._buf, MAX_WORDS)
            if not head:
                break
            out.append(head.strip())
            self._buf = rest
            self._scan = 0
        return out

    def _split_overlong(self, text: str) -> list[str]:
        """Break a final tail into ≤80-word chunks (rule 6 applied at flush)."""
        chunks: list[str] = []
        rest = text
        while _word_count(rest) > MAX_WORDS:
            head, rest = self._cut_at_word(rest, MAX_WORDS)
            chunks.append(head.strip())
        if rest.strip():
            chunks.append(rest.strip())
        return chunks

    @staticmethod
    def _cut_at_word(text: str, words: int) -> tuple[str, str]:
        """Split ``text`` after the ``words``-th word; return ``(head, rest)``."""
        count = 0
        in_word = False
        for idx, c in enumerate(text):
            if c.isspace():
                in_word = False
            elif not in_word:
                in_word = True
                count += 1
                if count == words + 1:
                    # idx is the first character of word N+1 — cut just before it.
                    return text[:idx], text[idx:]
        return text, ""


# ─────────────────────────────────────────────────────────────────────────────
# VoiceStream — synthesize sentences off the event loop and emit them in order.
# ─────────────────────────────────────────────────────────────────────────────

# An async callable that ships one already-built frame to the client. The caller
# (app.py) wraps it in a per-connection lock so audio frames never interleave with
# the text-token frames being sent on the same socket.
EmitFn = Callable[[dict], Awaitable[None]]

# A blocking text→wav-bytes callable (Kokoro). Run on a worker thread via
# ``asyncio.to_thread`` so synthesis (CPU/GPU heavy) never blocks token streaming.
SynthFn = Callable[[str], bytes]


class VoiceStream:
    """Turn a streamed reply into ordered audio frames over the chat socket.

    Lifecycle, one per voiced turn:

    * :meth:`feed` is called with each model token; completed sentences are queued.
    * a single background worker pulls sentences **in order**, synthesizes each on
      a thread, and emits an ``audio`` frame — so audio starts flowing while later
      tokens are still arriving (the "start speaking ≤ ~2.5 s" goal).
    * :meth:`finish` drains the splitter, waits for the worker, and returns.
    * :meth:`stop` skips any not-yet-synthesized sentences (used on error/cleanup;
      the client also stops playback locally when voice is toggled off mid-reply).

    Ordering is guaranteed by a single worker (sentences synth one at a time) plus
    a monotonically increasing ``seq`` on every frame, so the client can play (or
    re-order) deterministically. Kokoro being unavailable degrades to a one-shot
    ``voice_unavailable`` frame and text-only — never a crash (§9).
    """

    def __init__(self, synth: SynthFn, emit: EmitFn) -> None:
        self._splitter = SentenceSplitter()
        self._synth = synth
        self._emit = emit
        self._queue: asyncio.Queue = asyncio.Queue()
        self._seq = 0
        self._stopped = False
        self._unavailable = False
        # Start the worker immediately so the first sentence can synth the moment
        # it's queued, overlapping with the rest of the token stream.
        self._worker = asyncio.create_task(self._run())

    async def feed(self, token: str) -> None:
        """Feed one streamed token; queue any sentences it completes."""
        for sentence in self._splitter.push(token):
            await self._enqueue(sentence)

    async def finish(self) -> None:
        """Drain the splitter, signal the worker to stop, and await it."""
        for sentence in self._splitter.flush():
            await self._enqueue(sentence)
        await self._queue.put(None)  # sentinel: no more sentences
        await self._worker

    def stop(self) -> None:
        """Skip any pending/in-flight synthesis (client-side playback stops too)."""
        self._stopped = True

    async def _enqueue(self, sentence: str) -> None:
        self._seq += 1
        await self._queue.put((self._seq, sentence))

    async def _run(self) -> None:
        """Worker: synth queued sentences in order and emit them as audio frames."""
        while True:
            item = await self._queue.get()
            if item is None:
                return  # sentinel from finish() — clean shutdown
            seq, sentence = item
            if self._stopped or self._unavailable:
                continue  # drop without synth once stopped/unavailable
            try:
                wav = await asyncio.to_thread(self._synth, sentence)
            except tts.TTSUnavailable as exc:
                # Voice isn't set up. Tell the client once, then go text-only.
                self._unavailable = True
                log.warning("voice unavailable, falling back to text: %s", exc)
                await self._safe_emit({"type": "voice_unavailable", "message": str(exc)})
                continue
            except Exception:  # noqa: BLE001 — one bad sentence must not kill the turn
                log.exception("TTS synthesis failed for a sentence; skipping it")
                continue
            if not wav or self._stopped:
                continue
            await self._safe_emit(
                {
                    "type": "audio",
                    "seq": seq,
                    "format": "wav",
                    "text": sentence,
                    "data": base64.b64encode(wav).decode("ascii"),
                }
            )

    async def _safe_emit(self, frame: dict) -> None:
        """Emit a frame, swallowing send errors (a dropped socket isn't fatal here)."""
        try:
            await self._emit(frame)
        except Exception:  # noqa: BLE001 — the socket went away mid-reply; nothing to do
            log.debug("could not emit %s frame (socket closed?)", frame.get("type"))
