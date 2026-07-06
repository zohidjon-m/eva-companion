# Eva V2 Codebase Realignment Plan

Date: 2026-07-02

This document compares the current codebase to `docs/IMPLEMENTATION_PLAN_V2.md`
and turns the gaps into an implementation order. It is not a replacement for the
V2 plan; it is the bridge from the repo as it exists today to the V2 target.

The current repo is ahead of the README and has many later surfaces already
present. The main problem is not missing UI. The main problem is that several
V2 foundations are incomplete while later V1/demo seams are already built on
top of them.

## Working Rules

1. Treat Markdown journal files as L0 source of truth.
2. Keep one implementation phase per session and commit each completed phase.
3. Do not start real L3/L4 work until stable entry identity, hash-gated rebuild,
   and edit recompute are correct.
4. Do not ship seeded or hand-authored values as product truth.
5. Hybrid provider mode is accepted: local AI is the privacy-first default, and
   online API mode is opt-in and restricted to the configured provider host.
   Any new ambiguity about provider privacy or runtime network access is a stop
   gate, not an implementation detail.

## Current Baseline

Current code has these major surfaces:

- Backend app routes for health, chat, journal, corpus, STT, settings, profile,
  mood, graph, growth, privacy audit, and AI provider setup in `backend/app.py`.
- L0 vault writes in `backend/memory/vault.py`.
- L1 SQLite schema and helpers in `backend/memory/schema.sql` and
  `backend/memory/db.py`.
- Extraction and capture pipeline in `backend/memory/extract.py` and
  `backend/memory/capture.py`.
- ChromaDB journal/corpus vector path in `backend/memory/vector.py`.
- RAG and memory recall in `backend/memory/retrieval.py`.
- Profile, graph, and growth seams in `backend/memory/profile.py`,
  `backend/memory/graph.py`, and `backend/memory/growth.py`.
- React surfaces for chat, journal, library, insights, profile, settings, and
  first-run setup.

Current known drift:

- `README.md` still says "Status: Phase 0", but the code is far past Phase 0.
- `docs/IMPLEMENTATION_PLAN_V2.md` is currently untracked.
- The old demo implementation plan is deleted in git status.
- `DEMO_SCRIPT.md` is deleted in git status.
- Hybrid V2 is accepted by `docs/decisions/2026-07-01-hybrid-llm-provider.md`;
  docs now need to consistently reflect local-default plus opt-in online API
  mode.
- Backend tests are not clean in this workspace. With a workspace pytest temp
  directory, the first failure is `tests/test_chat_rag.py::test_vent_turn_bypasses_retrieval`,
  where `/chat` returns `runtime_missing` because provider/runtime startup now
  checks for a missing `llama-server` binary before the mocked stream path can
  run.
- Frontend build was not verified here because `npm` is not on PATH and
  `ui/node_modules` is absent.

## V2 Comparison

| V2 area | Current codebase status | Required action |
|---|---|---|
| Phase 0-1 scaffold and model runtime | Mostly present. `backend/llm/server.py` uses native `llama-server`; provider work added later. | Fix docs/tests around provider runtime. Hybrid mode is accepted; align docs and guard tests around local-default plus opt-in API mode. |
| Phase 2 L0/L1 capture | Present. Vault, DB, extraction, mood copy, and summary embedding exist. | Keep. Audit for rebuildability and idempotence before building L3. |
| Phase 3 full L1 | Partly present as JSON columns. V2 asks for full episode capture plus rebuild script; normalized subtables are not present. | Decide whether JSON-column L1 is accepted. Add `scripts/reextract.py` either way. |
| Phase 3.5 stable `uid` and `source_hash` | Not V2-complete. Current entries use UUID `id` in an HTML comment. No `uid` column, no `source_hash`, no hash gate. | Implement before all L3/L4 work. This is the first real V2 foundation task. |
| Phase 4 L2 semantic index | Partial. `journals` and `corpus` collections exist. No `episodes` sub-index and no `scripts/reindex.py`. | Add episodes collection, metadata contract, and reindex script after Phase 3.5. |
| Phase 5-7 UI shell, chat, journal | Mostly present. | Keep, but do not treat as proof that V2 foundations are complete. |
| Phase 7.5 editable entries | Partial and not V2-compliant. Current edit rewrites a day file with `.bak`, reuses background extraction, and lacks revision history/source hash. | Replace with revision-preserving edit plus synchronous single-entry recompute. |
| Phase 8 library | Mostly present with corpus ingestion and separate vector collection. | Keep. Re-check thresholds after vector changes. |
| Phase 9 conversation engine | Partial. There is a 3-class classifier and persona mode in prompt assembly. No 5-class state machine, no `check_in`/`check_out`, no recent L1 episode assembly. | Upgrade after L2 and edit foundation are stable. |
| Phase 10 grounded answers | Mostly present for corpus passages and citation frames. Retrieval currently fires for `question` and `advice_request`. | Align intent labels with V2 `ask_advice` gate before finalizing. |
| Phase 11-12 voice | Present at module/API level with lazy STT/TTS and sentence queue. | Verify with real dependencies/weights later; do not block memory foundation. |
| Phase 13 L3 user model | Not implemented. `profile.py` is marked `# DEMO-STUB`. No `apply_ops`, decay, contradiction handling, evidence rejection, or rebuild. | Build only after stable `uid`, source hash, reextract, and edit recompute exist. |
| Phase 14 consolidation | Not implemented. No `backend/memory/consolidate.py`, no scheduler, no nightly/weekly pipeline, no rollup builder. | Build after L3 operation grammar exists. |
| Phase 15 L3 read loop and profile evidence | Partial. Profile screen exists and profile slices can enter the prompt, but they come from the stub. | Rewire to real L3 and show evidence pointers. |
| Phase 16 recall chips | Present using journal summary recall. | Keep, but rebuild on top of completed Phase 4 L2. |
| Phase 17 mood analytics | Present using `mood_series`. | Keep. Make idempotent when edit recompute lands. |
| Phase 18 graph | Partial/demo. `graph.py` is marked `# DEMO-STUB`; live graph still uses curated lexicon/hypotheses rather than full V2 deterministic edges plus evidence-gated hypotheses. | Replace after consolidation can produce real candidates. |
| Phase 19 growth and verification | Partial/demo. `growth.py` computes mood/theme deltas, but no behavior-vs-goal/open-loop deltas and no verification pass. | Build after L3/L4 consolidation. |
| Phase 20 guardrails | Partial. Net guard and crisis keyword addendum exist; no full `check_in`/`check_out` rails. | Add after conversation engine is structured. |
| Phase 21 polish/debug | Partial. First-run/settings/offline UI exists; debug panel for context/intent/evidence/L3 ops is absent. | Add after engine produces real debug data. |
| Phase 22 hardening/package/demo | Partial. Demo scripts exist, but demo script doc is deleted and packaged build verification is not established. | Final hardening phase only after real V2 data path is complete. |

## Stop Gates Before Implementation

### Gate A - Privacy And Provider Direction

**Decision: Hybrid V2 accepted.**

Eva keeps local AI as the privacy-first default and supports opt-in online API
providers through the accepted ADR:
`docs/decisions/2026-07-01-hybrid-llm-provider.md`.

Documentation, privacy copy, network guard tests, and threat-model language must
describe this explicitly: local mode blocks outbound runtime calls except
approved first-run downloads; online API mode permits only the selected provider
host.

### Gate B - Canonical Plan Source

**Decision: `docs/IMPLEMENTATION_PLAN_V2.md` is canonical.**

README and implementation docs should point to the V2 plan. The deleted old
demo implementation plan should not be restored as the active build plan.

### Gate C - Test Baseline

Before changing memory foundations, repair the test harness enough to isolate
feature changes:

- Fix chat tests so provider/runtime checks do not bypass mocked model streams.
- Run backend tests with a workspace temp directory on Windows.
- Establish the frontend build command for this workspace.

## Implementation Order

### R0 - Documentation And Status Realignment

Goal: make the repo tell the truth before code changes.

Scope:

- Track `docs/IMPLEMENTATION_PLAN_V2.md`.
- Add this realignment plan to docs.
- Update `README.md` status from Phase 0 to the current mixed/V2-realignment
  state.
- Replace references to the deleted old demo plan with V2 plan links.
- Update root instructions and V2 plan language from the old local-only framing
  to local-default hybrid-provider.
- Leave `DEMO_SCRIPT.md` restoration/replacement to the final hardening/demo
  phase unless requested separately.

Files to read:

- `README.md`
- `docs/IMPLEMENTATION_PLAN_V2.md`
- `docs/decisions/2026-07-01-hybrid-llm-provider.md`
- `AGENTS.md`

Checks:

- `git status --short` clearly shows only expected docs changes.
- README links point to existing docs.
- The stale-term search from the phase test plan has only intentional
  historical/audit references.

### R1 - Test Harness Recovery

Goal: get a trustworthy baseline before touching storage contracts.

Scope:

- Fix `/chat` tests so mocked model streams do not require a real
  `llama-server` binary.
- Keep the missing-runtime behavior covered by a separate test.
- Document Windows test invocation using `--basetemp` inside the workspace.
- Establish frontend build tooling for the repo.

Likely files:

- `backend/app.py`
- `backend/llm/providers.py`
- `backend/tests/test_chat_rag.py`
- `backend/tests/test_chat_surface.py`
- `backend/tests/test_llm.py`
- `README.md`

Checks:

- `python -m pytest -q --basetemp <workspace-temp>` passes or has only known,
  documented dependency skips.
- Frontend `npm run build` or equivalent works in a prepared dev environment.

### R2 - V2 Phase 3.5: Stable Entry Identity And Source Hash

Goal: make every evidence pointer durable before building L3.

Scope:

- Add stable `uid` to L0 headers or a backward-compatible parser that treats the
  existing HTML comment id as legacy and rewrites to the V2 header format through
  a one-time migration.
- Add `entries.uid` unique column if keeping `id` as internal DB row identity, or
  explicitly make `entries.id` the stable uid and update docs to say so.
- Add `extractions.source_hash`.
- Ensure every derived layer uses the stable uid for evidence and vector ids.
- Add a migration/backfill for existing day files.
- Add hash calculation for the exact L0 block text used for extraction.

Likely files:

- `backend/memory/vault.py`
- `backend/memory/schema.sql`
- `backend/memory/db.py`
- `backend/memory/capture.py`
- `backend/tests/test_vault.py`
- `backend/tests/test_db_schema.py`
- new `scripts/backfill_entry_uids.py` if needed

Checks:

- Existing entries get stable uids exactly once.
- Re-running the migration is idempotent.
- Editing text does not change uid.
- Source hash changes when body text changes and stays stable otherwise.
- Tests prove uid survives DB deletion and rebuild.

### R3 - V2 Phase 3: Reextract And Rebuild From L0

Goal: prove SQLite L1 is derived from Markdown and can be rebuilt.

Scope:

- Add `scripts/reextract.py`.
- Rebuild `entries`, `extractions`, `mood_series`, and FTS from L0.
- Preserve uid from Markdown.
- Skip unchanged entries using `source_hash`.
- Requeue or retry `null_stored` rows according to the extraction contract.
- Decide whether current JSON columns satisfy V2 full L1 or whether normalized
  subtables are required. If normalized tables are required, add them here.

Likely files:

- `backend/memory/vault.py`
- `backend/memory/db.py`
- `backend/memory/extract.py`
- `backend/memory/capture.py`
- new `scripts/reextract.py`
- `backend/tests/test_capture.py`
- `backend/tests/test_extract_parse.py`

Checks:

- Delete `eva.db`, run reextract, counts match the Markdown vault.
- Uids before and after rebuild are identical.
- Unchanged entries are skipped by hash.
- Malformed extraction output still stores `null_stored` without losing L0.

### R4 - V2 Phase 4: Complete L2 Rebuildability

Goal: make vector storage fully derived and complete.

Scope:

- Add `episodes` ChromaDB collection for open loops and notable episodic units.
- Store metadata with uid, date, mood, themes, type, and unit id.
- Add `scripts/reindex.py` to rebuild `journals`, `episodes`, and `corpus`
  vectors where applicable.
- Keep seeded/demo data excluded from live recall by default.
- Update retrieval thresholds only after measuring with current embeddings.

Likely files:

- `backend/memory/vector.py`
- `backend/memory/retrieval.py`
- `backend/memory/capture.py`
- new `scripts/reindex.py`
- `backend/tests/test_retrieval.py`
- `backend/tests/test_corpus.py`

Checks:

- Delete Chroma directory, run reindex, recall behavior is equivalent.
- `episodes` recall can find an open loop or notable moment.
- No relevant match returns no memory.

### R5 - V2 Phase 7.5: Revisioned Edits And Single-Entry Recompute

Goal: make editing safe before claims depend on entries.

Scope:

- Replace day-file rewrite semantics with revision-preserving storage.
- Keep the original version retrievable.
- Add `recompute_entry(uid)` as a synchronous storage-layer operation.
- Recompute L1 and L2 idempotently for exactly one uid.
- Update mood series instead of inserting duplicate points on every edit.
- Update FTS if FTS remains part of the search contract.
- Move API toward `PUT /entries/{uid}` or document why existing journal route is
  the compatibility wrapper.

Likely files:

- `backend/memory/vault.py`
- `backend/memory/db.py`
- `backend/memory/capture.py`
- `backend/memory/vector.py`
- `backend/app.py`
- `ui/src/journal/*`
- `backend/tests/test_journal_surface.py`

Checks:

- Edit changes visible text, L1 extraction, FTS, mood point, and vector.
- Original revision is still available.
- uid is unchanged.
- One edit creates one recompute, not a full rebuild.

### R6 - V2 Phase 9/10: Conversation Engine Alignment

Goal: align the read loop with the V2 engine contract.

Scope:

- Decide final intent labels: V2 uses `vent`, `process`, `ask_info`,
  `ask_advice`, `ambient`; current code uses `vent`, `question`,
  `advice_request`.
- Refactor chat turn flow into named steps:
  `classify -> assemble_context -> check_in -> reason -> check_out -> persist`.
- Add recent L1 episode assembly.
- Ensure corpus retrieval is reachable only through the advice/info gate chosen
  by the privacy/product decision.
- Stream intent/persona/debug metadata for the later debug panel.

Likely files:

- `backend/intent/classifier.py`
- `backend/prompts/assembly.py`
- `backend/memory/retrieval.py`
- `backend/app.py`
- `ui/src/chat/*`

Checks:

- Pure vent never retrieves corpus.
- Advice/info request retrieves only allowed context.
- Model prompt carries no unavailable or fake context.

### R7 - V2 Phase 13: Real L3 User Model Engine

Goal: replace the profile demo seam with evidence-backed claims.

Scope:

- Implement claim store with evidence pointers to stable uids.
- Implement operation grammar: add, strengthen, weaken, note contradiction,
  mark resolved, link evidence, and user anchor operations.
- Reject any model operation without valid evidence.
- Implement deterministic apply, decay, contradiction handling, and source=user
  anchor protection.
- Generate `profile.md` from structured claims.
- Add `scripts/rebuild_profile.py` to replay from L1 and user anchors.
- Add edit self-heal flagging for claims whose evidence uid changed.

Likely files:

- `backend/memory/profile.py`
- `backend/memory/db.py`
- `backend/memory/schema.sql`
- `backend/prompts/*`
- `backend/tests/test_profile.py`
- new `scripts/rebuild_profile.py`

Checks:

- Three real entries can produce a higher-confidence goal/identity claim with
  three evidence pointers.
- Unsupported operations are rejected.
- A user-edited anchor cannot be weakened by the model.
- Rebuild recreates profile from L1 plus anchors.

### R7.5 - Evidence-Backed Identity And Emotional Baseline

Status: implemented. Identity/baseline now carry field-keyed `provenance`
(`schema_version` 2); the engine authors them via `set_identity` / `add_principle`
/ `add_baseline_item` under the evidence gate; `typical_mood` is code-derived from
L1 mood history; fields anchor by synthetic path (`identity.stated_self`, …) and
survive rebuild.

Goal: make the L3 `identity` and `emotional_baseline` sections real, closing the
last demo seam R7 left in place.

Context: R7 shipped the evidence-backed operation engine for goals, patterns,
open loops, relationship notes, and the watch list. It deliberately did not
author `identity` or `emotional_baseline`: the §7.3 grammar has no verb that
creates them, and (unlike goals/patterns) they are singleton dicts with no
confidence/evidence-claim shape. So the rebuild currently *preserves* those two
sections from the prior profile rather than deriving them from L1 — the demo/seed
values survive untouched. This is a scope/design decision, not a bug, and needs
its own phase.

Scope:

- Decide the claim shape for identity and baseline: either promote them to
  evidence-carrying claims (e.g. `stated_self`/`principles` and
  `typical_mood`/`known_triggers`/`what_helps` each gain `evidence` +
  `confidence` + `source`), or keep the §7.2 dict shape but require an evidence
  pointer set per field. Update §7.2 and bump `SCHEMA_VERSION` with a migration.
- Add operation verbs for them (e.g. `set_identity`, `add_principle`,
  `update_baseline`) under the same evidence gate — no field may be written
  without a valid entry uid.
- Derive `typical_mood` deterministically from L1 mood history (code counts, model
  never does the arithmetic); reserve the model for narrating `stated_self` /
  `principles` over evidence-counted candidates.
- Extend `scripts/rebuild_profile.py` to rebuild identity/baseline from L1 while
  still preserving user anchors on those fields.
- Extend the self-heal flag and `render_markdown`/`parse_markdown` round-trip to
  cover the new fields.

Likely files:

- `backend/memory/operations.py`
- `backend/memory/profile.py`
- `backend/memory/rebuild_profile.py`
- `backend/memory/schema.sql` / migration
- `backend/prompts/profile_operation.md`
- `docs/EVA_MEMORY_ARCHITECTURE.md` (§7.2/§7.3)
- `backend/tests/test_profile_operations.py`

Checks:

- Identity and baseline in a rebuilt profile carry evidence pointers into L1, not
  seed values.
- `typical_mood` matches a hand-computed average over the L1 extraction moods
  (`extractions.mood`, the canonical column `mood_series` is copied from), and is
  refreshed on both the full rebuild and the incremental update seam — never stale.
- A user-corrected identity/baseline field is anchored and cannot be overwritten
  by the model.
- No identity/baseline field is written without valid evidence.

### R8 - V2 Phase 14: Consolidation Scheduler And Rollups

Goal: build the write loop that updates L3/L4 without blocking chat.

Scope:

- Add `backend/memory/consolidate.py` with `on_save`, `run_nightly`, and
  `run_weekly`.
- Add scheduler integration with model access serialization.
- Add open-loop reconciliation.
- Add deterministic weekly miners for themes, emotions, open loops, and
  behavior-vs-goal contradictions.
- Add week, month, and era digest rollups.
- Add manual test-only consolidate trigger if desired.

Likely files:

- new `backend/memory/consolidate.py`
- new `backend/scheduler.py`
- `backend/llm/client.py`
- `backend/memory/profile.py`
- `backend/memory/graph.py`
- `backend/memory/growth.py`
- `backend/tests/*consolidate*`

Checks:

- Scheduler never interleaves model calls with active chat streaming.
- Weekly miner produces evidence-counted candidates before any model narration.
- Open loops can resolve from later entries.

### R9 - V2 Phase 15/16: Real Profile Injection And Visible Recall

Goal: make Eva's memory visible and auditable using real L3/L2.

Scope:

- Pull `retrieve_slices(topic)` from real L3 every turn.
- In advice mode, include relevant goals/values alongside corpus passages.
- Profile screen shows claims and evidence entries.
- Keep recall chips backed by L2 thresholded memories.
- Add graceful empty states for young profiles.

Likely files:

- `backend/memory/profile.py`
- `backend/memory/retrieval.py`
- `backend/prompts/assembly.py`
- `backend/app.py`
- `ui/src/profile/*`
- `ui/src/chat/*`

Checks:

- A question tied to a known goal receives relevant profile context.
- User can inspect which entries justify a claim.
- Unknown topics do not hallucinate profile facts.

### R10 - V2 Phase 17/18/19: Real L4 Analytics

Goal: replace demo graph/growth seams with computed analytics.

Scope:

- Keep mood analytics SQL and make it edit-safe.
- Replace graph lexicon stubs with nodes from real L1 entities, themes, goals,
  problems, and emotions.
- Add deterministic co-occurrence, temporal, and similarity edges.
- Add evidence-gated hypothesis edges that are clearly marked.
- Expand growth analytics to include open-loop resolution and goal-aligned versus
  goal-contradicting behavior counts.
- Add verification pass for high-impact claims and growth statements.

Likely files:

- `backend/memory/graph.py`
- `backend/memory/growth.py`
- `backend/memory/retrieval.py`
- `backend/memory/profile.py`
- `ui/src/insights/*`
- `backend/tests/test_insights_*`

Checks:

- Every graph node/edge has evidence entries.
- Hypothesis edges are visually and structurally distinct.
- Growth numbers match hand-computed periods.
- Unsupported high-impact narration is dropped by verification.

### R11 - V2 Phase 20/21/22: Safety, Debug, Packaging

Goal: make the real V2 system safe and demoable.

Scope:

- Add light `check_in` and `check_out` guardrail nodes.
- Keep crisis path humane, non-clinical, and fail-closed.
- Add debug panel showing assembled context, intent, retrieved evidence, and L3
  operations.
- Restore or recreate demo script documentation.
- Verify packaging on the target OS.
- Run failure drills on packaged build.

Likely files:

- `backend/safety/*`
- `backend/app.py`
- `ui/src/settings/*`
- `ui/src/layout/*`
- `packaging/*`
- `scripts/demo_*`
- `DEMO_SCRIPT.md`

Checks:

- Crisis message receives safe care response.
- Out-of-scope request declines without advice.
- Debug panel shows real evidence and operations.
- Packaged app works offline after setup.

## Recommended First Work Session

Start with **R0 - Documentation And Status Realignment** if the goal is to make
the project navigable.

Start with **R1 - Test Harness Recovery** if the goal is to resume coding safely.

Start with **R2 - Stable Entry Identity And Source Hash** if tests are already
accepted as temporarily red and the priority is V2 memory correctness.

Do not start Phase 13 L3 yet. L3 evidence pointers depend on durable entry
identity, source hashes, reextract, reindex, and revisioned edits. Building L3
first would harden the current identity/edit bugs into the core memory engine.
