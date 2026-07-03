"""Intent classification — the listen-first gate in front of retrieval.

Eva's discipline (EVA_MEMORY_ARCHITECTURE §5.9, EVA_SYSTEM_DESIGN §7.2) is that
advice is *pulled*, never pushed: the corpus is only fetched when the person is
actually asking for information or guidance. That gate lives here, not in the
prompt — if a venting turn never retrieves, the model literally cannot reach for
a passage to advise from.

R6 aligns this to the V2 five-class taxonomy (``vent`` / ``process`` /
``ask_info`` / ``ask_advice`` / ``ambient``, EVA_SYSTEM_DESIGN §5.11). A cheap
deterministic rule layer resolves the vast majority of turns; only a genuinely
ambiguous residue falls through to one small model call. The rest of the app
depends only on ``IntentResult.label`` / ``.retrieves``.
"""
