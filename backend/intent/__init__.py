"""Intent classification — the listen-first gate in front of retrieval.

Eva's discipline (EVA_MEMORY_ARCHITECTURE §5.9, EVA_SYSTEM_DESIGN §7.2) is that
advice is *pulled*, never pushed: the corpus is only fetched when the person is
actually asking for information or guidance. That gate lives here, not in the
prompt — if a venting turn never retrieves, the model literally cannot reach for
a passage to advise from.

Phase 7 ships a minimal three-class classifier (``vent`` / ``question`` /
``advice_request``). It is deliberately small and behind a clearly-marked seam so
the real five-class intent engine plugs in later without touching the call site.
"""
