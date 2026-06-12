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

```
You are building "Eva", a fully offline desktop AI journaling companion.
Stack: Tauri (Rust shell) + React/Vite frontend + Python FastAPI backend +
llama.cpp llama-server running gemma-4-E2B-it-qat-GGUF (port 11500, thinking
mode OFF) + ChromaDB + SQLite + faster-whisper + Kokoro TTS. English only.

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
- Tauri app with React + Vite frontend (`ui/`), backend in `backend/` (Python 3.11, FastAPI, venv).
- Backend launched as a sidecar by Tauri in dev mode (a `dev.sh` / `dev.ps1` that starts both is fine for now).
- `GET /health` returns `{status, model_present: false}`; frontend shows a status dot that reads it.
- Repo hygiene: `.gitignore` (venv, node_modules, `local_vault/`), `README.md` with run instructions.
**Read these:** `backend/app.py`, `ui/src/App.tsx`, the dev script.
**Test:**
- `dev` script opens the window; the status dot is green.
- `curl localhost:8000/health` returns 200 JSON.
**Done when:** app opens, backend answers, repo is clean. Commit.

### Phase 1 — Model online: streaming chat (backend only)
**Goal:** gemma4 streams tokens through the backend. No UI yet — proven with curl.
**Build:**
- `backend/llm/server.py`: start/supervise `llama-server` with the model (`unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL`, port 11500, `--jinja`, thinking OFF, temp 1.0 / top-p 0.95 / top-k 64). If the GGUF is missing, return a clear "model not found" state with the per-OS download command — never crash.
- `backend/llm/client.py`: async `stream_chat(messages) -> token iterator` against the OpenAI-compatible endpoint.
- `WS /chat`: accepts a message, streams tokens back.
- `scripts/download_model_mac.sh` + `scripts/download_model_win.ps1`.
**Read these:** `llm/server.py`, `llm/client.py` — this is the heart of the app; understand both fully.
**Test:**
- With model present: a WebSocket client (small `scripts/ws_test.py`) sends "hello", tokens stream back, reply is coherent.
- Move the GGUF away: `/health` reports `model_present: false` and the download command; nothing crashes.
**Done when:** streaming works and the missing-model path is graceful. Commit.

### Phase 2 — Vault: real entry capture (L0 + basic L1)
**Goal:** everything the user writes is genuinely persisted from day one.
**Build:**
- `backend/memory/vault.py`: append-only `local_vault/journal/YYYY-MM-DD.md` with YAML frontmatter; one file per day, turns appended with timestamps. Plain Markdown, readable without the app.
- `backend/memory/db.py`: SQLite with two tables for now — `entries` (id, date, type: chat|journal, text, created_at) and `extractions` (entry_id, summary, mood int −5..+5, entities JSON, themes JSON). FTS5 on text.
- `backend/memory/extract.py`: one bounded gemma4 call per saved entry → `{summary (4–5 sentences), mood, entities, themes}` as strict JSON (few-shot prompt; on parse failure retry once, then store nulls — never block saving).
- Wire `/chat` so every user turn is saved (vault + db) and extraction runs in the background.
**Read these:** `vault.py` (the storage contract), `extract.py` (the prompt + JSON parsing).
**Test:**
- Send 3 chat messages → today's `.md` contains all 3, correctly formatted; SQLite has 3 entry rows and (after a moment) 3 extraction rows with plausible mood/summary.
- Delete the SQLite file → Markdown remains complete and readable. Unit test: extraction JSON parser handles malformed output.
**Done when:** capture is real, durable, and survives DB loss. Commit.

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
- Eva's first system prompt: `backend/prompts/eva_system.md` — companion identity, warm, listens first, concise replies (~450 token cap). Keep it simple; personas come later.
- Enter to send, Shift+Enter newline; input disabled while Eva is replying.
**Read these:** the chat component, `eva_system.md`.
**Test:**
- Hold a 6+ turn conversation; streaming is smooth, history correct, today's `.md` captured everything.
- Kill the backend mid-reply → UI shows a graceful error and recovers on restart.
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
- A simple trigger for v1: retrieval runs when the user's message asks a question (heuristic or tiny classifier) — full intent-mode gating is a later TODO; mark the seam.
**Read these:** `retrieval.py`, the grounded prompt template — check the no-invented-citations rule yourself.
**Test:**
- Ask something answered in the uploaded book → correct answer + correct chip pointing at the right page.
- Ask something NOT in any document → Eva says she doesn't find it in your library (no fabricated citation).
- Pure venting message → no retrieval fires, no citations appear.
**Done when:** grounded answers are accurate, cited, and honest about gaps. Commit.

### Phase 8 — Voice in (push-to-talk STT)
**Goal:** speak to Eva instead of typing.
**Build:**
- `backend/voice/stt.py`: faster-whisper `base.en` int8, loaded once; `POST /stt` (audio → text).
- Mic button in chat + journal: hold-to-record (or click-toggle), 120 s cap, level indicator while recording.
- Flow: release → transcribe → text appears in the input box for confirmation → sends through the normal pipeline (so capture/RAG are identical for voice and text).
**Read these:** `stt.py`, the recorder hook in the UI.
**Test:**
- Speak two sentences → accurate transcription lands in the input within ~2 s of release.
- 120 s cap enforced; mic-permission-denied shows a helpful message, not a crash.
**Done when:** voice input is accurate and stable on your demo machine. Commit.

### Phase 9 — Voice out (Kokoro + sentence queue)
**Goal:** Eva speaks her replies naturally, starting almost immediately.
**Build:**
- `backend/voice/tts.py`: Kokoro, voice `af_heart`, text → 24 kHz wav chunk.
- `backend/voice/sentence_queue.py`: consume the token stream, buffer to sentence boundaries (handle abbreviations/numbers), synth each sentence, emit ordered audio chunks over the chat WebSocket alongside text.
- UI: sequential audio playback queue; voice on/off toggle in the top bar; stop-speaking button.
**Read these:** `sentence_queue.py` — the trickiest concurrency in the app; make the AI comment the ordering logic until you can explain it.
**Test:**
- Voice on, ask a multi-sentence question → Eva starts speaking ≤ ~2.5 s after generation starts, sentences play in order, no overlap/cutoff, text and audio match.
- Toggle voice off mid-reply → audio stops, text continues. A reply with "Dr. Smith paid $3.50." doesn't split mid-abbreviation.
**Done when:** talking with Eva feels like a conversation, not a buffer. Commit.

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
- `GET /insights/mood?from=&to=` — SQL over the extractions table (no LLM).
- Insights screen, first real block: a clean mood line/area chart (7/30-day toggle), dots per entry, hover shows that day's summary; tasteful empty state when history is short.
- Seed script: `scripts/seed_demo.py` generates ~3 weeks of plausible backdated entries + extractions so the demo has history (clearly marked, removable).
**Read these:** the SQL in the insights endpoint; the seed script (you'll run it before the demo).
**Test:**
- With seeded data the chart reads like a believable month; hover shows real summaries; a new journal entry appears on it after extraction.
**Done when:** the mood story lands visually with real plumbing under it. Commit.

### Phase 13 — Static profile behind a real seam (Eva uses it)
**Goal:** demo the *payoff* of the evolving profile without building the update engine.
**Build:**
- `backend/memory/profile.py` with the **real future interface**: `get_profile() -> Profile`, `get_slices(topic) -> fragments`. For now it reads a hand-written `local_vault/profile.md` + `profile.json` (identity & aspirations, 2–3 goals, 2 recurring patterns, key people, emotional baseline). `# DEMO-STUB: replaced by L3 engine`.
- Chat integration: relevant profile slices included in Eva's context every turn — she genuinely knows your goals/values and tailors replies (this is the highest-leverage fake in the project: the *use* is real, only the *updating* is stubbed).
- Profile screen: renders `profile.md` nicely, with an edit button (edits persist — this is also the future human-correction anchor).
**Read these:** `profile.py` (the seam), the context-assembly code that injects slices.
**Test:**
- Profile says you value discipline + a fitness goal → "should I skip the gym today?" gets an answer that references *your* stated goal, unprompted.
- Edit the profile (change a goal), ask again → Eva reflects the edit. Delete `profile.md` → app degrades gracefully (no profile context, no crash).
**Done when:** Eva demonstrably knows who you are, via the same interface the real L3 will implement. Commit.

### Phase 14 — Seeded insights: graph + growth report
**Goal:** complete the surface area — every promised screen exists and looks finished.
**Build:**
- `GET /insights/graph` and `GET /insights/growth?a=&b=` returning **seeded but well-shaped** data (`# DEMO-STUB`), schemas matching the system-design doc.
- Knowledge-graph view: interactive force-directed graph (themes/people/problems as typed nodes; association edges; click → side panel listing the "evidence" entries). Cytoscape or d3.
- Growth view: a descriptive period-comparison report (theme shifts, mood delta, a reflective closing question) — framed as reflection, never a verdict.
- Where seeded data can cheaply be real, let it be: e.g. graph nodes from actual extracted themes/entities with co-occurrence edges is ~a day and makes the graph honest — do it if schedule allows, otherwise seed.
**Read these:** the two endpoint schemas (they're the contract the real L4 must satisfy later).
**Test:**
- Graph renders smoothly with ~30 nodes, interactions feel good, evidence panel opens; growth report reads as thoughtful and descriptive.
- Both screens behave with empty data (fresh vault).
**Done when:** Insights feels like a finished product area. Commit.

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
| Nightly/weekly consolidation + scheduler + rollup digests | background jobs; feeds profile + insights |
| Real pattern/mistake mining (behavior-vs-goal contradictions) | growth + graph endpoints (Phase 14 schemas) |
| Real knowledge-graph edges (temporal precedence, hypothesis edges) | `GET /insights/graph` |
| Real growth analytics (period deltas in SQL) | `GET /insights/growth` |
| Intent classifier (vent/process/ask_info/ask_advice) + listen-first retrieval gating | the retrieval-trigger seam (Phase 7) |
| Personas (close friend / coach / mentor) | persona selector (visual since Phase 3) |
| NeMo guardrails + crisis-care path | wraps the chat pipeline |
| Time-travel UI (beyond the past-entries list), photo attachments | journal browse (Phase 5) |
| Windows + macOS dual packaging, full setup wizard | Phase 10/15 foundations |

---

*Working agreement: one phase, one session, one commit. If a phase balloons past roughly a day of work, stop and split it — that's the plan failing, not you.*
