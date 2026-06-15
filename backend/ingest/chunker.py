"""Chunker — split loaded sections into ~500-token overlapping chunks.

Each chunk carries forward its source locator (page for PDF, heading for
Markdown) so a retrieved passage can be cited precisely in Phase 7. Chunks are
the unit we embed; ~500 tokens keeps each one comfortably inside bge-small's
512-token window while staying large enough to hold a coherent idea, and a small
overlap stops a relevant sentence from being orphaned at a chunk boundary.

**Token counting without a tokenizer.** We deliberately avoid loading the
embedding model's tokenizer here (it would couple the chunker to the embedder and
slow imports). Instead we approximate: for English prose ~1.3 tokens per
whitespace word is a stable, well-known ratio. So a ~500-token target is ~385
words, and a ~60-token overlap is ~46 words. The approximation only needs to keep
us under 512 tokens with margin, which it does.

Chunks never span a section boundary: a PDF chunk belongs to exactly one page, a
Markdown chunk to exactly one heading. That keeps the locator exact at the cost
of some short trailing chunks on short pages — a worthwhile trade for honest
citations.
"""

from __future__ import annotations

from dataclasses import dataclass

from .loaders import LoadedSection

# Target ~500 tokens per chunk with ~60 tokens of overlap, converted to words via
# the ~1.3-tokens-per-word ratio documented above.
CHUNK_TOKENS = 500
OVERLAP_TOKENS = 60
TOKENS_PER_WORD = 1.3

WORDS_PER_CHUNK = round(CHUNK_TOKENS / TOKENS_PER_WORD)  # ~385
OVERLAP_WORDS = round(OVERLAP_TOKENS / TOKENS_PER_WORD)  # ~46


@dataclass
class Chunk:
    """One embeddable passage plus its source metadata.

    ``index`` is the chunk's 0-based position within its document (stable id seed).
    ``page`` / ``section`` are inherited from the originating section so the chunk
    can be cited. ``est_tokens`` is the approximate token count (diagnostics/tests).
    """

    text: str
    index: int
    page: int | None = None
    section: str | None = None
    est_tokens: int = 0


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text`` (~1.3 tokens per whitespace word)."""
    return round(len(text.split()) * TOKENS_PER_WORD)


def _window_words(
    words: list[str], size: int, overlap: int
) -> list[list[str]]:
    """Slide a ``size``-word window over ``words`` advancing by ``size - overlap``.

    Returns the windows as word lists. A non-zero overlap means the tail of one
    window repeats at the head of the next, so an idea split across the boundary
    still appears whole in at least one chunk.
    """
    if not words:
        return []
    if len(words) <= size:
        return [words]

    step = max(1, size - overlap)
    windows: list[list[str]] = []
    start = 0
    while start < len(words):
        windows.append(words[start : start + size])
        if start + size >= len(words):
            break  # last window reached the end; don't emit a tiny overlap-only tail
        start += step
    return windows


def chunk_sections(
    sections: list[LoadedSection],
    *,
    words_per_chunk: int = WORDS_PER_CHUNK,
    overlap_words: int = OVERLAP_WORDS,
) -> list[Chunk]:
    """Split loaded sections into overlapping chunks, preserving page/section.

    Each section is windowed independently (chunks never cross a page/heading
    boundary) and the resulting chunks are numbered sequentially across the whole
    document. The defaults target ~500-token chunks with ~60-token overlap; the
    parameters are exposed mainly so tests can use small, readable sizes.
    """
    chunks: list[Chunk] = []
    index = 0
    for section in sections:
        words = section.text.split()
        for window in _window_words(words, words_per_chunk, overlap_words):
            text = " ".join(window).strip()
            if not text:
                continue
            chunks.append(
                Chunk(
                    text=text,
                    index=index,
                    page=section.page,
                    section=section.section,
                    est_tokens=estimate_tokens(text),
                )
            )
            index += 1
    return chunks
