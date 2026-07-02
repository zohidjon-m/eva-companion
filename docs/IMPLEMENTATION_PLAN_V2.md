# Eva — Build Plan

**Small phases, strict priority order, every phase readable and testable by a human — and nothing faked.**
*Plan v2 · 2026-06-24 · executed one phase at a time via Claude Code / Codex*

> **What changed from v1.** v1 was a *demo* plan: it shipped the top memory layers (L3 user model, L4 insights) as hand-written stubs and seeded data, behind real interfaces, to be swapped out later. v2 builds those engines for real. The five-layer architecture in [`EVA_MEMORY_ARCHITECTURE.md`](../system/EVA_MEMORY_ARCHITECTURE.md) and [`EVA_SYSTEM_DESIGN.md`](../system/EVA_SYSTEM_DESIGN.md) is already fully specified — no design is missing — so this plan simply stops deferring it. **Nothing in the product is hardcoded or seeded.** Every layer is computed from real data and rebuildable from L0.

> **Provider direction.** Eva V2 is hybrid-provider: local AI remains the privacy-first default, and online API mode is explicitly opt-in. The accepted provider policy lives in [`decisions/2026-07-01-hybrid-llm-provider.md`](decisions/2026-07-01-hybrid-llm-provider.md).

---

## 0. How to work this plan

**The rules that keep it from becoming a mess:**

1. **One phase per AI session.** Paste the Global Rules (below) once, then exactly one phase. Never two. A fresh session per phase keeps the AI focused and the diff small.
2. **Every phase ends in a commit** named `phase-XX: <title>`. If a phase doesn't compile, run, and pass its checks, it doesn't get committed and the next phase doesn't start.
3. **You review before you commit.** Each phase lists the 2–4 files that matter ("Read these"). If you can't explain what they do after reading, make the AI add comments or simplify — that's a legitimate phase task.
4. **Priority is the order.** Phases are sorted so the app is coherent at every stop-point: after Phase 12 you have a polished companion that talks, journals, and grounds answers in your books; after Phase 19 it genuinely models you and reflects real analytics back. Later phases deepen, they don't paper over.
5. **Real behind clean interfaces — never a fake behind one.** Each component is built against the interface the rest of the system depends on (the `Interface:` lines in System Design §5). A component may be *built in stages* (basic L1 before full L1), but every stage is real: it computes its output from actual data, it is rebuildable from L0, and no screen ever shows a value that wasn't derived from the user's own entries.
6. **Capture is complete from day one.** The intelligence layers (L3, L4, pattern mining) can only ever reflect structure that was captured at write time — *you cannot retroactively extract what you never stored* ([Memory Architecture §5.6](../system/EVA_MEMORY_ARCHITECTURE.md)). So the full L1 episode schema lands early (Phase 3), before the surfaces that generate the bulk of the entries.
7. **The model never remembers, counts, or connects across history — code does.** Every heavy job (pattern mining, deltas, graph edges, profile updates) is deterministic Python/SQL that assembles evidence; the model only (a) extracts one entry at a time and (b) narrates over what code already counted. This is the line that makes the ambitious features honest on a 2B model. If a phase ever asks the model to "notice a pattern" over many entries, the phase is designed wrong — split the counting out.

**Using two AI tools:** the simplest split that works — use **Claude Code as the implementer** (it executes the phase), and use **Codex as the reviewer** (paste the diff, ask "find bugs, dead code, and anything that doesn't match the phase spec"). Or alternate implementers per phase if you prefer; just never have both editing the repo at once.

### Global Rules (paste once at the start of every AI session)

```
You are building "Eva", a privacy-first hybrid-provider desktop AI journaling companion.
Stack: Tauri (Rust shell) + React/Vite frontend + Python FastAPI backend +
llama.cpp llama-server running gemma-4-E2B-it-qat-GGUF for the default local
provider (port 11500, thinking mode OFF), opt-in online API providers behind
the same provider interface, ChromaDB + SQLite + faster-whisper + Kokoro TTS +
APScheduler + FastEmbed. English only.

Rules:
1. Implement ONLY the phase given. Do not touch later phases. Do not refactor
   unrelated code.
2. Small, readable modules. Every public function gets a docstring saying what
   it does and why it exists. A human will read all of this code.
3. After implementing, run the phase's checks. Fix failures BEFORE reporting
   done. Then list: files changed, how to test manually, anything left TODO.
4. Privacy is hard law: no telemetry and no analytics. Local mode blocks
   outbound runtime calls except explicit first-run model/voice downloads.
   Online API mode is opt-in and may call only the configured provider host.
5. Full journal entries are plain Markdown on disk — the source of truth.
   Databases are derived and rebuildable; Markdown never depends on them.
6. NOTHING IN THE PRODUCT IS HARDCODED OR SEEDED. Every value a user sees is
   computed from their real entries. Every layer (L0-L4) is rebuildable from L0.
   The only synthetic data permitted is a clearly-marked dev test fixture that
   flows through the REAL pipeline (real extraction, real L3 ops, real SQL) and
   is never shipped — mark such code  # DEV-FIXTURE  and gate it behind a flag.
7. Code counts; the model narrates. Never ask the model to count, rank, or
   connect across multiple entries. Heavy analysis is deterministic Python/SQL
   that hands the model a small, pre-counted, evidence-backed job.
8. Every L3 claim carries evidence pointers to the L1 entries that justify it.
   No pointer, no claim — code rejects an unsupported assertion.
9. If anything is ambiguous about data storage, privacy, or Eva's behavior:
   STOP and ask. Do not guess.
10. End every phase with a git commit: "phase-XX: <title>".
```

---

## 1. Phase map (priority order)

| # | Phase | Milestone | Why this position |
|---|---|---|---|
| 0 | Scaffold: shell + backend + health | A — Spine | Nothing runs without it |
| 1 | Model online: streaming chat (backend only) | A — Spine | Proves gemma4 works before any UI |
| 2 | Vault: real entry capture (L0 + basic L1) | A — Spine | Capture must be real from day one |
| 3 | L1: full episode schema (+ re-extraction backfill) | B — Capture substrate | The moat can only reflect what was captured at write time |
| 3.5 | Stable entry identity (`uid`) + content-hash dirty-tracking | B — Capture substrate | Editing *and* durable evidence pointers both require it; cheapest before entries pile up ([ADR-001](../decisions/ADR-001-editable-entries.md)) |
| 4 | Semantic index (L2): embeddings + ChromaDB | B — Capture substrate | Recall, clustering, edge candidates all sit on this |
| 5 | App shell UI + design system | C — Surfaces | "Really good UI" starts here, not as an afterthought |
| 6 | Chat surface (streaming, polished) | C — Surfaces | The core demo interaction |
| 7 | Journaling surface (separate from chat) | C — Surfaces | Second pillar of the experience |
| 7.5 | Editable entries (L0 revisions + single-entry recompute) | C — Surfaces | Edits must propagate L0→L4 without a full rebuild ([ADR-001](../decisions/ADR-001-editable-entries.md)) |
| 8 | Library: upload books / PDFs / text | C — Surfaces | First half of the RAG story |
| 9 | Conversation engine: intent classifier + listen-first gating + personas | D — Talking, for real | Listen-first is structural, not a prompt plea |
| 10 | Grounded answers with citations (advice-mode RAG) | D — Talking, for real | The strongest real proof of the thesis |
| 11 | Voice in (push-to-talk STT) | D — Talking, for real | Wow factor, riskier — after the safe wins |
| 12 | Voice out (Kokoro + sentence queue) | D — Talking, for real | Completes "talk with Eva" |
| 13 | L3 user model engine: claims, operation grammar, apply/decay/contradiction | E — The moat (real) | The evolving self — the heart of the product |
| 14 | Consolidation pipeline: on-save / nightly / weekly + scheduler + rollups | E — The moat (real) | Where L3/L4 are actually built; code counts, model narrates |
| 15 | Read loop uses L3 + Profile screen (read/edit, edits are anchors) | E — The moat (real) | Eva genuinely knows you, via the real interface |
| 16 | Memory recall surfaced ("Eva remembers", with chips) | E — The moat (real) | The recall the user can *see* happen |
| 17 | Mood / emotion analytics (L4) + charts | F — Analytics (real) | Real SQL over real extractions |
| 18 | Knowledge graph (L4): real edges + evidence-gated hypotheses | F — Analytics (real) | Association from co-occurrence/precedence/similarity |
| 19 | Growth analytics (L4) + verification pass | F — Analytics (real) | Real period deltas; model narrates descriptively |
| 20 | Guardrails & wellbeing (light rails + crisis-care path) | G — Safety & ship | Wraps the chat pipeline before strangers touch it |
| 21 | Polish pass: states, settings, provider/network status, first-run | G — Safety & ship | Turns working into impressive |
| 22 | Hardening + packaging + demo script | G — Safety & ship | Protects the live demo |

Milestone A = it runs. ▸ B = capture is complete. ▸ C = the product surfaces exist. ▸ D = it talks (and listens) for real. ▸ E = it models you for real. ▸ F = it reflects real analytics. ▸ G = it ships.

**Stop-points that still make sense:** after **12** you have a finished companion (chat + journal + grounded answers + voice) with complete capture underneath but no long-horizon intelligence surfaced yet. After **16** the evolving-memory moat is live. After **19** every promised screen shows real, computed data. 20–22 make it safe and shippable.

---

## 2. The phases

### Phase 0 — Scaffold: shell + backend + health ✅ *(complete)*
**Goal:** an empty but running app: Tauri window ↔ FastAPI backend over localhost.
**Build:**
- Tauri app with React + Vite frontend (`ui/`), backend in `backend/` (Python 3.12, FastAPI, venv).
- Backend launched as a sidecar by Tauri in dev mode (`dev.ps1` starts both).
- `GET /health` returns `{status, model_present}`; frontend shows a status dot that reads it.
- Repo hygiene: `.gitignore` (venv, node_modules, `local_vault/`), `README.md` with run instructions.
**Read these:** `backend/app.py`, `ui/src/App.tsx`, `dev.ps1`.
**Done when:** app opens, backend answers on `:8420`, repo is clean. Commit. *(Done 2026-06-14.)*

### Phase 1 — Model online: streaming chat (backend only) ✅ *(complete)*
**Goal:** gemma4 streams tokens through the backend. No UI yet — proven with curl.
**Build:**
- `backend/llm/server.py`: start/supervise `llama-server` (`unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL`, port 11500, `--jinja`, thinking OFF, temp 1.0 / top-p 0.95 / top-k 64). Missing GGUF → a clear "model not found" state with the per-OS download command — never crash.
- `backend/llm/client.py`: async `stream_chat(messages)` + `complete_chat(messages)` against the OpenAI-compatible endpoint.
- `WS /chat`: accepts a message, streams tokens back. Download scripts for win/mac, `scripts/ws_test.py`.
**Read these:** `llm/server.py`, `llm/client.py`.
**Done when:** streaming works and the missing-model path is graceful. Commit. *(Done 2026-06-14.)*

### Phase 2 — Vault: real entry capture (L0 + basic L1) ✅ *(complete)*
**Goal:** everything the user writes is genuinely persisted from day one.
**Build:**
- `backend/memory/vault.py`: append-only `local_vault/journal/YYYY-MM-DD.md` with YAML frontmatter; lock-serialized, verbatim, never rewritten.
- `backend/memory/db.py`: SQLite `entries` + `extractions` (basic: summary, mood, entities, themes) + FTS5.
- `backend/memory/extract.py`: one bounded gemma4 call per saved entry → strict JSON; retry once, then store nulls. Never blocks saving.
- `/chat` saves every user turn (vault + db) and runs extraction in the background; L0 write failure is a hard error.
**Read these:** `vault.py`, `extract.py`.
**Done when:** capture is real, durable, and survives DB loss. Commit. *(Done 2026-06-15.)*

---

### Phase 3 — L1: full episode schema (+ re-extraction backfill)
**Goal:** capture *all* the structure the moat needs, from day one — not just summary/mood/entities/themes.
**Why now:** L3 (the user model), feature 6 (behavior-vs-goal mistakes), and the open-loop timeline can only ever reflect fields captured at write time. Phase 2 shipped a deliberately tight schema; expand it *before* the surfaces (Phases 5–12) generate the bulk of entries. Re-extraction is safe because L0 is the source of truth.
**Build:**
- Extend `extractions` (and `extract.py`'s few-shot prompt + parser) to the full [Memory Architecture §1 L1](../system/EVA_MEMORY_ARCHITECTURE.md) record, keeping each field tight and well-exampled:
  - **mood** (−5..+5) **+ discrete emotions** (controlled set: anger, shame, joy, anxiety, calm, …) with intensity
  - **entities** (people/places/projects, normalized for cross-entry linking) · **themes/topics** · **events** (short, what happened)
  - **stated goals/values** (with provenance: the entry that asserted it) · **behaviors** (what the user actually did — *kept distinct from goals*; this distinction is the engine of feature 6)
  - **decisions/intentions** · **open loops** (first-class, with `status: open|updated|resolved` and a timeline) · **self-judgments/regrets**
- New normalized tables (e.g. `emotions`, `entities`, `goals`, `behaviors`, `open_loops`, `self_judgments`) keyed to `entry_id`, with an `entity` canonicalization helper so "my brother" and "Tom" can later link. Keep extraction one bounded call; parse defensively; partial/failed fields store null, never block the save.
- `scripts/reextract.py`: re-run extraction over all of L0 to backfill the new fields into a fresh DB (idempotent; the DB is rebuildable, L0 is not touched).
- Schema-degradation guard: extraction quality on a small model falls as the schema grows — keep the prompt few-shot, and if a run shows degradation, the fix is fewer/better-exampled fields, not more retries.
**Read these:** `extract.py` (the expanded prompt + parser), `db.py` (the new schema), `scripts/reextract.py`.
**Test:**
- Write 5 varied entries (a vent, a goal statement, an unresolved argument, a decision, a regret) → each gets a row in the right sub-table with plausible values; an open loop is marked `open`; a stated goal and a contradicting behavior are stored *separately*.
- Delete `eva.db`, run `reextract.py` → every field rebuilds from the Markdown; counts match. Unit tests cover each new field's parser path (missing/malformed → null, never crash).
**Done when:** every entry yields the full episode record, and the whole DB rebuilds from L0. Commit.

### Phase 3.5 — Stable entry identity (`uid`) + content-hash dirty-tracking ✅ *(complete)*
**Goal:** give every entry a stable, content-independent identity and track what each derived row was computed from — the foundation that makes editing safe *and* fixes a latent bug today.
**Why now:** two things break the moment entries can change or the DB rebuilds. (1) Today entries are keyed by autoincrement `entries.id`, which `reextract.py` **reassigns on every rebuild** — so any L3 evidence pointer (Phase 13, *no pointer no claim*) would silently point at the wrong entry after a rebuild. (2) Editing (Phase 7.5) needs to address one entry and recompute only it. A stable `uid` solves both, and it must land **before L2 (Phase 4) keys off entry identity and before the surfaces generate the bulk of entries** — retrofitting ids onto a full vault is far worse than doing it now. This is Action Item 1 of [ADR-001](../decisions/ADR-001-editable-entries.md) and is worth landing *even if editing were never built*.
**Build:**
- **Stable `uid` in L0.** Assign each entry a content-independent id at append time (ULID or 8-char random hex) and write it into the block header — e.g. `## 14:03:27 · journal · a1b2c3` (extend `_BLOCK_HEADER` + `parse_day_file` in `vault.py`). The `uid` never derives from the text, so editing the text never changes it.
- **One-time backfill migration** stamps `uid`s into existing day-files (a deliberate, logged, one-time rewrite — append-only purity holds *going forward*). Few entries exist now; this is the cheap moment.
- **Derived layers key off `uid`, not rowid.** Add `uid` to `entries` (unique) and make L1 sub-tables / future L2 / L3 evidence pointers reference `uid`. Re-extraction and rebuild must **preserve `uid`** so pointers survive (`reextract.py` reads the `uid` from L0 rather than minting a new id).
- **Content hash per entry.** Store `source_hash` (hash of the L0 block text) on the `extractions` row. `reextract.py` becomes **hash-gated/incremental**: only re-extract entries whose `source_hash` changed — the same mechanism Phase 7.5 uses to recompute a single edited entry.
**Read these:** `vault.py` (the `uid` in the header + parser), `db.py` (the `uid`/`source_hash` columns), `scripts/reextract.py` (uid-preserving, hash-gated rebuild).
**Test:**
- Append entries → each gets a unique `uid` in the Markdown and the DB. Delete `eva.db`, run `reextract.py` → **every `uid` is identical to before** (pointer stability proven); unchanged entries are skipped by the hash gate, counts still match.
- The backfill migration run twice is idempotent (no duplicate ids, no double-rewrite).
**Done when:** every entry has a durable `uid` that survives a full rebuild, and rebuild only touches changed entries. Commit.

### Phase 4 — Semantic index (L2): embeddings + ChromaDB
**Goal:** real associative recall and the candidate-generation substrate for clustering and graph edges.
**Build:**
- `backend/memory/vector.py`: ChromaDB persistent client in the vault; FastEmbed `bge-small-en-v1.5`. A `journals` collection embedding each entry **summary**, and an `episodes` sub-index embedding individual episodic units (open loops, notable moments) — both with metadata (date, mood, themes, entry_id).
- Wire on-save: after extraction, embed the summary + episodic units into L2 (extends the Phase 2 background task). Idempotent upsert keyed by entry_id/unit_id so re-extraction rebuilds the index cleanly.
- `scripts/reindex.py`: rebuild L2 from L1 (which rebuilds from L0) — proves the layer is derived.
- `recall(query, k, filters) -> hits` with a relevance threshold (the contract Phases 13/16 depend on).
**Read these:** `vector.py` (the collections + `recall`), the on-save wiring in `app.py`.
**Test:**
- Journal three distinct days; `recall("the argument with my brother")` returns the right day above threshold and an unrelated day below it.
- Delete the Chroma dir, run `reindex.py` → recall results are identical. Querying with no relevant match returns nothing above threshold (no false recall).
**Done when:** summaries + episodic units are embedded, queryable, and fully rebuildable. Commit.

### Phase 5 — App shell UI + design system
**Goal:** the frame of a genuinely good-looking app, before any feature screens.
**Build:**
- Layout: left sidebar (Chat · Journal · Library · Insights · Profile · Settings), main content area, top bar with a **persona selector** (wired for real in Phase 9 — render it now, no fake state) and a provider/network status badge.
- Design tokens: one font pairing, a calm palette (light + dark), spacing scale, radii — CSS variables in one file. Consistent button/input/card components (`ui/src/components/`).
- Intentional empty states for all six sections (real screens come in later phases — empty, not faked).
- Follow the repo's frontend-design guidance for a distinctive, non-generic look.
**Read these:** the tokens file, the layout component.
**Test:** click through all six sections (intentional empty states, no dead links, no lorem ipsum); toggle dark/light; resize small — layout holds.
**Done when:** the app already *looks* like a product with no features in it. Commit.

### Phase 6 — Chat surface (streaming, polished)
**Goal:** talking to Eva in text feels alive and finished.
**Build:**
- Chat screen wired to `WS /chat`: user bubbles, Eva bubbles streaming token-by-token with a typing indicator, auto-scroll, error toast + retry if the socket drops.
- Conversation history for the current session (in memory + persisted via capture).
- Eva's first real system prompt: `backend/prompts/eva_system.md` — companion identity, warm, listens first, concise (~450 token cap). Personas come in Phase 9.
- Enter to send, Shift+Enter newline; input disabled while Eva replies.
**Read these:** the chat component, `eva_system.md`.
**Test:** hold a 6+ turn conversation (smooth streaming, correct history, today's `.md` captured everything); kill the backend mid-reply → graceful error, recovers on restart.
**Done when:** a stranger could chat with Eva and nothing would feel broken. Commit.

### Phase 7 — Journaling surface (separate from chat)
**Goal:** journaling is its own ritual, not a chat thread.
**Build:**
- Journal screen: a calm full-width editor for today's entry ("How was your day?"), explicit Save, autosave draft every ~10 s.
- Saving writes to the same vault (entry type `journal`) and triggers the same full extraction (Phase 3) + indexing (Phase 4).
- A browseable past-entries list (by date, from SQLite) with a read-only day view — the seed of time-travel.
- After save, one gentle Eva acknowledgment (single bounded LLM call): a reflection or soft question, never advice.
**Read these:** the journal screen, the save path in `vault.py` (chat vs journal types).
**Test:** write & save → appears in the day file, the list, gets a full extraction; acknowledgment is appropriate in tone. Reopen the app → draft/entry persists; an older hand-placed file renders correctly.
**Done when:** journaling works end-to-end and feels distinct from chat. Commit.

### Phase 7.5 — Editable entries (L0 revisions + single-entry recompute)
**Goal:** let the user fix a typo or reword *any* past entry, and have the whole stack stay correct — without a full rebuild and without losing the original.
**Why now:** the journal browse list (Phase 7) is where an edit affordance belongs, and by here L1 (Phase 3), `uid`/hash (Phase 3.5), and L2 (Phase 4) all exist, so a single-entry recompute is cheap and real. This implements Action Items 2–3 and 5 of [ADR-001](../decisions/ADR-001-editable-entries.md). (L3 doesn't exist until Phase 13, so the L3 self-heal hook is added there — see below; an edit before then correctly recomputes L1/L2/L4.)
**Build:**
- **L0 stays append-only — edits are revisions, not rewrites.** `vault.py` gains `update_entry(uid, new_text)`: the prior version is preserved (in `journal/.history/` or as a superseded block) and the current text is the latest revision. *"Never rewritten" stays literally true*, and the original survives for time-travel (feature 8).
- **Atomic write.** The day-file rewrite goes through a temp file + atomic rename under the existing `_WRITE_LOCK`, so a failed edit can never corrupt the one irreplaceable store (`append_entry` couldn't corrupt prior data; a block rewrite must be just as safe).
- **Single-entry recompute (the fast, synchronous path).** `recompute_entry(uid)`: `source_hash` changes → re-extract just that entry (one model call, reuses the idempotent `save_extraction`) → re-embed L2 (idempotent upsert) → L4 needs nothing (computed on demand). What the user sees is immediately correct; cost is one extraction + one embedding.
- **Endpoint + UI.** `PUT /entries/{uid}` → vault revision + `recompute_entry`. In the Phase 7 browse view: an edit affordance on an entry, and a "show original" toggle when an entry has prior revisions.
- **Deletion is the same machinery** (edit-to-tombstone → dirty → cascade-drop L1 rows → L2 removed).
- **Doc amendment (Action Item 6):** update `EVA_MEMORY_ARCHITECTURE.md` §L0 and `EVA_SYSTEM_DESIGN.md` recovery section — "append-only" → "append-only *storage* with editable *presentation* via revisions."
**Read these:** `vault.py` (`update_entry` + atomic rewrite + history), `recompute_entry` (the per-entry re-extract/re-embed), the `PUT /entries/{uid}` handler.
**Test:**
- Edit a past entry's text → its L1 record, FTS, and L2 embedding all reflect the new text; **its `uid` and evidence-pointer identity are unchanged**; the original is still retrievable.
- Fix a one-character typo → exactly one entry is re-extracted (the hash gate skips all others); an interrupted/failed rewrite leaves the day-file intact (atomicity proven).
- Edit-to-empty (delete) → L1 sub-rows cascade-drop and the L2 vectors are gone; no orphan rows.
**Done when:** any entry is editable, edits propagate L0→L2/L4 by recomputing only that entry, and the original is never lost. Commit.

### Phase 8 — Library: upload books / PDFs / text
**Goal:** the user can hand Eva their books; the corpus pipeline is real.
**Build:**
- `backend/ingest/loaders.py` (pdf via pypdf/pdfplumber, md, txt) + `chunker.py` (~500-token chunks, overlap, source + page/section metadata).
- `backend/memory/vector.py`: add a `corpus` collection (same FastEmbed model).
- `POST /corpus/upload` (file → load → chunk → embed → indexed) and `GET /corpus` (list with chunk counts / status); remove.
- Library screen: drag-and-drop upload, progress state, list of ingested documents, remove.
**Read these:** `chunker.py` (chunk + metadata shape), `vector.py`.
**Test:** upload a real PDF (50+ pages), a .md, a .txt → all index with sensible chunk counts; progress + failure states render (try a corrupt file); a script queries the `corpus` collection for a book phrase and gets the right chunk.
**Done when:** documents go in reliably and are queryable. Commit.

### Phase 9 — Conversation engine: intent classifier + listen-first gating + personas
**Goal:** listen-first and opt-in advice become **structural**, enforced by what's in the context window — not by hoping the prompt holds.
**Build:**
- `backend/engine/intent.py`: classify each turn into `vent / process / ask_info / ask_advice / ambient` (a tiny bounded model call or a small classifier — single, cheap, per turn). Expose `classify(text, recent_context) -> intent`.
- Restructure the read loop (toward the LangGraph state machine in System Design §5.11) as: `classify → assemble_context → check_in → reason(stream) → check_out → persist`. For now `assemble_context` pulls recent L1 episodes; L3 slices and corpus passages plug in at Phases 15 and 10.
- **Retrieval gate:** in `vent`/`process` the corpus and advice slices are *never* fetched — the model literally cannot reach for advice. Only `ask_advice` opens those retrievals (used by Phase 10).
- `backend/engine/personas.py` + `POST /persona`: real persona system (close friend / coach / mentor) — each a system-prompt + token-budget profile (≈450 default, ≈800 coach/mentor, hard cap 1000). Wire the Phase 5 selector to it for real.
- Stream the chosen `intent` and active persona to the UI (also feeds the debug panel in Phase 21).
**Read these:** `intent.py`, the read-loop assembler, the gate that withholds retrieval in vent/process.
**Test:**
- A venting message → classified `vent`, no corpus/advice retrieval fires (assert it in a test), reply is a reflection.
- "What does my book say about patience?" → `ask_advice`/`ask_info`, retrieval is permitted.
- Switch persona to coach → reply length/tone budget changes measurably; switch back → reverts.
**Done when:** intent is correct, the gate provably withholds advice in vent/process, and personas are real. Commit.

### Phase 10 — Grounded answers with citations (advice-mode RAG)
**Goal:** Eva answers from the user's documents — the strongest real proof of the thesis — and never invents a citation.
**Build:**
- `backend/memory/retrieval.py`: embed query → top-k corpus chunks above a relevance threshold; **only reachable through the `ask_advice` gate** from Phase 9.
- Prompt integration: retrieved passages injected with a hard rule — *answer from the passages; if they don't contain it, say so; never invent a quote or citation* (Memory Architecture §5.10).
- Citations rendered in chat as small source chips (file + page/section); clicking shows the passage text.
**Read these:** `retrieval.py`, the grounded prompt template — verify the no-invented-citations rule yourself.
**Test:** ask something answered in the book → correct answer + correct chip pointing at the right page; ask something in no document → Eva says she doesn't find it (no fabricated citation); pure venting → no retrieval, no citations.
**Done when:** grounded answers are accurate, cited, and honest about gaps. Commit.

### Phase 11 — Voice in (push-to-talk STT)
**Goal:** speak to Eva instead of typing.
**Build:**
- `backend/voice/stt.py`: faster-whisper `base.en` int8, loaded once; `POST /stt` (audio → text).
- Mic button in chat + journal: hold-to-record (or click-toggle), 120 s cap, level indicator.
- Flow: release → transcribe → text appears in the input for confirmation → sends through the normal pipeline (capture/intent/RAG identical for voice and text).
**Read these:** `stt.py`, the recorder hook in the UI.
**Test:** speak two sentences → accurate transcription within ~2 s of release; 120 s cap enforced; mic-permission-denied shows a helpful message, not a crash.
**Done when:** voice input is accurate and stable on your demo machine. Commit.

### Phase 12 — Voice out (Kokoro + sentence queue)
**Goal:** Eva speaks her replies naturally, starting almost immediately.
**Build:**
- `backend/voice/tts.py`: Kokoro, voice `af_heart`, text → 24 kHz wav chunk.
- `backend/voice/sentence_queue.py`: consume the token stream, buffer to sentence boundaries (handle abbreviations/numbers), synth each sentence, emit ordered audio chunks over the chat WebSocket alongside text.
- UI: sequential audio playback queue; voice on/off toggle; stop-speaking button.
**Read these:** `sentence_queue.py` — the trickiest concurrency in the app; comment the ordering logic until you can explain it.
**Test:** voice on, multi-sentence question → speech ≤ ~2.5 s after generation starts, sentences in order, no overlap; toggle off mid-reply → audio stops, text continues; "Dr. Smith paid $3.50." doesn't split mid-abbreviation.
**Done when:** talking with Eva feels like a conversation, not a buffer. Commit.

---

### Phase 13 — L3 user model engine: claims, operation grammar, apply/decay/contradiction
**Goal:** the evolving model of *who the user is* — the moat — built for real. This is the hardest single component (Memory Architecture §6); design it explicitly before building on it.
**Build:**
- `backend/memory/profile.py` with the real interface from System Design §5.8: `retrieve_slices(topic) -> fragments`, `apply_ops(ops)`, `get_profile()`.
- **Claim data structure** (SQLite/JSON): `{id, statement, type (identity|goal|pattern|relationship|baseline|open_loop|watch_item), evidence_pointers:[uid…], confidence, first_seen, last_seen, status, source (model|user), needs_revalidation}`. Evidence pointers are entry **`uid`s** (Phase 3.5) so they survive rebuilds and entry edits. **No evidence pointer → claim rejected by code** (Memory Architecture §5.1). The `watch_item` type holds candidate recurring mistakes tied to goals (the feature-6 precursor). `needs_revalidation` is the edit self-heal flag — see below.
- **Edit self-heal hook** ([ADR-001](../decisions/ADR-001-editable-entries.md) Action Item 4): when an entry is edited (Phase 7.5), code flags every claim whose `evidence_pointers` include that `uid` as `needs_revalidation = true`. The claim is *not* rebuilt synchronously; it is revalidated on the next consolidation (Phase 14). This keeps an edit cheap while the moat self-heals on its normal cadence.
- **Operation grammar** the model is allowed to emit — a small fixed set: `add` / `strengthen` / `weaken` / `note-contradiction` / `mark-resolved` / `link-evidence`. Each operation carries its evidence pointer. The model emits operations over a *bounded* slice of L3 (only the sections today's facts touch); it never regenerates the profile.
- **Deterministic apply + decay + contradiction-resolution** (pure Python, fully audited): apply validates evidence and updates confidence; **confidence rises with corroboration and fades without it** (Memory Architecture §5.4) so one-off remarks never harden into "facts"; contradiction resolution reconciles conflicting claims by recency/evidence/source.
- **Two stores:** structured `profile.json` (the machine's), and a human-readable `profile.md` narrative regenerated from the structured claims. **User edits to `profile.md` become `source: user` anchors the model may not overwrite** (Memory Architecture §5.8).
- Rebuildable: `scripts/rebuild_profile.py` re-derives `profile.json` from L1 by replaying operations, then re-applies the user's `profile.md` anchors.
**Read these:** `profile.py` (the claim schema + the apply/decay/contradiction logic — understand it fully), the operation grammar definition, `rebuild_profile.py`.
**Test:**
- Feed three entries asserting "I want to be disciplined" → an `identity`/`goal` claim with three evidence pointers and rising confidence; a single off-hand remark stays low-confidence and decays over simulated time.
- Emit a `note-contradiction` for a behavior that conflicts a goal → a `watch_item` appears with both pointers; resolving it updates status, not by rewriting the profile.
- Hand-edit `profile.md` (correct a goal) → that claim is locked `source: user`; a later model `weaken` op against it is rejected. Delete `profile.json`, run rebuild → structured profile reconstructs from L1 with anchors re-applied.
**Done when:** L3 updates only via bounded, evidence-backed operations, decays correctly, honors user anchors, and rebuilds from L0. Commit.

### Phase 14 — Consolidation pipeline: on-save / nightly / weekly + scheduler + rollups
**Goal:** the write loop that actually *builds* the upper layers — code generates candidates and does all counting; the model only narrates (Memory Architecture §3).
**Build:**
- `backend/memory/consolidate.py` with `on_save(entry_id)`, `run_nightly()`, `run_weekly()`:
  - **On save:** L0 append → L1 extract → L2 embed (already wired Phases 2–4) → queue downstream. One small model call total.
  - **Nightly (today only):** (1) append today's mood/emotion/metrics to SQLite — *no model*. (2) Reconcile open loops: embedding-match today's content vs currently-open loops, then a tiny yes/no model check ("same unresolved thing?") to mark resolved/updated/new. (3) Update L3 via bounded **operations** with evidence pointers (Phase 13 `apply_ops`) — never a rewrite. (4) **Revalidate edited claims** ([ADR-001](../decisions/ADR-001-editable-entries.md)): for every claim flagged `needs_revalidation` (an entry it cites was edited via Phase 7.5), re-pull its now-updated cited entries and run the cheap verification pass (Phase 19 / Memory Architecture §5.7, "supported by its evidence? yes/no"); claims that lost support decay or drop (§5.4), claims still supported clear the flag. No replay, no full rebuild.
  - **Weekly (the reduce step — features 6 & 9):** (1) **Deterministic mining first** — code counts theme frequencies, emotion co-occurrence, open-loop recurrence, and **behavior-vs-goal contradictions** (a recurring behavior running against a stated goal) → ranked candidates *with evidence counts*. (2) Cluster similar episodes (L2 embeddings). (3) **Narrate** — only now the model describes the top-few candidates strictly from the provided evidence. (4) Reconcile L3 (merge, decay, resolve). (5) Rebuild the graph + roll up digests.
  - **Rollup hierarchy:** entries → week digests → month digests → "era" digests, each a map-reduce of the level below, so no model call ever sees more than one bounded window (Memory Architecture §3, §5.5).
- `backend/scheduler.py` (APScheduler): runs nightly/weekly when idle; **defers jobs while a chat turn is active** and **serializes model access** so background work never contends with the real-time path (System Design §8). Plus a manual `POST /consolidate?scope=nightly|weekly` trigger for testing only.
**Read these:** `consolidate.py` (the three cadences; confirm code does the counting), the weekly behavior-vs-goal miner, `scheduler.py` (the defer-while-chatting rule).
**Test:**
- Seed a week of varied entries via the **`# DEV-FIXTURE`** path (real entries through the real pipeline, backdated) → run `run_weekly()` → the miner surfaces a real behavior-vs-goal contradiction *with evidence counts*, the model's narration cites only those entries, and L3 gains the corresponding `watch_item`.
- Start a chat turn, fire the scheduler → the background job defers until the turn finishes (assert serialization). Open loops opened earlier are correctly marked resolved when a later entry resolves them.
**Done when:** the write loop builds L3/L4 from real data, code does all counting, and background never blocks chat. Commit.

### Phase 15 — Read loop uses L3 + Profile screen
**Goal:** Eva genuinely knows who you are — the read loop hands her the consolidated understanding, and the user can see and correct it.
**Build:**
- Extend the Phase 9 `assemble_context`: every turn, pull `retrieve_slices(topic)` — the active goals, relevant patterns, live open loops, and the people involved in *this* topic (retrieved, **not** the whole profile) — and inject them. In `ask_advice`, also pull goal/value slices alongside corpus passages (Memory Architecture §4, feature 2). Eva sounds like she understands because she's been *handed* the understanding, not because the model reasoned over history.
- Profile screen: render `profile.md` nicely; an edit button (`GET /profile` / `PUT /profile`) whose saves persist as `source: user` anchors (the human-in-the-loop correction path from Phase 13). Show each claim's evidence (the entries that justify it) — this is the auditability/anti-hallucination story made visible.
- Graceful degradation: empty/young profile → no slices injected, no crash; deleted `profile.md` → app still runs.
**Read these:** the context-assembly code that injects slices, the profile screen + `PUT /profile` anchor path.
**Test:**
- Profile holds a discipline value + a fitness goal (earned over real entries) → "should I skip the gym today?" gets an answer that references *your* stated goal, unprompted, with the source visible.
- Edit the profile (change the goal), ask again → Eva reflects the edit and it's locked against model overwrite. Delete `profile.md` → degrades gracefully.
**Done when:** Eva demonstrably knows the user, via the real L3 interface, and the user can audit and correct it. Commit.

### Phase 16 — Memory recall surfaced ("Eva remembers")
**Goal:** the recall the demo audience can *see* happen — real, correct, never fabricated.
**Build:**
- Per-turn recall: top-k past summaries from L2 (`journals` collection) above threshold, recency-weighted, injected as "context from past entries — reference only if relevant." (Builds directly on Phase 4 `recall` + Phase 15 assembly.)
- A subtle UI affordance when memory was used (a "remembering Jun 3" chip) so recall is visible.
**Read these:** the recall query in `retrieval.py`/`vector.py` and the prompt block that carries memories.
**Test:** journal a specific event on day 1; later ask "what's been on my mind lately?" → Eva references it correctly, chip shows the right date; ask about something never journaled → no false memories.
**Done when:** recall is correct, visible, and never fabricates. Commit.

---

### Phase 17 — Mood / emotion analytics (L4) + charts
**Goal:** visible mood/emotion tracking computed from real extractions — pure SQL, no model.
**Build:**
- `backend/memory/analytics.py`: `mood_series(range)` and emotion aggregations — SQL over the L1 mood/emotion tables (Phase 3). `GET /insights/mood?from=&to=`.
- Insights screen, first real block: a clean mood line/area chart (7/30-day toggle), dots per entry, hover shows that day's real summary; tasteful empty state when history is short.
- Optional dev convenience: the `# DEV-FIXTURE` backfill from Phase 14 lets you see a populated month during development — clearly marked, removable, real data through the real pipeline (never shipped).
**Read these:** the SQL in `analytics.py`; the chart component.
**Test:** with real (or dev-fixture) history the chart reads like a believable month; hover shows real summaries; a brand-new journal entry appears on it after extraction; empty vault → graceful empty state.
**Done when:** the mood story lands visually with real plumbing under it. Commit.

### Phase 18 — Knowledge graph (L4): real edges + evidence-gated hypotheses
**Goal:** an honest knowledge graph — association built deterministically, causal guesses clearly labeled.
**Build:**
- `analytics.py`: `build_graph()` — nodes from real L1 entities/themes/problems/goals; **edges from co-occurrence + temporal precedence + embedding similarity (all deterministic, code)**, plus a *small, evidence-gated* set of model-proposed "leads-to" edges **labeled as hypotheses** the user can confirm/reject (Memory Architecture §5.13). `GET /insights/graph` (nodes + typed/labeled edges).
- Knowledge-graph view: interactive force-directed graph (typed nodes; edges typed/labeled; hypothesis edges visually distinct); click a node/edge → side panel listing the **evidence entries**. Cytoscape or d3.
**Read these:** the `build_graph` edge logic (confirm association edges are deterministic), the endpoint schema.
**Test:** graph renders smoothly with the user's real ~N nodes; co-occurrence/precedence edges trace to real evidence; hypothesis edges are visibly marked and the evidence panel opens; empty vault → graceful empty state; no edge is shown without backing evidence.
**Done when:** the graph is real, auditable, and never presents a guess as fact. Commit.

### Phase 19 — Growth analytics (L4) + verification pass
**Goal:** comparative growth that reports *what changed*, computed deterministically, narrated descriptively — never a verdict.
**Build:**
- `analytics.py`: `period_delta(a, b)` — code computes deltas (mood, theme mix, open-loop resolution rate, goal-aligned vs goal-contradicting behavior counts) over two windows. `GET /insights/growth?a=&b=`. The model only narrates the computed deltas in the user's terms, framed as reflection with a closing question, **never a character judgment** (Memory Architecture §5.11).
- **Verification pass** (Memory Architecture §5.7): before a high-impact claim (a named recurring mistake, a growth statement) reaches the user, a cheap second model check — "is this supported by its cited evidence? yes/no" — gates it. Apply the same pass to weekly-mined patterns from Phase 14.
- Growth view: a descriptive period-comparison report (theme shifts, mood delta, reflective closing question).
**Read these:** the delta SQL (confirm code computes the numbers), the verification-pass gate.
**Test:** with two real periods, the report's numbers match hand-computed deltas; narration is descriptive, never a verdict; an unsupported claim is caught and dropped by the verification pass; empty/short history → graceful.
**Done when:** growth is real, descriptive, evidence-verified, and keeps the user as interpreter. Commit.

---

### Phase 20 — Guardrails & wellbeing (light rails + crisis-care path)
**Goal:** keep Eva safe before a stranger touches her, without making voice sluggish.
**Build:**
- Light input/output rails wrapping the engine's `check_in` / `check_out` nodes (System Design §5.13): block out-of-scope topics, enforce the grounded-citation rule, keep latency low. Local engine, telemetry off.
- Crisis-care path: detect signals of self-harm/abuse → respond with care + a gentle nudge toward a real person/professional — never clinical, never method detail. Fail closed: if the guardrail engine errors, no advice, plain acknowledgment.
**Read these:** the `check_in`/`check_out` wrappers, the crisis-care responder.
**Test:** a crisis-signal message → caring, non-clinical response with a nudge to real support (no method detail); an out-of-scope request → declined gracefully; guardrail engine forced to error → fails closed, chat still responds plainly.
**Done when:** the pipeline is wrapped, crisis-care is humane, and failure is safe. Commit.

### Phase 21 — Polish pass: states, settings, provider/network status, first-run
**Goal:** convert "works" into "impressive"; kill every rough edge a demo audience would notice.
**Build:**
- Loading/empty/error states audited on every screen; micro-interactions (hover, transitions, streaming cursor); consistent toasts.
- Settings screen for real: vault location (display + open-in-finder), voice on/off + speed, persona default, whisper size, model path/status.
- Provider/network status wired to truth: local mode shows outbound blocking status; online API mode shows the selected provider host and turns warning-red if any non-allowlisted call is attempted (socket guard in the backend).
- First-run experience: model/voices missing → a clean guided setup screen with copyable per-OS commands and a live "found ✓" check.
- Debug panel (System Design §9): show the assembled context, chosen intent, retrieved evidence pointers, and applied L3 operations — the auditability story made visible.
**Read these:** the socket guard; the debug panel; skim every screen as a user.
**Test:** fresh-clone run without the model → setup screen guides to local setup or opt-in API mode; local mode works with Wi-Fi off after setup; online mode clearly reports provider/network failures; the debug panel shows real intent + evidence for a live turn.
**Done when:** you'd hand your laptop to a stranger without hovering. Commit.

### Phase 22 — Hardening + packaging + demo script
**Goal:** protect the live demo from itself.
**Build:**
- Failure drills: model down, mic denied, Wi-Fi off, huge PDF, rapid-fire messages, mid-consolidation chat — each fails soft with a clear message and a retry.
- A "demo reset" script: clean vault → optional `# DEV-FIXTURE` history → ready state in one command (clearly non-shipping).
- `tauri build` for your demo OS; verify the packaged app end-to-end on a clean account/machine (backend sidecar + llama.cpp binary bundled).
- A one-page `DEMO_SCRIPT.md`: the exact 7–10 beat walkthrough (open → chat → voice → journal → upload book → grounded answer with citation → recall chip → mood chart → graph → growth → profile Eva genuinely built), with a fallback for each beat.
**Test:** run the full script twice on the packaged build, including one run with Wi-Fi off and one deliberate failure per category.
**Done when:** you can run the demo cold, twice in a row, without improvising. Commit + tag `v1`.

---

## 3. Genuinely out of v1 scope (deferred — but never faked)

These are *not* in v1, but nothing standing in for them is hardcoded — the feature is simply absent until built, behind the same real interfaces. Tracked as TODOs so nothing is silently forgotten.

| Deferred work | Plugs into | Why it can wait |
|---|---|---|
| Routing nightly/weekly consolidation to a larger model for richer narration | the write loop (Phase 14) shares one model; the seam is the scheduler | E2B is sufficient for v1; the real-time path is untouched either way |
| Photo attachments in entries | L0 already reserves space for them (Memory Architecture §1) | Text is the core ritual; images are additive |
| Time-travel UI beyond the past-entries date browser (juxtapose past entry vs current L3) | journal browse (Phase 7) + L3 (Phase 15) | The data is all there; this is a richer *view* over it |
| Full NeMo guardrails (beyond the light rails in Phase 20) | wraps the chat pipeline | Light rails + crisis-care cover the safety floor for v1 |
| macOS packaging (if demoing on Windows only) + full setup wizard | Phase 21/22 foundations | One platform for the demo; the build is cross-platform-ready |
| Multi-language (Kokoro voice + embedding model swap) | TTS (Phase 12) + L2 embed model (Phase 4) | English-only is a v1 scope choice, not a fake |
| GPU acceleration for llama-server / whisper | LLM runtime (Phase 1), STT (Phase 11) | CPU works; GPU is pure speed upside |

The distinction from v1's old "Deferred" table: **none of these are stand-ins for a shipped-but-faked feature.** Everything the user can see and touch in v1 is computed from their real data.

---

*Working agreement: one phase, one session, one commit. If a phase balloons past roughly a day of work, stop and split it — that's the plan failing, not you. The intelligence lives in the pipeline and the data structures, never in a hardcoded value.*
