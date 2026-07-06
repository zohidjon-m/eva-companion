# Wiring the Growth Report's High-Impact Claim Verification to a Real Model

**Status:** Accepted
**Date:** 2026-07-06
**Owner:** Eva product/engineering

## Context

Phase R10 ("real L4 analytics", commit `5ab4dde`) added a verification pass so the
descriptive growth report never asserts a high-impact claim about the user unless
the cited evidence actually supports it. The pieces were built and tested:

- `memory/verification.py` — `verify_claim_supported(claim, evidence, call_model)`
  (async, model-backed) and `verify_claim_with_callable(claim, evidence, verifier)`
  (sync seam for tests). Both fail closed: no verifier / no evidence → the claim is
  dropped.
- `memory/growth.py` — `compare_periods(..., verifier=...)` computes candidate
  high-impact claims (more open loops resolved recently; behaviors counted against
  stated goals) and only returns those a verifier confirms.
- `ui/src/insights/GrowthView.tsx` — renders a `verified_claims` section.

The gap: the live endpoint `GET /insights/growth` called `growth.compare_periods`
**without** passing a verifier. With `verifier=None`, `verify_claim_with_callable`
returns `None` for every candidate, so `verified_claims` was **always empty in
production**. The feature was fail-closed and safe, but permanently off — supported
claims were dropped alongside unsupported ones, and the UI section never rendered.
The R10 check "unsupported high-impact narration is dropped by verification" passed
only because *all* narration was dropped.

This is the R10 follow-up to close that seam. V2 is local-first but also supports
online LLMs, so the fix had to work with whichever provider the user selected.

## Decision

Wire the growth report's high-impact claim verification to the shared model path,
reusing the exact pattern `memory/consolidate.py` already uses
(`verify_claim_supported(claim, evidence, call_model or operations._llama_server_call)`).

Two decisions were made explicitly:

- **Reuse the one model-access path, don't invent a provider branch.** The model
  caller is `operations._llama_server_call`, which routes through
  `llm.client.complete_chat` → `providers.selected_provider()`. That resolves to the
  user's selected provider — local `llama-server` or an online LLM — at call time, so
  the growth verifier is provider-agnostic for free. No local-vs-online logic lives in
  the analytics code.
- **Keep failing closed, and keep the sync seam.** The synchronous
  `compare_periods(..., verifier=...)` path is unchanged, so every existing R10 test
  holds. The live endpoint adds a real (async, model-backed) gate on top; when no
  provider is configured it passes `call_model=None` and behavior is byte-identical to
  before — claims are dropped rather than surfaced unverified.

## Approach

Candidate selection (which statements are high-impact) is separated from
verification (whether a candidate passes), so the sync and async gates consider the
same claims from the same evidence.

- **`growth._claim_candidates(pa, pb)`** — pure/deterministic, returns
  `(claim, entry_ids, evidence)` for each high-impact candidate. Both gates build on it.
- **`growth._verified_claims`** (sync, test seam) and **`growth._verified_claims_async`**
  (model-backed) share `_claim_candidates`; the async one awaits
  `verification.verify_claim_supported`.
- **`growth._build_report(pa, pb, verified_claims)`** — the report assembly, extracted
  from `compare_periods` so both the sync and async entry points produce an identical
  report shape and only differ in how `verified_claims` was gated.
- **`growth.compare_periods_verified(...)`** and **`growth.auto_compare_verified(...)`**
  — async entry points that compute the two period summaries, verify candidates via
  `call_model`, and assemble the report.
- **`app.insights_growth`** is now `async`. It selects the caller once —
  `operations._llama_server_call` when `llm.client.provider_configured()` is true,
  else `None` — and awaits `compare_periods_verified` / `auto_compare_verified`. All
  request validation, the four-date contract, `include_seeded`, and the empty-vault
  shape are unchanged.

The model call runs at background priority (`_llama_server_call` uses
`complete_chat(..., priority=False)`), so verifying growth claims never blocks a
real-time chat turn — the same guarantee the R8 consolidation path relies on.

## Privacy And Security

No new outbound surface. The verifier reuses the single model-access path the rest
of the backend already uses; in local mode nothing leaves the machine, and in
online mode it uses the provider the user already configured and consented to for
chat. The bounded yes/no prompt (`render_verification_prompt`) sends only the claim
text and the evidence entry summaries that the report is already derived from — no
new data is exposed.

## Graceful Degradation

Fail-closed at every layer:

- No provider configured → `call_model=None` → `verify_claim_supported` returns
  `None` → the claim is dropped. Identical to the pre-wiring behavior; the report
  still renders, just without high-impact claims.
- A model/transport error or an undecided ("neither yes nor no") answer →
  `verify_claim_supported` returns `None`/`False` → the claim is dropped, never
  surfaced on a failed check.

The rest of the growth report (entry counts, mood delta, theme shifts, open-loop and
behavior deltas, narrative, closing question) is pure computation and does not depend
on the model, so it is unaffected whether or not a provider is present.

## Consequences

Positive:

- `verified_claims` and its UI section are now live: when a provider is available and
  confirms a claim, the user sees it. The R10 verification pass is no longer a no-op.
- Local-first and online modes both work with no provider-specific code in the
  analytics layer.
- The sync `verifier` seam and all R10 tests are untouched; the change is additive.

Tradeoffs:

- `GET /insights/growth` can now touch the model (previously a pure DB read). It is a
  manual, low-frequency insights request at background priority, so the cost is a
  bounded yes/no call per high-impact candidate (at most two per report) and it never
  blocks chat. A fresh report with candidates present will be slightly slower than the
  old pure-read.
- `_claim_candidates` is the single source of truth for candidate wording; the two
  gates depend on it staying in sync (they do, by construction — both call it).

## Current Implementation Notes

- `backend/memory/growth.py`: added `_claim_candidates`, `_verified_claims_async`,
  `_build_report`, `compare_periods_verified`, and `auto_compare_verified`;
  `compare_periods`/`_verified_claims` refactored onto the shared helpers with no
  behavior change.
- `backend/app.py`: `insights_growth` is now `async`, selects `call_model` from the
  configured provider (or `None`), and awaits the verified comparison functions.
- `backend/tests/test_insights_growth.py`: added three tests — a direct async test of
  `compare_periods_verified` (model says yes → surfaced; no → dropped; `None` → fail
  closed) and two endpoint tests (configured+confirming provider surfaces claims;
  no provider drops them).
- Verified in the current environment: `test_insights_growth.py` 11/11, full backend
  suite 413 passed / 2 skipped. The model was faked in tests (no local model present
  here), which also exercises the fail-closed path.

## Follow-Up Checks

Before this is considered demo-ready:

- On a machine with a real provider (local `llama-server` running, or an online key
  configured), open the Growth screen for a period with a genuine high-impact
  candidate and confirm a supported claim renders, and that toggling the provider off
  makes it go quiet again.
- Confirm the yes/no verifier is well-calibrated on real evidence: a clearly supported
  claim should clear it; a claim whose evidence is thin or contradictory should be
  dropped.
