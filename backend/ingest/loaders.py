"""Document loaders — read a PDF / Markdown / text file into plain text sections.

The output is a list of :class:`LoadedSection`, each a span of text plus a
*locator* that later becomes citation metadata: a ``page`` number for PDFs, a
``section`` heading for Markdown, and neither for plain text. The chunker
(``chunker.py``) splits these sections; keeping the locator on the section means
every chunk knows which page or heading it came from, which is what Phase 7's
citations need.

Design choices:
* **PDF via pypdf, with a pdfplumber fallback per page.** pypdf is fast and pure
  Python; on pages where its simple text extraction comes back empty (some PDFs
  with awkward content streams), we fall back to pdfplumber, which is slower but
  more thorough. Both are listed in the plan ("pdf via pypdf/pdfplumber").
* **Failures raise :class:`LoaderError`, never crash.** A corrupt, encrypted, or
  empty file raises a clear, user-facing message; the upload pipeline records it
  as a failed document rather than letting an exception escape.

This module does pure I/O: no embeddings, no database, no network.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("eva.ingest.loaders")

# The file types the Library accepts. Markdown gets two common extensions.
SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt", ".text"}


class LoaderError(Exception):
    """A document could not be read (unsupported, corrupt, encrypted, or empty).

    Carries a short, user-facing message — the Library surfaces it verbatim as
    the failed document's reason, so it must read cleanly to a non-technical user.
    """


@dataclass
class LoadedSection:
    """One span of text from a document, with where it came from.

    ``page`` is 1-based for PDFs (``None`` otherwise); ``section`` is the nearest
    Markdown heading (``None`` otherwise). At most one is set for a given source
    type; both ``None`` means plain text with no internal structure.
    """

    text: str
    page: int | None = None
    section: str | None = None


def load_document(filename: str, data: bytes) -> list[LoadedSection]:
    """Load ``data`` (the raw bytes of ``filename``) into text sections.

    Dispatches on the file extension. Raises :class:`LoaderError` for an
    unsupported type, an unreadable/corrupt file, or a document with no
    extractable text (e.g. an empty file or a scanned PDF with no text layer).
    """
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise LoaderError(
            f"Unsupported file type '{ext or filename}'. "
            "Eva accepts PDF, Markdown (.md), and text (.txt) files."
        )

    if ext == ".pdf":
        sections = _load_pdf(data)
    elif ext in {".md", ".markdown"}:
        sections = _load_markdown(data)
    else:  # .txt / .text
        sections = _load_text(data)

    # Guard against files that "load" but contain nothing usable — a scanned PDF
    # with no text layer, or an empty document. Embedding empty text is pointless
    # and would create a zero-chunk document, so we fail clearly instead.
    if not any(s.text.strip() for s in sections):
        raise LoaderError(
            "No readable text found in this file. If it is a scanned PDF, it has "
            "no text layer Eva can index."
        )
    return sections


def _decode_text(data: bytes) -> str:
    """Decode text bytes as UTF-8, falling back to Latin-1, else fail clearly.

    UTF-8 covers virtually all real notes; Latin-1 rescues older Windows exports
    without ever raising (it maps every byte). If even that yields control-byte
    garbage we'd rather surface a failure than index mojibake — but in practice
    the Latin-1 fallback is enough for any genuine text file.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError as e:  # pragma: no cover — Latin-1 rarely fails
            raise LoaderError("This file is not readable as text.") from e


def _load_text(data: bytes) -> list[LoadedSection]:
    """Load a plain-text file as a single section with no locator."""
    return [LoadedSection(text=_decode_text(data))]


def _load_markdown(data: bytes) -> list[LoadedSection]:
    """Split Markdown into sections by heading, carrying the heading as locator.

    Lines starting with ``#``..``######`` open a new section whose ``section`` is
    the heading text; the body up to the next heading is that section's text. Any
    preamble before the first heading becomes a leading section with no heading.
    Splitting on headings (rather than blindly by size) keeps each chunk's
    ``section`` citation meaningful.
    """
    text = _decode_text(data)
    sections: list[LoadedSection] = []
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(LoadedSection(text=body, section=current_heading))
        buffer.clear()

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= hashes <= 6 and stripped[hashes : hashes + 1] in {" ", "\t"}:
                flush()  # close the previous section before starting a new one
                current_heading = stripped[hashes:].strip() or None
                continue
        buffer.append(line)
    flush()

    # A Markdown file with no headings at all still yields one section.
    if not sections:
        sections.append(LoadedSection(text=text.strip()))
    return sections


def _load_pdf(data: bytes) -> list[LoadedSection]:
    """Extract one section per PDF page (pypdf, with a per-page pdfplumber fallback).

    Each page becomes a :class:`LoadedSection` with its 1-based ``page`` number.
    Pages pypdf extracts as empty are retried with pdfplumber, which handles more
    awkward layouts. Corrupt or password-protected files raise :class:`LoaderError`.
    """
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover — declared in requirements
        raise LoaderError("PDF support is not installed.") from e

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Try an empty password (common for "owner-locked" but readable PDFs).
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 — any failure means we can't read it
                raise LoaderError(
                    "This PDF is password-protected, so Eva can't read it."
                )
        pages_text = [(p.extract_text() or "") for p in reader.pages]
    except LoaderError:
        raise
    except Exception as e:  # noqa: BLE001 — any parse failure → one clear message
        raise LoaderError(
            "This PDF appears to be corrupt or is not a valid PDF file."
        ) from e

    # For any page pypdf left empty, try pdfplumber once (slower, more thorough).
    if any(not t.strip() for t in pages_text):
        pages_text = _fill_empty_pages_with_pdfplumber(data, pages_text)

    return [
        LoadedSection(text=text, page=i + 1)
        for i, text in enumerate(pages_text)
        if text.strip()
    ]


def _fill_empty_pages_with_pdfplumber(
    data: bytes, pages_text: list[str]
) -> list[str]:
    """Re-extract only the empty pages with pdfplumber; return the merged list.

    Best-effort: if pdfplumber is unavailable or also fails, the original (empty)
    text is kept for that page — the document still loads with whatever pypdf got.
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover — declared in requirements
        return pages_text

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for i, page in enumerate(pdf.pages):
                if i < len(pages_text) and not pages_text[i].strip():
                    extracted = page.extract_text() or ""
                    if extracted.strip():
                        pages_text[i] = extracted
    except Exception as e:  # noqa: BLE001 — fallback only; never fatal
        log.warning("pdfplumber fallback failed: %s", e)
    return pages_text
