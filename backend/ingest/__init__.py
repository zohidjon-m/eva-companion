"""Corpus ingestion (Phase 6) — turning the user's books into retrievable chunks.

This package is the first half of the RAG story (Phase 7 builds retrieval on top).
It is deliberately split into three small, separately-testable pieces:

* ``loaders.py`` — read a PDF / Markdown / text file into plain text *sections*,
  each carrying a locator (page number for PDF, heading for Markdown). Pure I/O;
  no embeddings, no database.
* ``chunker.py`` — split those sections into ~500-token overlapping chunks,
  carrying ``source + page/section`` metadata. Pure computation; no I/O.
* ``corpus.py`` — the orchestrator: save the upload into the vault, run
  load → chunk → embed → index (via :mod:`memory.vector`), and keep a small
  on-disk manifest so the Library screen can list documents with chunk counts
  and a ``ready``/``failed`` status, and remove them.

The corpus lives entirely inside the user's vault (``local_vault/corpus/``) and
its vectors go into a ChromaDB collection (``corpus``) that is strictly separate
from the ``journals`` collection — recall never touches corpus, advice never
touches journals (EVA_SYSTEM_DESIGN §6).
"""
