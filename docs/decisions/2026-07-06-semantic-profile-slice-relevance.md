# Semantic Relevance for L3 Profile Slices

**Status:** Accepted
**Date:** 2026-07-06
**Owner:** Eva product/engineering

## Context

Phase R9 ("real profile injection and visible recall", commit `b085623`) decides which L3
profile facts get injected into a chat turn. A claim is a candidate only when
`profile._is_relevant()` finds a shared content word between the message and the claim text
— a bare, stopword-filtered, case-insensitive token intersection with no stemming.

This lexical gate is deliberately small (it prevents whole-profile injection and satisfies
R9's "unknown topics do not hallucinate profile facts" check), but it is brittle. A goal
worded *"Train at the gym four times a week"* surfaces for *"should I skip the **gym**
today?"* (shares "gym") yet **not** for *"thinking of bailing on my **workout**"* — the same
intent with zero shared words. That partially walks back the Phase 15 promise that Eva
references a stated goal *unprompted*, which now only holds when the wording lexically
overlaps.

The gap is known in the codebase itself: the R9 `_is_relevant` docstring calls the lexical
match "the eventual semantic retrieval seam", and the pieces to close it already exist —
`memory/vector.py` runs a local FastEmbed `bge-small-en-v1.5` model that powers L2 recall.

This work is an enhancement **beyond** the V2 realignment roadmap (R10 is L4 analytics, R11
is safety/packaging; neither touches slice selection), recorded here for traceability.

## Decision

Add a local, embedding-based **semantic relevance** check that runs **alongside** the
lexical gate. A claim is a prompt candidate when it matches lexically **OR** its cosine
similarity to the message is above a threshold.

Two decisions were made explicitly:

- **Augment, not replace.** Lexical matching is kept and short-circuits first, so every
  existing R9 relevance guarantee and test holds unchanged; semantics only *add* recall for
  paraphrases. A full replacement would retune all existing behavior and risk the precision
  tests, for no additional benefit.
- **Behind a config flag.** A module constant gates the whole semantic half, so it can be
  disabled instantly if a threshold surfaces something odd during a live demo.

## Approach

The embedding mechanics live in `memory/vector.py`; `memory/profile.py` only consumes a
similarity list and applies a threshold.

- **`vector.semantic_scores(query_text, candidate_texts) -> list[float]`** reuses the
  existing asymmetric embed pair — the chat message is the query side (with the bge
  instruction prefix), each claim text is the passage side — and returns one cosine
  similarity per candidate. One batched embed call scores the whole profile per turn.
  Cosine is computed in pure Python (`_cosine`), so no numpy dependency is added.
- **`profile.retrieve_slices`** computes a `{claim_text: score}` map once per turn via
  `_candidate_texts(prof)` (the exact strings the relevance loop checks), then widens all
  nine relevance checks through a `_relevant()` closure:
  `_is_relevant(text, tokens) or score >= SEMANTIC_SLICE_THRESHOLD`.
- Everything downstream — the evidence-required gate, confidence floor, stale/needs-review
  exclusion, ranking, `advice_mode`, and the `MAX_PROMPT_SLICES` cap — is unchanged.
  Semantics only change *which candidates enter* the pipeline, never how they are filtered
  for evidence or ranked, so the anti-hallucination guarantees still apply to every
  semantically-surfaced claim.

### Configuration

- `SEMANTIC_SLICE_MATCHING = True` — flip to `False` for instant rollback to lexical-only.
- `SEMANTIC_SLICE_THRESHOLD = 0.62` — cosine similarity; higher is stricter. A conservative
  starting value, to be tuned empirically to sit in the gap between on-topic paraphrases
  (~0.6–0.7) and unrelated messages (~0.3–0.5), the same way `retrieval.py` centres its
  distance thresholds.

## Privacy And Security

Embeddings are computed by the already-bundled local FastEmbed model and never leave the
machine, so this holds the privacy-first guarantee in both local and online-API modes — no
profile text is sent anywhere new. `vector.py` already forces the embedding stack offline at
import, so the relevance check triggers no outbound call.

## Graceful Degradation

`profile._semantic_scores_map` wraps the embedding call in a broad `try/except`: any failure
— most likely the embedding model not yet downloaded on a fresh install — logs once and
falls back to an empty map, so the turn degrades to the lexical gate rather than crashing the
reply. The chat path never depends on the semantic half succeeding.

## Consequences

Positive:

- Eva surfaces a stated goal for semantically-related but differently-worded messages,
  restoring the "she remembers, unprompted" moment across natural paraphrasing.
- No new dependency; reuses the existing embedding model and asymmetric query/passage design.
- Fully local, so the privacy guarantee is untouched.
- Instant rollback via the flag; the lexical baseline remains the safety floor.

Tradeoffs:

- Adds one query embed plus one batched passage embed per turn on the context-assembly path
  (runs in `asyncio.to_thread`, alongside the existing recall embed). Claim embeddings are
  recomputed each turn; a cache keyed on profile revision is a possible future optimization.
- Introduces a threshold that must be tuned. Too low reintroduces the whole-profile-injection
  and false-positive risk R9 guards against; the conservative default and the flag mitigate
  this until it is measured.
- `_candidate_texts` duplicates the relevance loop's field list. A drift guard test
  (`test_candidate_texts_match_loop_strings`) prevents the two silently diverging.

## Current Implementation Notes

- `memory/vector.py`: added `_cosine` and public `semantic_scores`.
- `memory/profile.py`: added the two config constants, `_candidate_texts`,
  `_semantic_scores_map`, and the `_relevant` closure inside `retrieve_slices`; the nine
  relevance call sites now go through it. Public signatures are unchanged, so `engine/turn.py`
  needed no change.
- `tests/test_profile.py`: four model-free tests (stubbing `vector.semantic_scores`) covering
  the paraphrase win, threshold precision, flag-off fallback, and the drift guard.
- Verified in the current environment: `test_profile.py` 29/29 and the engine/chat suites
  41/41 pass. Because FastEmbed and ChromaDB were not installed here, the existing precision
  tests passed via the lexical-fallback path — which also confirms graceful degradation, but
  means the real embedding model was not exercised.

## Follow-Up Checks

Before this is considered demo-ready:

- On a machine with the embedding model present, run `test_profile.py` and confirm the
  negative/precision tests (`test_unknown_topic_does_not_surface_profile_facts`, the Daniel
  off-topic test) still pass with **real** semantic scores, not just the lexical fallback.
- Tune `SEMANTIC_SLICE_THRESHOLD` against real embeddings: paraphrases such as
  *"bailing on my workout"* / *"skip leg day"* should clear it; *"what should I cook for
  dinner"* / *"nothing in particular"* should stay below it.
- Manual end-to-end: with a real profile, send *"thinking of bailing on my workout"* and
  confirm the `profile → N slice(s) in context` log fires and the reply references the
  fitness goal; flip `SEMANTIC_SLICE_MATCHING` off and confirm it goes quiet again.
