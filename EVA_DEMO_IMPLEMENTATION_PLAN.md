# Eva — Demo Implementation Plan

**Small phases, strict priority order, every phase readable and testable by a human**
*Plan v1 · 2026-06-12 · executed one phase at a time via Claude Code / Codex*

---

## 0. How to work this plan

**The rules that keep it from becoming a mess:**

1. **One phase per AI session.** Paste the Global Rules (below) once, then exactly one phase. Never two. A fresh session per phase keeps the AI focused and the diff small.
2. **Every phase ends in a commit** named `phase-XX: <title>`. If a phase doesn't compile, run, and pass its checks, it doesn't get committed and the next phase doesn't start.
3. **You review before you commit.** Each phase lists the 2–4 files that matter ("Read these"). If you can't explain what they do after reading, make the AI add comments or simplify — that's a legitimate phase task.
4. **Priority is the order.** Phases are sorted so that if time runs out, you stop anywhere after Phase 8 and still have a coherent demo. Phases 13–15 are pure upside.
5. **Stubs live behind real seams.** Anything faked for the demo (profile, insights) is faked *behind the same interface the real component will use later*. Swapping in the real engine later is a drop-in, not a rewrite.
6. **Capture stays real.** Even while analysis is stubbed, every entry is genuinely saved (L0) and extracted (basic L1). You can't retroactively capture what you never stored.

**Using two AI tools:** the simplest split that works — use **Claude Code as the implementer** (it executes the phase), and use **Codex as the reviewer** (paste the diff, ask "find bugs, dead code, and anything that doesn't match the phase spec"). Or alternate implementers per phase if you prefer; just never have both editing the repo at once.

### Global Rules (paste once at the start of every AI session)

> These rules are also stored as `CLAUDE.md` at the repo root. Claude Code reads that file automatically at the start of every session, so you should never need to paste them manually — but verify the file is present before starting.

```
You are building "Eva", a fully offline desktop AI journaling companion.
Hardware target: MacBook M1 Air 8 GB RAM (Apple Silicon; Metal acceleration
available via -ngl 99). Voice models (faster-whisper, Kokoro) are lazy-loaded
on first use — never at startup — to stay within the 8 GB memory budget.
Stack: Tauri (Rust shell) + React/Vite frontend + Python FastAPI backend +
llama.cpp llama-server running gemma-4-E2B-it-qat-GGUF (port 11500, thinking
mode OFF, -ngl 99) + ChromaDB + SQLite + faster-whisper + Kokoro TTS.
English only.

llama-server command:
  export LLAMA_CACHE="unsloth/gemma-4-E2B-it-qat-GGUF"
  ./llama.cpp/llama-server \
      -hf unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL \
      --ctx-size 131072 \
      --cache-type-k q8_0 --cache-type-v q8_0 \
      --temp 1.0 --top-p 0.95 --top-k 64 \
      -ngl 99 \
      --port 11500

Context budget per request: ≤ 8 192 tokens for real-time chat turns;
≤ 32 768 for consolidation. The client sets max_tokens; never change
--ctx-size to do per-request limiting.

Rules:
1. Implement ONLY the phase given. Do not touch later phases. Do not refactor
   unrelated code.
2. Small, readable modules. Every public function gets a docstring saying what
   it does and why it exists. A human will read all of this code.
3. After implementing, run the phase's checks. Fix failures BEFORE reporting
   done. Then list: files changed, how to test manually, anything left TODO.
4. Privacy is hard law: no telemetry, no analytics, no outbound network calls
   at runtime. Only the first-run model/voice download is allowed.
5. Full journal entries are plain Markdown on disk — the source of truth.
   Databases are derived and rebuildable; Markdown never depends on them.
6. Stubs (profile, insights) go behind the same interface the real component
   will implement later. Mark them with  # DEMO-STUB  comments.
7. If anything is ambiguous about data storage, privacy, or Eva's behavior:
   STOP and ask. Do not guess.
8. End every phase with a git commit: "phase-XX: <title>".
```

---

## 1. Phase map (priority order)

| # | Phase | Milestone | Why this position |
|---|---|---|---|
| 0 | Scaffold: shell + backend + health | A — Spine | Nothing runs without it |
| 1 | Model online: streaming chat (backend only) | A — Spine | Proves gemma4 works before any UI |
| 2 | Vault: real entry capture (L0 + basic L1) | A — Spine | Capture must be real from day one |
| 3 | App shell UI + design system | B — Product | "Really good UI" starts here, not as an afterthought |
| 4 | Chat surface (streaming, polished) | B — Product | The core demo interaction |
| 5 | Journaling surface (separate from chat) | B — Product | Second pillar of the demo |
| 6 | Library: upload books / PDFs / text | B — Product | First half of the RAG story |
| 7 | Grounded answers with citations (RAG) | B — Product | The strongest *real* proof of the thesis |
| 8 | Voice in (push-to-talk STT) | B — Product | Wow factor, riskier — after the safe wins |
| 9 | Voice out (Kokoro + sentence queue) | B — Product | Completes "talk with Eva" |
| 10 | Polish pass: states, settings, offline badge | B — Product | Turns working into impressive |
| 11 | Memory recall ("Eva remembers") | C — Light intelligence | Cheap, real, on-message |
| 12 | Mood capture + chart | C — Light intelligence | Cheap, visible tracking |
| 13 | Static profile behind a real seam (Eva uses it) | D — Demo dressing | Demos L3's payoff without building L3 |
| 14 | Seeded insights: graph + growth report | D — Demo dressing | Completeness of surface area |
| 15 | Demo hardening + packaging + script | D — Demo dressing | Protects the live demo |

Milestone A = it runs. ▸ B = the demo product. ▸ C = it looks intelligent (really). ▸ D = it looks complete.

---

## 2. The phases

### Phase 0 — Scaffold: shell + backend + health
**Goal:** an empty but running app: Tauri window ↔ FastAPI backend over localhost.
**Build:**
- `CLAUDE.md` at the repo root containing the Global Rules block verbatim — Claude Code reads this file automatically, so rules are active without pasting.
- Tauri app with React + Vite frontend (`ui/`), backend in `backend/` (Python 3.11, FastAPI, venv).
- Backend launched as a sidecar by Tauri in dev mode (a `dev.sh` that starts both is fine for now).
- `GET /health` returns `{status, model_present: false}`; frontend shows a status dot that reads it.
- **Packaging spike:** create a "hello world" FastAPI endpoint, bundle it as a Tauri sidecar via PyInstaller (or embedded venv), and confirm the packaged binary launches correctly on macOS. This is a one-time proof; it does not need to be complete packaging — just proof the mechanism works. If this fails, solve it now, not in Phase 15.
- **Early socket guard:** at backend startup, install a global outbound socket block: any connection attempt to a host other than `127.0.0.1` / `localhost` raises an exception and is logged. The single allowlisted host for first-run download is controlled via an env var (`EVA_ALLOW_HOST`). The Offline ✓ badge UI wires in Phase 10, but the block must exist from Phase 0 so no library accidentally phones home during development.
- Repo hygiene: `.gitignore` (venv, node_modules, `local_vault/`), `README.md` with run instructions.
**Read these:** `backend/app.py`, `ui/src/App.tsx`, the dev script, the socket guard code.
**Test:**
- `dev.sh` opens the window; the status dot is green.
- `curl localhost:8000/health` returns 200 JSON.
- The packaged hello-world sidecar launches and responds.
- Attempt an outbound HTTP request from Python (e.g. `requests.get("https://example.com")`) → confirm it is blocked and logged.
**Done when:** app opens, backend answers, packaging spike passes, socket guard is live. Commit.

### Phase 1 — Model online: streaming chat (backend only)
**Goal:** gemma4 streams tokens through the backend. No UI yet — proven with curl.
**Build:**
- `backend/llm/server.py`: start/supervise `llama-server` with the exact command from the Global Rules (`-ngl 99`, port 11500, thinking OFF, temp 1.0 / top-p 0.95 / top-k 64). If the GGUF is missing, return a clear "model not found" state with the download command — never crash.
- `backend/llm/client.py`: async `stream_chat(messages, max_tokens=450, priority=True) -> token iterator` against the OpenAI-compatible endpoint. The `max_tokens` param enforces per-request context budgets; the server's `--ctx-size` is never changed per-request. The `asyncio.Lock` from EVA_SYSTEM_DESIGN.md §8 lives here.
- `WS /chat`: accepts a message, streams tokens back.
- `scripts/download_model_mac.sh`.
**Read these:** `llm/server.py`, `llm/client.py` — this is the heart of the app; understand both fully.
**Test:**
- With model present: a WebSocket client (`scripts/ws_test.py`) sends "hello", tokens stream back, reply is coherent.
- Move the GGUF away: `/health` reports `model_present: false` and the download command; nothing crashes.
- Confirm `-ngl 99` is in the server launch args; check `llama-server` logs for "Metal" or "GPU" offload confirmation.
**Done when:** streaming works, missing-model path is graceful, Metal offload is confirmed. Commit.

### Phase 2 — Vault: real entry capture (L0 + full L1)
**Goal:** everything the user writes is genuinely persisted from day one, with the complete L1 schema.

> **Before starting this phase:** the extraction prompt (`backend/prompts/extract_entry.md`) MUST be written and tested against the real model in a separate session. Run 5–10 realistic journal entries through it manually and confirm the JSON is consistently parseable. Do not begin Phase 2 code until the prompt passes that bar.

**Step A — Vault + schema (do this first; stop and review before Step B):**
- `backend/memory/vault.py`: append-only `local_vault/journal/YYYY-MM-DD.md` with YAML frontmatter; one file per day, turns appended with timestamps. Plain Markdown, readable without the app.
- `backend/memory/db.py`: apply `schema.sql` from `EVA_MEMORY_ARCHITECTURE.md §7.1` exactly — all tables, all columns, all constraints. Do not improvise or simplify the schema; missing columns cannot be retroactively added without a migration and data loss for early entries.
- Confirm: `sqlite3 local_vault/eva.db .schema` matches the spec. Commit checkpoint before moving to Step B.

**Step B — Extraction + ChromaDB + wiring:**
- `backend/memory/extract.py`: one bounded gemma4 call per saved entry using the pre-approved `extract_entry.md` prompt. Output is strict JSON matching the full L1 extraction schema. Retry once at `temperature=0.3` on parse failure; on second failure write `extraction_status='null_stored'` and store NULLs — never block saving. Log all failures.
- `backend/memory/vector.py`: ChromaDB persistent client; create the `journals` collection with `bge-small-en-v1.5`. On every successful extraction, embed the summary into `journals` with metadata `{entry_id, date, mood, themes, is_seeded: False}`. This is done here — not deferred to Phase 11 — so seeded data and real data are all embeddable from day one.
- Wire `/chat` so every user turn is saved (vault + db), extraction runs in the background, and on success the summary is embedded.
**Read these:** `vault.py` (the storage contract), `extract.py` (the prompt + JSON parsing), `vector.py` (the embedding path).
**Test:**
- Send 3 chat messages → today's `.md` contains all 3, correctly formatted; SQLite has 3 entry rows; after extraction, 3 rows in `extractions` with status `done` and all non-null fields; 3 vectors in the `journals` ChromaDB collection.
- Deliberately send malformed output from the model (mock the LLM response): confirm `null_stored` status, null fields, and that the vault entry still exists.
- Delete the SQLite file → Markdown remains complete and readable.
- Unit test: extraction JSON parser handles every bad-output case (empty string, truncated JSON, wrong schema).
**Done when:** capture is real, durable, survives DB loss, and ChromaDB is seeded from day one. Commit.

### Phase 3 — App shell UI + design system
**Goal:** the frame of a genuinely good-looking app, before any feature screens.
**Build:**
- Layout: left sidebar navigation (Chat · Journal · Library · Insights · Profile · Settings), main content area, top bar with persona selector (visual only for now) and an "Offline ✓" badge.
- Design tokens: one font pairing, a calm palette (light + dark), spacing scale, radii — as CSS variables in one file. Consistent button/input/card components (`ui/src/components/`).
- Empty-state screens for all six sections (real screens come in later phases).
- Follow the repo's frontend-design guidance for a distinctive, non-generic look — this phase decides whether the app feels premium.
**Read these:** the tokens file, the layout component.
**Test:**
- Click through all six sections; every screen renders an intentional empty state (no dead links, no lorem ipsum).
- Toggle dark/light; nothing breaks. Resize to a small window; layout holds.
**Done when:** the app already *looks* like a product with no features in it. Commit.

### Phase 4 — Chat surface (streaming, polished)
**Goal:** talking to Eva in text feels alive and finished.
**Build:**
- Chat screen wired to `WS /chat`: user bubbles, Eva bubbles that stream token-by-token with a typing indicator, auto-scroll, error toast + retry if the socket drops.
- Conversation history for the current session (in memory + persisted via Phase 2 capture).
- System prompt assembled from template slots in `backend/prompts/assembly.py`. The slots are: `{persona_block}` (from `eva_system.md` — loaded as-is), `{memory_context}` (empty until Phase 11), `{profile_slices}` (empty until Phase 13), `{corpus_context}` (empty until Phase 7). Each slot is a separate string, never concatenated by hand. This architecture means adding memory or profile context later is a one-line change, not a prompt surgery.
- Eva's persona block: `backend/prompts/eva_system.md` — companion identity, warm, listens first. Token cap: 450 for default persona.
- **Interim crisis-care rule** (active until NeMo guardrails are built in a later phase): before the assembled prompt reaches the model, run a cheap keyword check (`backend/safety/crisis_check.py`) over the user's input. Keywords: `end my life`, `kill myself`, `don't want to be here`, `hurt myself` (and close variants). On match, append a crisis-aware addendum to the persona block instructing Eva to acknowledge with care and gently encourage reaching out — do not suppress the reply or hand off to a bot script.
- Enter to send, Shift+Enter newline; input disabled while Eva is replying.
**Read these:** `assembly.py` (the template), `eva_system.md`, `crisis_check.py`.
**Test:**
- Hold a 6+ turn conversation; streaming is smooth, history correct, today's `.md` captured everything.
- Kill the backend mid-reply → UI shows a graceful error and recovers on restart.
- Send a crisis-signal message → Eva's reply is warm and mentions reaching out; it does not ignore the signal or lecture.
**Done when:** a stranger could chat with Eva and nothing would feel broken. Commit.

### Phase 5 — Journaling surface (separate from chat)
**Goal:** journaling is its own ritual, not a chat thread.
**Build:**
- Journal screen: a calm full-width editor for today's entry ("How was your day?"), explicit Save, autosave draft every ~10 s.
- Saving writes to the same vault (entry type `journal`) and triggers the same extraction.
- A browseable past-entries list (by date, from SQLite) with a read-only day view — this is the seed of time-travel.
- After save, one gentle Eva acknowledgment line (single bounded LLM call): a reflection or soft question, never advice.
**Read these:** the journal screen, the save path in `vault.py` (chat vs journal types).
**Test:**
- Write and save an entry → appears in the day file, the list, and gets an extraction row; Eva's acknowledgment shows and is appropriate in tone.
- Reopen the app → draft/entry still there. Past-day view renders an older (hand-placed) file correctly.
**Done when:** journaling works end-to-end and feels distinct from chat. Commit.

### Phase 6 — Library: upload books / PDFs / text
**Goal:** the user can hand Eva their books; the corpus pipeline is real.
**Build:**
- `backend/ingest/loaders.py` (pdf via pypdf/pdfplumber, md, txt) + `chunker.py` (~500-token chunks, overlap, source + page/section metadata).
- `backend/memory/vector.py`: ChromaDB persistent client in the vault; `corpus` collection; FastEmbed `bge-small-en-v1.5`.
- `POST /corpus/upload` (file → load → chunk → embed → indexed) and `GET /corpus` (list with chunk counts / status).
- Library screen: drag-and-drop upload, progress state, list of ingested documents, remove.
**Read these:** `chunker.py` (chunk + metadata shape), `vector.py`.
**Test:**
- Upload a real PDF (50+ pages), a .md, a .txt → all index with sensible chunk counts; library lists them; progress and failure states render (try a corrupt file).
- A quick script queries the corpus collection for a phrase from the book and gets the right chunk back.
**Done when:** documents go in reliably and are queryable. Commit.

### Phase 7 — Grounded answers with citations (RAG)
**Goal:** Eva answers from the user's documents — the strongest real proof of the thesis.
**Build:**
- `backend/memory/retrieval.py`: embed query → top-k corpus chunks above a relevance threshold.
- Chat integration: retrieved passages injected into the prompt with a hard rule — *answer from the passages; if they don't contain it, say so; never invent a quote or citation.*
- Citations rendered in the chat UI as small source chips (file + page/section); clicking shows the passage text.
- **Minimal intent classifier** (`backend/intent/classifier.py`): a 3-class classifier — `vent`, `question`, `advice_request` — that runs on every user message before retrieval. This is not fully deferred; the test "pure venting message → no retrieval fires" requires it. Implementation: a rule-based layer first (question marks, advice keywords like "what should I do", "any advice", "help me think") with a tiny prompt-based fallback for ambiguous cases. Mark the seam clearly (`# INTENT-SEAM: replace with full 5-class classifier`) so the real intent engine (vent/process/ask_info/ask_advice/ambient) plugs in later. RAG retrieval fires only on `question` and `advice_request`; `vent` class bypasses retrieval entirely.
**Read these:** `retrieval.py`, the grounded prompt template, `classifier.py` — check the no-invented-citations rule and the vent-bypass yourself.
**Test:**
- Ask something answered in the uploaded book → correct answer + correct chip pointing at the right page.
- Ask something NOT in any document → Eva says she doesn't find it in your library (no fabricated citation).
- Pure venting message (no question mark, no advice keyword) → no retrieval fires, no citations appear. Confirm via log.
- Ambiguous message ("I don't know what to do") → check that the fallback classifier fires and produces a reasonable label.
**Done when:** grounded answers are accurate, cited, and honest about gaps; venting bypass is confirmed in logs. Commit.

### Phase 8 — Voice in (push-to-talk STT)
**Goal:** speak to Eva instead of typing.
**Build:**
- `backend/voice/stt.py`: faster-whisper, **model size configurable via settings** (default `base.en` int8; user can switch to `small.en` in Settings if transcription quality is poor on their accent). Lazy-loaded on first STT request — not at backend startup. `POST /stt` (audio → text).
- Mic button in chat + journal: hold-to-record (or click-toggle), 120 s cap, level indicator while recording.
- Flow: release → transcribe → text appears in the input box for confirmation → sends through the normal pipeline (so capture/RAG are identical for voice and text).
- The Settings screen (Phase 10) exposes the Whisper model size as a dropdown; `stt.py` reloads the model on change.
**Read these:** `stt.py`, the recorder hook in the UI.
**Test:**
- Speak two sentences → accurate transcription lands in the input within ~1.5 s of release (on M1 Air with Metal).
- 120 s cap enforced; mic-permission-denied shows a helpful message, not a crash.
- Switch to `small.en` in settings → the next transcription uses the new model (confirmed via log).
**Done when:** voice input is accurate and stable on the demo machine. Commit.

### Phase 9 — Voice out (Kokoro + sentence queue)
**Goal:** Eva speaks her replies naturally, starting almost immediately.
**Build:**
- `backend/voice/tts.py`: Kokoro, voice `af_heart`, text → 24 kHz wav chunk. Lazy-loaded on first TTS request.
- `backend/voice/sentence_queue.py`: consume the token stream, buffer to sentence boundaries, synth each sentence, emit ordered audio chunks over the chat WebSocket alongside text. Implement the **sentence-splitter rules from `EVA_MEMORY_ARCHITECTURE.md §7.5` exactly** — abbreviation list, number-period rule, open-quote rule, 4-word minimum, 80-word maximum flush. Do not use `nltk.sent_tokenize`.
- UI: sequential audio playback queue; voice on/off toggle in the top bar; stop-speaking button.
**Read these:** `sentence_queue.py` — the trickiest concurrency in the app; have Claude Code comment every state transition in the splitter until you can explain each one.
**Test:**
- Voice on, ask a multi-sentence question → Eva starts speaking ≤ ~2.5 s after generation starts, sentences play in order, no overlap/cutoff, text and audio match.
- Toggle voice off mid-reply → audio stops, text continues.
- Test the exact string: `"He saw Dr. Smith, who paid $3.50 for it. Then left."` → must split into exactly two TTS chunks (before "Then"), not three or four.
- Test: `"She said 'I'll be there'"` (open quote) → no split inside the quoted phrase.
**Done when:** talking with Eva feels like a conversation, not a buffer; all splitter test cases pass. Commit.

### Phase 10 — Polish pass: states, settings, offline badge
**Goal:** convert "works" into "impressive"; kill every rough edge a demo audience would notice.
**Build:**
- Loading/empty/error states audited on every screen; micro-interactions (hover, transitions, streaming cursor); consistent toasts.
- Settings screen for real: vault location (read-only display + open-in-finder), voice on/off + speed, whisper size, model path/status.
- Offline badge wired to truth: backend confirms no outbound configured; badge turns warning-red if any non-allowlisted call is ever attempted (simple socket-guard in the backend).
- First-run experience: if model/voices missing, a clean guided setup screen with copyable per-OS commands and a live "found ✓" check (wizard-lite, not the full Phase-8-of-old wizard).
**Read these:** the socket guard; skim every screen as a user.
**Test:**
- Fresh-clone run on a machine without the model → setup screen guides to a working chat without touching a terminal error.
- Disconnect Wi-Fi entirely → every feature still works (the real offline proof).
**Done when:** you'd hand your laptop to a stranger without hovering. Commit.

### Phase 11 — Memory recall ("Eva remembers")
**Goal:** the cheapest real intelligence: Eva references your past entries.
**Build:**
- Add a `journals` collection: on every extraction, embed the 4–5-sentence summary with metadata (date, mood, themes).
- Retrieval per turn: top-k past summaries above threshold, recency-weighted; injected into the prompt as "context from past entries — reference only if relevant."
- A subtle UI affordance when memory was used (e.g. a "remembering Jun 3" chip), so the demo audience *sees* the recall happen.
**Read these:** the recall query in `retrieval.py` and the prompt block that carries memories.
**Test:**
- Day 1: journal about a specific event. Later: "what's been on my mind lately?" → Eva references it correctly, chip shows the right date.
- Ask about something never journaled → no false memories.
**Done when:** recall is correct, visible, and never fabricates. Commit.

### Phase 12 — Mood capture + chart
**Goal:** visible mood tracking from the data you're already extracting.
**Build:**
- `GET /insights/mood?from=&to=` — SQL over the `mood_series` table (populated by extraction, no LLM). Filter `WHERE is_seeded = 0` for live data; the endpoint accepts a `?include_seeded=true` param for the demo chart.
- Insights screen, first real block: a clean mood line/area chart (7/30-day toggle), dots per entry, hover shows that day's summary; tasteful empty state when history is short. NULL mood days show as gaps in the line, never as zero.
- Seed script: `scripts/seed_demo.py` generates ~3 weeks of backdated entries + extractions + mood_series rows, all with `is_seeded = 1`. Seed data is clearly marked in the DB and excluded from recall queries (the `journals` ChromaDB collection filters `is_seeded=False`). Run the seed script before the demo; it is safe to run on a vault that already has real entries.
**Read these:** the SQL in the insights endpoint; the seed script (you'll run it before the demo).
**Test:**
- With seeded data the chart reads like a believable month; hover shows real summaries; a new (real) journal entry appears on the chart after extraction with `is_seeded = 0`.
- Confirm seeded entries do NOT appear in recall (Phase 11 memory chip must not surface seed data).
**Done when:** the mood story lands visually with real plumbing under it. Commit.

### Phase 13 — Static profile behind a real seam (Eva uses it)
**Goal:** demo the *payoff* of the evolving profile without building the update engine.
**Build:**
- `backend/memory/profile.py` with the **real future interface**: `get_profile() -> Profile`, `get_slices(topic) -> fragments`. For now it reads a hand-written `local_vault/profile.json` + `local_vault/profile.md`. The JSON **must conform to the `profile.json` schema from `EVA_MEMORY_ARCHITECTURE.md §7.2`** exactly — same fields, same types, same structure the real L3 engine will write. `# DEMO-STUB: replaced by L3 engine`.
- Chat integration: relevant profile slices included in Eva's context every turn via the `{profile_slices}` slot in `assembly.py` — she genuinely knows your goals/values and tailors replies.
- Profile screen: renders `profile.md` nicely, with an edit button (edits persist to both `profile.md` and `profile.json` via the sync described in §7.2 of the Memory Architecture doc — this is also the future human-correction anchor).
**Read these:** `profile.py` (the seam), the context-assembly code in `assembly.py`.
**Test:**
- Profile says you value discipline + a fitness goal → "should I skip the gym today?" gets an answer that references *your* stated goal, unprompted.
- Edit the profile (change a goal), ask again → Eva reflects the edit. Delete `profile.json` → app degrades gracefully (no profile context, no crash).
**Done when:** Eva demonstrably knows who you are, via the same interface the real L3 will implement. Commit.

### Phase 14 — Seeded insights: graph + growth report
**Goal:** complete the surface area — every promised screen exists and looks finished.
**Build:**
- `GET /insights/graph` and `GET /insights/growth?a=&b=` returning **seeded but well-shaped** data (`# DEMO-STUB`). Both endpoints must return data conforming exactly to the schemas in `EVA_MEMORY_ARCHITECTURE.md §7.4` (graph) and the growth shape in the System Design §11 — the real L4 must satisfy these exact schemas later.
- Knowledge-graph view: interactive force-directed graph (themes/people/problems as typed nodes; association edges; click → side panel listing the "evidence" entries). Cytoscape or d3. Hypothesis edges render dashed with confirm/dismiss affordance.
- Growth view: a descriptive period-comparison report (theme shifts, mood delta, a reflective closing question) — framed as reflection, never a verdict.
- Where seeded data can cheaply be real, let it be: graph nodes from actual extracted themes/entities with co-occurrence edges — these are a day's work and make the graph honest. All seeded graph data has `is_seeded=1` in `graph_nodes`/`graph_edges` so it can be pruned later.
**Read these:** the two endpoint schemas (they're the contract the real L4 must satisfy later).
**Test:**
- Graph renders smoothly with ~30 nodes, interactions feel good, evidence panel opens; growth report reads as thoughtful and descriptive.
- Both screens behave with empty data (fresh vault).
- Both endpoints return JSON that validates against the §7.4 schema (write a quick `scripts/validate_graph.py`).
**Done when:** Insights feels like a finished product area and schemas are validated. Commit.

### Phase 15 — Demo hardening + packaging + script
**Goal:** protect the live demo from itself.
**Build:**
- Failure drills: model down, mic denied, Wi-Fi off, huge PDF, rapid-fire messages — each fails soft with a clear message and a retry.
- A "demo reset" script: clean vault → seed → ready state in one command.
- `tauri build` for your demo OS; verify the packaged app end-to-end on a clean account/machine.
- A one-page `DEMO_SCRIPT.md`: the exact 7–10 beat walkthrough (open → chat → voice → journal → upload book → grounded answer with citation → recall chip → mood chart → graph → profile), with the fallback for each beat (e.g. voice fails → type instead, pre-recorded clip as last resort).
**Test:** run the full script twice on the packaged build, including one run with Wi-Fi off and one deliberate failure per category.
**Done when:** you can run the demo cold, twice in a row, without improvising. Commit + tag `demo-v1`.

---

## 3. Deferred (real engines behind the seams) — explicitly NOT in the demo

Tracked as TODOs so nothing is silently forgotten. Each plugs into a seam that already exists:

| Deferred work | Plugs into |
|---|---|
| L3 update engine (operations, evidence pointers, confidence/decay, contradiction resolution) | `profile.py` seam (Phase 13) |
| Nightly/weekly consolidation + APScheduler + rollup digests | background jobs; feeds profile + insights |
| Real pattern/mistake mining (behavior-vs-goal contradictions) | growth + graph endpoints (Phase 14 schemas) |
| Real knowledge-graph edges (temporal precedence, hypothesis edges) | `GET /insights/graph` |
| Real growth analytics (period deltas in SQL) | `GET /insights/growth` |
| Full 5-class intent classifier (vent/process/ask_info/ask_advice/ambient) | replaces the Phase 7 3-class stub at the `# INTENT-SEAM` marker |
| Personas (close friend / coach / mentor) | persona selector (visual since Phase 3) |
| NeMo guardrails + full crisis-care path | replaces the Phase 4 keyword `crisis_check.py` |
| Time-travel UI (beyond the past-entries list), photo attachments | journal browse (Phase 5) |
| macOS full packaging + setup wizard | Phase 10/15 foundations |
| `scripts/reindex.py` (ChromaDB re-embed on model change) | `vector.py` versioning guard |
| `profile.md` ↔ `profile.json` bidirectional parser | `PUT /profile` endpoint (stub exists in Phase 13) |

---

*Working agreement: one phase, one session, one commit. If a phase balloons past roughly a day of work, stop and split it — that's the plan failing, not you.*
