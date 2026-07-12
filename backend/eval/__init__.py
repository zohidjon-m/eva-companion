"""Eva's evaluation harness (offline, local-first).

Slice 1 is the **trace substrate**: :func:`eval.trace.trace_turn` runs the real
chat pipeline (``classify → assemble_context → check_in``) over one input and
snapshots exactly what entered the context window — the intent and its stratum,
every retrieved item with its distance, which items were kept vs. dropped, the
per-slot token cost, the verbatim assembled system prompt, and enough provenance
to replay it. See ``docs/EVAL_HARNESS_DESIGN.md`` for the full design.

Nothing here talks to the network; it reuses the same local retrieval the app
uses. The only model call is the optional intent fallback (on an ambiguous turn)
and optional generation (``run_model=True``) — both off the critical path of the
deterministic trace.
"""
