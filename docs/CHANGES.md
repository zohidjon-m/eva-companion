# EVA — Design Document Changes

**Applied:** 2026-06-15 · Based on `AUDIT.md`
**Files changed:** `EVA_MEMORY_ARCHITECTURE.md` · `EVA_SYSTEM_DESIGN.md` · `EVA_DEMO_IMPLEMENTATION_PLAN.md` · `eva_system.md`

---

## EVA_MEMORY_ARCHITECTURE.md

### Added: Section 7 — Concrete schemas (entire new section, ~270 lines)

Addresses audit findings C1, C2, S2, S4.

**7.1 — L1 SQLite schema (`schema.sql`)**
Full `CREATE TABLE` DDL for all tables: `entries`, `entries_fts` (FTS5), `extractions`, `mood_series`, `graph_nodes`, `graph_edges`, `digests`. Every column that the Memory Architecture doc described in prose is now a real column with the correct type and constraint.

Previously missing columns now captured:
`behaviors`, `stated_goals`, `decisions`, `open_loops`, `self_judgments`, `emotions`, `events` — all as JSON columns in `extractions`.

Added: `extraction_status` column (`pending` / `done` / `failed` / `null_stored`) with the full retry contract: retry once at `temperature=0.3`; on second failure write `null_stored` and store NULLs; re-queue `null_stored` rows nightly. Mood chart skips NULL points (shows gap, never zero).

Added: `is_seeded` column on `entries` and `mood_series` — seed data is excluded from recall queries via `is_seeded=0` filter.

Added: migration note — every schema change increments `PRAGMA user_version`; `db.py` applies migration blocks in order.

Added: ChromaDB collection contract — two strictly separate collections: `journals` (entry summaries, filter `is_seeded=False`) and `corpus` (book chunks). Populated simultaneously with extraction, not as a deferred phase.

**7.2 — L3 claim schema (`profile.json`)**
Full JSON example with all top-level keys: `schema_version`, `identity`, `goals`, `patterns`, `relationships`, `emotional_baseline`, `open_loops`, `watch_list`, `anchors`. Every field named with its type.

Added: anchor semantics — `"source": "user"` blocks model `weaken`/`strengthen` ops.

Added: `profile.md` ↔ `profile.json` sync specification — `PUT /profile` re-parses the Markdown diff into `set_anchor` ops; lenient parse; unparseable sections left unchanged with a warning.

**7.3 — L3 operation grammar**
Complete table of 10 operations the model may emit: `add_goal`, `update_goal_status`, `add_pattern`, `strengthen`, `weaken`, `note_contradiction`, `mark_resolved`, `update_loop`, `add_relationship_note`, `set_anchor`. Each with required fields and effect.

Primary anti-hallucination gate: any operation without at least one valid `entry_id` in `evidence[]` is silently rejected.

Decay rule: `confidence -= 0.01 × days_since_last_seen` nightly on non-anchor claims; flag stale at < 0.5 after 60 days.

**7.4 — Knowledge graph API schema (L4)**
JSON shape for `GET /insights/graph`: `nodes[]` with `{id, label, type, entry_count, entries[]}` and `edges[]` with `{id, source, target, type, weight, is_hypothesis, label, entries[]}`. Node type enum: `theme|person|place|goal|problem|emotion`. Edge type enum: `co_occurrence|temporal|similarity|hypothesis`. Hypothesis edges carry `is_hypothesis: true` and render with a confirm/dismiss UI affordance.

**7.5 — Sentence-splitter rules (TTS boundary detection)**
Six ordered rules for `sentence_queue.py`:
1. Do not split after known abbreviations (15 listed).
2. Do not split inside a number (period followed by digit).
3. Do not split inside an open quotation.
4. Split on `. ` `! ` `? ` followed by uppercase, or at `\n`.
5. Minimum chunk: 4 words (buffer into next sentence if shorter).
6. Maximum chunk: 80 words (flush at word boundary even without punctuation).

Note: do not use `nltk.sent_tokenize` — wrong tool for streaming token-by-token context.

Updated the Section 6 closing note to reference Section 7 instead of calling schema definition a "future step."

---

## EVA_SYSTEM_DESIGN.md

### Section 4 — Process & deployment model

Addresses audit findings S6, M5, hardware.

- Added `-ngl 99` to the llama-server command (Metal offload on Apple Silicon). Without this flag the model runs on CPU and misses every latency target on M1 Air.
- Added the complete exact llama-server command as a `sh` block (matches the command the user provided).
- Added context budget note: `--ctx-size 131072` is the server maximum; real-time chat turns must cap at ≤ 8 192 tokens per request via `max_tokens`. Consolidation tasks may use up to 32 768.
- Changed "Voice workers loaded at startup" → "Voice workers **lazy-loaded on first use**" with explicit reasoning: on M1 Air 8 GB, loading faster-whisper + Kokoro at startup alongside the 2.3 GB model exhausts available RAM.
- Added **M1 Air 8 GB memory budget** breakdown: model ~2.3 GB + KV cache ~0.3 GB + FastEmbed ~130 MB + faster-whisper ~150 MB (lazy) + Kokoro ~200 MB (lazy) + Python ~350 MB + macOS ~1.5 GB ≈ 5 GB typical, leaving ~3 GB headroom.
- Added packaging spike note: do it in Phase 0, not Phase 15.

### Section 6 — Data architecture

Addresses audit findings S3, and missing items (ChromaDB versioning, vault portability).

- Added: **ChromaDB collections are strictly separated** — `journals` and `corpus` have separate retrieval paths, same embedding model but different distance thresholds.
- Added: **ChromaDB embedding model versioning** — `vector.py` stores the model name in collection metadata and raises a migration error + `scripts/reindex.py` instructions if the model changes. Never silently mix vectors from different models.
- Added: **Vault portability** — internal file references are relative paths within the vault folder; the absolute vault path lives only in settings. Moving the vault = re-point settings, nothing else breaks.

### Section 8 — Concurrency & scheduling

Addresses audit finding S5.

Replaced the vague "the scheduler defers jobs while a chat turn is active" with a concrete `asyncio.Lock` implementation:
- A single `_model_lock = asyncio.Lock()` in `llm/client.py`.
- `stream_chat(messages, priority=False)` — chat path uses `priority=True`; background jobs yield with `asyncio.sleep(0)` before acquiring the lock.
- APScheduler fires nightly/weekly jobs; if a chat turn starts mid-job, the job's next model call blocks on the lock until the chat turn completes (it does not cancel the job).

### Section 9 — Cross-cutting concerns (Performance)

Addresses hardware specifics.

Updated performance budget from "mid laptop" generic to M1 Air 8 GB primary target with `-ngl 99`:
- STT: < ~1.5 s (was "< ~2 s")
- LLM TTFT: ~0.3–0.8 s (was "~0.3–1 s")
- Per-request context budget explicitly stated: real-time ≤ 8 192 tokens, consolidation ≤ 32 768 tokens.
- Added warning: CPU-only machines (no Metal) roughly double all figures; warn user during setup.

---

## EVA_DEMO_IMPLEMENTATION_PLAN.md

### Global Rules block

Addresses hardware context and CLAUDE.md drift risk.

- Added introductory note: rules are stored as `CLAUDE.md` at repo root; Claude Code reads it automatically — verify presence before each session.
- Added hardware context line: M1 Air 8 GB, Metal via `-ngl 99`, lazy voice loading.
- Added complete llama-server command as a block.
- Added per-request context budget note (≤ 8 192 / ≤ 32 768 tokens).
- Updated stack description to include `-ngl 99`.

### Phase 0 — Scaffold

Addresses audit findings S6, M3.

Added three new build items:
1. **`CLAUDE.md`** — create at repo root with the Global Rules verbatim.
2. **Packaging spike** — bundle a hello-world FastAPI endpoint as a Tauri sidecar via PyInstaller; verify it launches on macOS. Do this in Phase 0, not Phase 15.
3. **Early socket guard** — global outbound block at backend startup from Phase 0; allowlisted host via `EVA_ALLOW_HOST` env var; UI badge wires in Phase 10 but the block is live immediately. (Previously the guard was UI-only and arrived in Phase 10, leaving 10 phases of potential library phone-home.)

Added test: attempt outbound HTTP from Python → confirm it is blocked and logged.

### Phase 1 — Model online

- Updated server.py build instruction to reference the exact command from Global Rules (including `-ngl 99`).
- Updated `stream_chat` signature: `max_tokens=450, priority=True` — context budget is enforced per-request, not by changing `--ctx-size`.
- Added `asyncio.Lock` mention — it lives in `client.py`.
- Removed Windows download script (macOS-only target).
- Added test: confirm `-ngl 99` in server args and Metal offload in logs.

### Phase 2 — Vault & L1 capture (renamed from "basic L1" to "full L1")

Addresses audit findings C2, C3, S1, S3, M1.

Added pre-phase gate: **extraction prompt must be written and tested against the real model before Phase 2 code starts.** Run 5–10 realistic entries, confirm JSON is consistently parseable.

Split Phase 2 into two checkpointed steps:

**Step A — Vault + schema:**
- `vault.py` as before.
- `db.py`: apply `schema.sql` from `EVA_MEMORY_ARCHITECTURE.md §7.1` exactly — no improvising. Commit checkpoint and verify `.schema` matches before Step B.

**Step B — Extraction + ChromaDB + wiring:**
- `extract.py`: uses pre-approved prompt; full L1 JSON schema output; retry once at `temperature=0.3`; `null_stored` on second failure; log all failures.
- `vector.py`: ChromaDB `journals` collection with `bge-small-en-v1.5`; embed summary on every successful extraction — not deferred to Phase 11.
- Wire `/chat` to save + extract + embed in that order.

Updated tests: now require `extraction_status='done'`, all non-null fields, and 3 vectors in ChromaDB. Added mock-LLM bad-output test.

### Phase 4 — Chat surface

Addresses audit findings M4, and interim crisis-care gap between Phase 4 and deferred NeMo.

- Replaced single-file system prompt with **template slots** in `backend/prompts/assembly.py`: `{persona_block}`, `{memory_context}`, `{profile_slices}`, `{corpus_context}`. Each slot is a separate string; adding future context is a one-line change.
- Added **interim crisis-care** in `backend/safety/crisis_check.py`: keyword check before the assembled prompt reaches the model. On signal match, appends a crisis-aware addendum to the persona block. This runs from Phase 4 until NeMo guardrails replace it.
- Added crisis test: send a crisis-signal message → Eva's reply acknowledges warmly and mentions reaching out.

### Phase 7 — Grounded answers with citations (RAG)

Addresses audit finding C4.

Replaced "simple heuristic trigger — full intent gating is a later TODO" with a **minimal 3-class intent classifier** (`backend/intent/classifier.py`): `vent`, `question`, `advice_request`. Rule-based first (question marks, advice keywords); tiny prompt fallback for ambiguous cases. The seam is clearly marked `# INTENT-SEAM: replace with full 5-class classifier`. RAG fires only on `question` and `advice_request`; `vent` bypasses retrieval entirely.

Added test: pure venting message → retrieval does not fire (confirmed via log). Ambiguous message → fallback classifier produces a reasonable label.

### Phase 8 — Voice in (STT)

Addresses audit finding M5.

- Whisper model size is now **configurable via settings** (dropdown: `base.en` / `small.en`). `stt.py` reloads on change.
- Marked as **lazy-loaded** on first STT request (not at backend startup) — M1 Air 8 GB memory constraint.
- Updated latency target: ~1.5 s (was "~2 s") reflecting Metal acceleration.
- Added test: switch to `small.en` in settings → next transcription uses new model (confirmed via log).

### Phase 9 — Voice out (TTS + sentence queue)

Addresses audit finding S2.

- `sentence_queue.py`: now explicitly required to implement the **sentence-splitter rules from `EVA_MEMORY_ARCHITECTURE.md §7.5`** exactly (abbreviation list, number-period rule, open-quote rule, 4-word min, 80-word max). `nltk.sent_tokenize` is prohibited.
- Marked Kokoro as lazy-loaded.
- Added two concrete test cases:
  - `"He saw Dr. Smith, who paid $3.50 for it. Then left."` → must produce exactly two TTS chunks.
  - `"She said 'I'll be there'"` → no split inside the quoted phrase.

### Phase 12 — Mood capture

Addresses audit finding M2.

- Mood endpoint now queries `mood_series` table (not raw `extractions`) with `WHERE is_seeded = 0` for live data; accepts `?include_seeded=true` for demo chart.
- NULL mood days render as gaps in the chart line, never as zero.
- Seed script generates entries with `is_seeded = 1`; safe to run on a vault with real entries. Seed data excluded from recall queries in ChromaDB (`is_seeded=False` filter).
- Added test: confirm seeded entries do NOT appear in recall (Phase 11 memory chip must not surface seed data).

### Phase 13 — Static profile

Addresses audit finding C1.

- `profile.json` stub must now conform exactly to `EVA_MEMORY_ARCHITECTURE.md §7.2` schema — same fields, same types, same structure the real L3 engine will write.
- Profile edits persist to both `profile.md` and `profile.json` via the `PUT /profile` sync described in §7.2.
- Context injection via the `{profile_slices}` slot in `assembly.py` (not ad-hoc string injection).
- Degradation test now targets `profile.json` (the real source of truth), not `profile.md`.

### Phase 14 — Seeded insights

Addresses audit finding S4.

- Both endpoints must return JSON conforming exactly to `EVA_MEMORY_ARCHITECTURE.md §7.4` graph schema.
- Hypothesis edges render dashed with confirm/dismiss affordance (not just as regular edges).
- Seeded graph data carries `is_seeded=1` in `graph_nodes`/`graph_edges` for future pruning.
- Added test: `scripts/validate_graph.py` validates the endpoint JSON against the §7.4 schema.

### Section 3 — Deferred table

Updated and expanded:

| Added | Reason |
|---|---|
| "Full 5-class intent classifier" | Replaces "Intent classifier" — Phase 7 now has a 3-class stub, full 5-class is deferred |
| "NeMo guardrails + full crisis-care path replaces `crisis_check.py`" | Clarifies that Phase 4's keyword check is the interim |
| "`scripts/reindex.py` (ChromaDB re-embed on model change)" | New deferred item from ChromaDB versioning requirement |
| "`profile.md` ↔ `profile.json` bidirectional parser" | New deferred item — `PUT /profile` stub exists in Phase 13 |
| Removed "Windows packaging" | macOS-only target for demo |

---

## eva_system.md

### Proportional-length rule

Added hard rule to the "How you speak" section:

> As a hard rule: if their message is under 20 words, keep your reply under 3 sentences.

Previously "match their energy" was a soft guideline. On a 450-token cap, a 3-word input could still get a 6-sentence reply. The new rule makes the constraint explicit and enforceable.

### Crisis-care — responsibility clause

Added one sentence to the Care section:

> This responsibility is yours in every conversation — do not wait for a system to catch it.

Reason: NeMo guardrails are deferred. Between Phase 4 (Eva goes live) and whenever NeMo is integrated, the model's persona is the only safety net for crisis signals. The addition makes clear the model must not treat crisis handling as someone else's job.

---

*All changes are derived directly from `AUDIT.md` findings. No design decisions were changed; only gaps, missing specifications, and ambiguities were resolved.*
