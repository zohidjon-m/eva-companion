# Eva — System Design & Architecture

**A privacy-first hybrid AI journaling companion**
*Architecture doc v1 · 2026-06-09 · Tauri (macOS + Windows) · Gemma 4 E2B (QAT GGUF) · Kokoro TTS · faster-whisper STT*

---

## 1. Context

Eva is a desktop companion the user journals with daily — by voice or text, for 5–15 minutes — the way you'd talk to a close friend. The differentiator is not chat; it is a **deep, evolving model of the user** that makes every conversation more accurate over time. Local AI remains the privacy-first default; online API mode is opt-in and may send prompts and selected context only to the configured provider host.

This document defines the system: its processes, components, data, runtime flows, and the trade-offs behind them. It sits on top of two companion docs — the **Memory Architecture** (the five layers and their rules) and the **Build Plan** (phased execution) — and assumes them.

### Goals
- A single installable app for macOS and Windows that works locally after first-run setup, with optional online API mode for users who choose it.
- Natural, low-latency two-way voice plus text.
- A memory that visibly improves the companion the longer it's used.
- All nine product features (venting, opt-in grounded advice, evolving memory, mood tracking, pattern detection, knowledge graph, time-travel recall, growth analytics).
- The user owns and can read/edit/delete all their data in plain files.

### Non-goals (v1)
- No required cloud account, no sync, no telemetry.
- No multi-user or sharing.
- Not a therapist, coach-bot, or roleplay character — a companion that listens first.
- No always-on listening (push-to-talk only).

---

## 2. Constraints & key drivers

These shape every decision downstream:

1. **Small local model.** Gemma 4 E2B (~3 GB). Reliable only on small, bounded jobs — single-entry extraction and short narration over pre-assembled evidence. Never long-horizon reasoning. *(The "bad historian, good clerk" principle.)*
2. **Privacy-first hybrid model.** Local mode blocks outbound runtime network access except approved first-run downloads. Online API mode is opt-in, visibly labeled, and restricted to the configured provider host. The L3 user model is effectively a psychological profile and must stay in local, user-owned files.
3. **Cross-platform desktop.** macOS + Windows, including CPU-only Windows machines (slower, must still work).
4. **Real-time voice UX.** Perceived time-to-first-speech ~1–2.5 s, end-to-end ~3–5 s on a mid laptop — achieved via sentence-queue streaming, not a faster model.
5. **Python-heavy ecosystem.** LangGraph, ChromaDB, faster-whisper, Kokoro, NeMo, FastEmbed are all Python — the backend is Python; the shell is Tauri.

---

## 3. High-level architecture

Three local processes, one machine, plus optional provider network access only in opt-in online API mode.

```
┌────────────────────────────────────────────────────────────────────────────┐
│  USER'S MACHINE — local-first; provider network only in opt-in API mode      │
│                                                                              │
│  ┌─────────────────────────────┐                                            │
│  │ PROCESS 1 — Tauri shell      │   localhost HTTP + WebSocket               │
│  │ (Rust + web UI)              │ ◄──────────────────────────────┐          │
│  │  • chat / journaling surface │   chat stream · audio chunks    │          │
│  │  • insights surface          │   · state · insights            │          │
│  │  • mic (push-to-talk)        │                                 ▼          │
│  │  • setup wizard, settings    │                  ┌──────────────────────┐  │
│  │  • provider/network status   │                  │ PROCESS 2 — Backend   │  │
│  └─────────────────────────────┘                  │ (Python / FastAPI)    │  │
│                                                    │                       │  │
│   ┌──────────────────────────────────────────┐    │  Conversation engine  │  │
│   │ Voice (in-process workers)               │    │  (read loop):         │  │
│   │  STT: faster-whisper ◄── mic audio       │◄───┤  classify→retrieve→   │  │
│   │  TTS: Kokoro ──► sentence-by-sentence wav │    │  reason→check→persist │  │
│   └──────────────────────────────────────────┘    │                       │  │
│                                                    │  Consolidation        │  │
│   ┌──────────────────────────────────────────┐    │  pipeline (write loop)│  │
│   │ PROCESS 3 — llama-server (sidecar)        │◄───┤  + scheduler          │  │
│   │  Gemma 4 E2B QAT GGUF                     │    │                       │  │
│   │  OpenAI-compatible endpoint :11500        │    │  Guardrails (light)   │  │
│   └──────────────────────────────────────────┘    └───────────┬───────────┘  │
│                                                                │              │
│   THE VAULT — plain, user-owned files (~/EvaVault)             │              │
│   ┌──────────────────────────────────────────────────────────▼───────────┐  │
│   │ Markdown (L0): full daily entries + photos  — source of truth          │  │
│   │ SQLite (L1/L4): episode records, time-series, graph, digests (+FTS5)    │  │
│   │ ChromaDB (L2): journal summaries + corpus embeddings                    │  │
│   │ profile.md + profile.json (L3): the evolving user model (user-editable) │  │
│   │ corpus/: user-uploaded books (PDF/md/txt)                               │  │
│   └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│   Network guard: local blocks outbound; online allows selected provider only.│
└────────────────────────────────────────────────────────────────────────────┘
                              ✗/✓  Internet (blocked locally; provider-only online)
```

**Why three processes:** the UI must stay responsive (Tauri/Rust), the AI/ML stack is Python (backend), and the model server is a separate native binary with its own lifecycle (llama-server). Keeping them as separate processes isolates crashes — if llama-server dies, the backend restarts it without taking down the UI.

---

## 4. Process & deployment model

| Process | Tech | Responsibility | Lifecycle |
|---|---|---|---|
| Shell | Tauri (Rust) + web frontend | Window, UI, mic capture, audio playback, IPC to backend | Launched by the user; owns the others |
| Backend | Python 3.11 + FastAPI (asyncio) | All orchestration, memory, voice workers, guardrails | Spawned by the shell as a sidecar on launch |
| Model server | llama.cpp `llama-server` | Serves Gemma 4 E2B over an OpenAI-compatible endpoint | Spawned & supervised by the backend |

- **Packaging:** Tauri bundles the frontend and ships the backend as a packaged sidecar (PyInstaller or an embedded venv) plus the llama.cpp binaries for the target OS. One installer per platform. *Do a packaging spike in Phase 0 — before any real code — to verify the sidecar mechanism works on the target OS; do not leave this to Phase 15.*
- **First run:** the setup wizard offers local AI setup or opt-in online API mode. Local setup downloads the model GGUF, the Kokoro voice, and the faster-whisper weights into the vault/cache, then verifies them. After that local mode works offline; online API mode may contact only the configured provider host.
- **Voice workers** (faster-whisper, Kokoro) are **lazy-loaded on first use**, not at backend startup. On M1 Air 8GB, loading all voice models at startup alongside the model server exhausts available RAM. Load faster-whisper on the first STT request; load Kokoro on the first TTS request; keep them loaded thereafter.
- **Model serving choice:** `llama-server` over Ollama because it exposes a clean OpenAI-compatible endpoint (clean for both the engine and NeMo), gives explicit control of generation params and thinking-mode, and avoids Ollama's then-current audio crashes. (See §10.)
- **Exact server command (macOS / M1):**
  ```sh
  export LLAMA_CACHE="unsloth/gemma-4-E2B-it-qat-GGUF"
  ./llama.cpp/llama-server \
      -hf unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL \
      --ctx-size 131072 \
      --cache-type-k q8_0 --cache-type-v q8_0 \
      --temp 1.0 --top-p 0.95 --top-k 64 \
      -ngl 99 \
      --port 11500
  ```
  `-ngl 99` puts all layers on Metal (Apple GPU). On M1 Air this is mandatory — CPU-only inference on a 2B model is 3–5× slower and will miss latency targets. `--ctx-size 131072` is the *server maximum*; each chat request must stay within a per-request context budget (≤ 8 192 tokens for real-time turns, ≤ 32 768 for consolidation tasks). The client controls this via `max_tokens` and message truncation — never by changing the server flag.
- **M1 Air 8 GB memory budget (shared CPU + GPU RAM):** model weights ~2.3 GB · KV cache at 8 k ctx ~0.3 GB · FastEmbed model ~130 MB · faster-whisper base.en ~150 MB (lazy) · Kokoro ~200 MB (lazy) · Python backend ~350 MB · macOS overhead ~1.5 GB = ~5 GB typical, leaving ~3 GB headroom. Do not load voice models before they are needed. If memory pressure is observed, reduce ChromaDB in-memory cache size.

---

## 5. Component architecture

Fourteen components in five groups. Each lists its responsibility and its key interface (the contract other components depend on). Boundaries are drawn so each can be built and tested with the others stubbed.

### Group A — Platform & runtime
1. **Desktop shell & setup** — Tauri window, frontend↔backend bridge, first-run wizard, model-download flow, settings. *Interface:* launches/monitors the backend; renders surfaces; exposes settings to the backend.
2. **Privacy & network guard** — an outbound socket kill-switch with a single allow-listed download host, plus the audit that powers the "Offline ✓" badge. *Interface:* a guard all outbound calls pass through; emits an event if anything is attempted.
3. **LLM runtime & client** — supervises `llama-server`; provides an async client with prompt assembly, token streaming, generation params (temp 1.0 / top-p 0.95 / top-k 64), thinking-mode off, and a context budget. *Interface:* `stream_chat(messages, mode) -> async token iterator`.

### Group B — Voice
4. **Speech-to-text** — faster-whisper (`base.en`, int8), push-to-talk capture, 120 s cap. *Interface:* `transcribe(audio) -> text`.
5. **Text-to-speech & sentence queue** — Kokoro (`af_heart`); a splitter that consumes the token stream, emits at sentence boundaries, synthesizes each sentence, and streams ordered audio chunks to the shell. *Interface:* `speak_stream(token_iter) -> async audio-chunk iterator`.

### Group C — Memory (the five layers + the loop)
6. **Vault & capture (L0 + L1)** — append-only full Markdown entries (truth) + the bounded per-entry extraction + the SQLite capture schema. **Foundational — build first.** *Interface:* `save_entry(text) -> entry_id`; `get_entry(id)`; `query_episodes(filters)`.
7. **Semantic index (L2)** — FastEmbed (`bge-small-en-v1.5`) + ChromaDB; embeds entry summaries and episodic units. *Interface:* `index(summary, meta)`; `recall(query, k) -> hits`.
8. **User model (L3)** — the evolving profile: claim schema, the operation grammar the model may emit, and the deterministic apply/decay/contradiction logic; structured `profile.json` + user-editable `profile.md`. **Hardest component.** *Interface:* `retrieve_slices(topic) -> profile fragments`; `apply_ops(ops)`.
9. **Derived analytics (L4)** — mood/emotion time-series, period-vs-period deltas, knowledge-graph builder. Pure computation. *Interface:* `mood_series(range)`; `period_delta(a, b)`; `build_graph()`.
10. **Consolidation pipeline** — the write loop: on-save, nightly, weekly reduce, rollup hierarchy (week→month→era), plus the scheduler. Orchestrates updates into L3/L4; code generates candidates, the model only narrates. *Interface:* `on_save(entry_id)`; `run_nightly()`; `run_weekly()`.

### Group D — Intelligence & interaction
11. **Conversation engine** — the read loop as a LangGraph state machine: intent classifier (vent / process / ask_info / ask_advice / ambient), persona system (close friend / coach / mentor), context assembler (recent L1 + relevant L3 + optional corpus), reason, check, persist. *Interface:* `handle_turn(input, persona) -> token+audio stream`.
12. **Corpus ingestion & grounded retrieval** — books/PDFs → chunk → embed (corpus collection); advice-mode retrieval with traceable citations and the "never invent a citation" rule. *Interface:* `ingest(file)`; `retrieve_passages(query) -> cited passages`.
13. **Guardrails & wellbeing** — light NeMo input/output rails (telemetry off, local engine), crisis-care handling, grounded-citation enforcement. Wraps the engine's `check` node. *Interface:* `check_in(text)`, `check_out(reply)`.

### Group E — Surfaces
14. **Frontend** — the chat/journaling surface (text + voice, Today panel) and the insights surface (mood charts, knowledge-graph viz, growth reports, time-travel browser). Consumes the backend API. Can split into two sub-surfaces.

---

## 6. Data architecture

The five-layer memory maps onto three physical stores, all inside the user's vault:

| Store | Holds | Layer | Role |
|---|---|---|---|
| **Markdown files** | Full daily entries + photos | L0 | Source of truth; self-sufficient without any DB |
| **SQLite (+FTS5)** | Episode records, entities, mood/metric time-series, digests, graph nodes/edges | L1, L4 | Analytical backbone; all deterministic counting lives here |
| **ChromaDB** | Entry-summary embeddings (journals collection); book chunks (corpus collection) | L2 | Semantic recall + clustering + edge candidates |
| **profile.json + profile.md** | Identity, goals, patterns, relationships, baseline, open loops, watch list | L3 | The evolving user model; `profile.md` is user-editable |

Design rules that the data layer enforces (from the Memory doc): **every L3 claim carries evidence pointers** to L1 entries (no pointer → not a claim); **summaries are embedded, full text is preserved**; L1–L4 are all **rebuildable from L0**; the user can read/edit/delete everything.

**ChromaDB collections are strictly separated:** `journals` (entry summaries, metadata: `{entry_id, date, mood, themes, is_seeded}`) and `corpus` (book chunks, metadata: `{source_file, page, section}`). Recall queries never touch the corpus collection; advice retrieval never touches the journals collection. The two collections use the same embedding model (`bge-small-en-v1.5`) but different distance thresholds calibrated separately.

**ChromaDB embedding model versioning:** if the embedding model is ever changed (e.g. `bge-small` → `bge-base`), existing vectors are incompatible. `vector.py` stores the model name in a config record inside the collection metadata. On startup it checks: if the stored model name does not match the current model, it raises a migration error and provides a `scripts/reindex.py` command to re-embed from scratch. Never silently mix vectors from different models.

**Recovery posture:** if SQLite or Chroma is corrupted, rebuild from L0 by re-running extraction and embedding. If `profile.json` is lost, rebuild incrementally from L1 (with the user's `profile.md` edits re-applied as anchors). L0 is the only irreplaceable store, so it is append-only and never rewritten.

**Vault portability:** the vault path is stored in settings as an absolute path. If the user moves the vault folder, they re-point it in Settings → Vault Location. All internal references use relative paths *within* the vault (e.g. `journal/2026-06-10.md`, not `~/EvaVault/journal/...`), so the vault itself is self-consistent regardless of where it lives.

---

## 7. Key runtime flows

### 7.1 Chat turn (the read loop) — text or voice
```
input → [STT if voice] → classify_intent
      → assemble context: recent L1 episodes
                         + relevant L3 slices (goals, patterns, open loops, people)
                         + corpus passages (ONLY if ask_advice)
      → guardrails.check_in
      → reason: stream tokens from llama-server
            ├─► UI renders text as it streams
            └─► sentence queue → Kokoro → ordered audio chunks → UI plays
      → guardrails.check_out
      → persist: full text → L0 ; extract → L1 ; embed summary → L2 ; queue on_save
```
The sentence queue is what makes voice feel live: Eva starts speaking after the first sentence, not after the whole reply.

### 7.2 Listen-first / opt-in advice
The intent classifier gates retrieval. In `vent`/`process` modes the corpus is never fetched, so the model **cannot** reach for advice — the discipline is structural, not a prompt plea. `ask_advice` is the only mode that pulls corpus passages and goal/value slices.

### 7.3 Grounded advice (feature 2)
On `ask_advice`, the engine retrieves the user's relevant goals/values from L3 and matching passages from the corpus. Citations are surfaced **only** from retrieved passages; the model never generates a religious or factual citation from memory. If nothing relevant is retrieved, Eva engages the user's own reasoning without inventing a source.

### 7.4 Consolidation (the write loop)
- **On save:** L0 append + one L1 extraction + L2 embed.
- **Nightly (today only):** update time-series (SQL); reconcile open loops (embedding match + tiny yes/no check); update L3 via bounded *operations* with evidence pointers — never a rewrite.
- **Weekly (reduce):** deterministic pattern mining (theme/emotion counts, open-loop recurrence, **behavior-vs-goal contradictions**) → cluster → model narrates top candidates → reconcile L3 (merge, decay, resolve contradictions) → rebuild graph → roll up digests.
- **Long horizons:** week→month→era digests; no model call ever sees more than one bounded window.

### 7.5 Insights on demand (features 5, 7, 8, 9)
These read L4/L0 with no heavy model reasoning: mood charts and growth deltas are SQL aggregations the model only narrates *descriptively*; the graph is built from co-occurrence/precedence/similarity (deterministic) plus a few evidence-gated, clearly-labeled hypothesis edges; time-travel retrieves L0 by date range and juxtaposes a raw past entry against the current L3.

---

## 8. Concurrency & scheduling

- The backend is async (asyncio). The **real-time chat path has priority**; it must never block on a background job.
- **Background jobs** (nightly/weekly consolidation) run on APScheduler when the app is idle / at night. They share the single `llama-server`, so model access is serialized through a single `asyncio.Lock`.
- **Concurrency mechanism (concrete):**
  ```python
  # backend/llm/client.py
  _model_lock = asyncio.Lock()

  async def stream_chat(messages, priority=False):
      """Acquire the model lock before calling llama-server.
      priority=True is used by the real-time chat path.
      Background jobs call with priority=False and check the flag
      before acquiring; if a chat turn is in progress they defer
      by sleeping and retrying."""
      if not priority:
          # Yield to any waiting chat turns before taking the lock
          await asyncio.sleep(0)
      async with _model_lock:
          # ... stream tokens from llama-server
  ```
  The scheduler calls `run_nightly()` / `run_weekly()` only when `/chat` is idle (no active WebSocket session). APScheduler fires the job; if a chat turn starts mid-job the job's next model call blocks on the lock until the chat turn finishes — it does not cancel the job.
- This split — fast E2B chat vs. slower, heavier background reflection — is the core concurrency idea that makes the ambitious analytics features feasible on a small model: the hard work happens where latency doesn't matter.

---

## 9. Cross-cutting concerns

**Privacy & security.** All outbound traffic passes through the network guard; only the first-run model download host is allow-listed. NeMo telemetry is disabled and verified by a packet-capture audit. The vault lives in plain user-owned files; there are no accounts, keys, or servers. The L3 profile is the most sensitive artifact and is treated as such — local, readable, deletable.

**Safety & wellbeing.** Light guardrails keep voice responsive while blocking out-of-scope topics and enforcing grounded citations. A crisis-care path detects signals of self-harm/abuse and responds with care plus a gentle nudge toward a real person or professional support — never clinical, never method detail. Growth analytics are framed as reflection, never a verdict on the user's character.

**Performance / latency budgets.** Primary target: MacBook M1 Air 8 GB with `-ngl 99` (Metal). STT < ~1.5 s after speech release (faster-whisper base.en on Metal); LLM TTFT ~0.3–0.8 s; perceived first-speech (first sentence synthesized) ~1.5–2.5 s; end-to-end turn ~3–5 s. On CPU-only machines (no Metal) all figures roughly double — warn the user during setup if Metal is unavailable. Limits: voice input ≤ 120 s/recording; text input ≤ ~8 000 chars; reply ~450 tokens default, ~800 in coach/mentor, hard cap 1 000. Per-request context budget: real-time chat turns cap the messages passed to llama-server at 8 192 tokens total (system + history + context); consolidation tasks may use up to 32 768 tokens but run off the real-time path.

**Observability.** Local-only structured logs (no remote). A debug panel can show the assembled context, the chosen intent, retrieved evidence pointers, and applied L3 operations — making the system auditable to the user, which is also the anti-hallucination story.

**Failure modes & degradation.** Model not downloaded → wizard shows the command, no crash. llama-server crash → backend restarts it; the turn fails gracefully. STT/TTS failure → fall back to text. Chroma/SQLite corruption → rebuild from L0. Guardrail engine failure → fail closed (no advice, plain acknowledgment).

**Configuration.** A single settings store (vault path, voice + speed, persona default, model path, whisper size). Surfaced in the UI; read by the backend.

**Upgrade / migration.** L0 Markdown is the stable contract across versions. Schema migrations touch only L1–L4, which are rebuildable, so upgrades can re-derive rather than risk in-place migration of irreplaceable data.

---

## 10. Key design decisions & trade-offs

1. **Intelligence in the pipeline, not the weights.** Code counts, connects, and remembers; the model extracts single entries and narrates over pre-counted evidence. *Trade-off:* more engineering up front; in return, features 4/6/9 become reliable on a 2B model instead of hallucination-prone.
2. **`llama-server` over Ollama.** OpenAI-compatible endpoint, explicit param/thinking control, clean NeMo integration, no audio-crash issues. *Trade-off:* we manage the binary lifecycle ourselves.
3. **Three processes, one backend (not microservices).** A single Python backend orchestrates everything; only the UI (Rust) and model server (native) are separate. *Trade-off:* less isolation between subsystems, but far simpler to build, package, and reason about for a solo, single-user, local app.
4. **Full entries kept + summaries embedded.** L0 preserves everything (time-travel, recovery); L2 embeds only summaries (sharp recall, small index). *Trade-off:* a little storage duplication for a lot of robustness and recall quality.
5. **Incremental L3 operations, never rewrites.** Bounded, auditable, reversible updates with evidence pointers. *Trade-off:* a richer apply/decay/contradiction engine to design — accepted, because it's the moat.
6. **Background reflection split from real-time chat.** Heavy analysis runs at night. *Trade-off:* insights are not instantaneous after a single entry — acceptable, since they're inherently long-horizon.
7. **Push-to-talk, not always-on.** Simpler, more private, no false triggers. *Trade-off:* a deliberate user action per turn — acceptable for a journaling ritual.
8. **English-only v1 (Kokoro voice limit).** *Trade-off:* defers other languages; embedding model is swappable when that changes.

---

## 11. Backend API surface (frontend ↔ backend)

Localhost HTTP for request/response; WebSocket for streaming. Indicative:

| Method / channel | Purpose |
|---|---|
| `GET /health` | Liveness + setup state (model present?) |
| `WS /chat` | Send a turn (text or transcribed); receive streamed tokens, audio chunks, state, intent |
| `POST /stt` | Audio → text (when not streamed over `/chat`) |
| `GET /journal?date=` / `GET /entries?from=&to=` | L0 retrieval (today panel, time-travel) |
| `GET /insights/mood?from=&to=` | L4 mood/emotion series |
| `GET /insights/graph` | L4 knowledge graph (nodes + edges, edges typed/labeled) |
| `GET /insights/growth?a=&b=` | L4 period comparison (descriptive) |
| `GET /profile` / `PUT /profile` | Read / edit L3 (`profile.md`) — user corrections become anchors |
| `POST /corpus/upload` / `GET /corpus` | Add / list books |
| `POST /persona` | Switch active persona |
| `GET /settings` / `PUT /settings` | Configuration |
| `POST /privacy/audit` | Run the offline network audit |

Background jobs (`run_nightly`, `run_weekly`) are scheduler-driven, not user-facing endpoints (a manual "consolidate now" trigger can exist for testing).

---

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| 2B model hallucinates patterns / over-reaches | Code-counts-model-narrates; evidence pointers; verification pass; human-in-the-loop corrections |
| Background analysis too weak on E2B | Heavy jobs run offline where multiple passes are affordable; can route nightly/weekly to a larger model later without touching the real-time path |
| Voice latency feels robotic/slow | Sentence-queue streaming; thinking-mode off; warn on CPU-only machines |
| Misquoted religious/factual citation (real harm) | Cite only retrieved corpus passages; never generate citations from memory |
| Growth analytics read as a harmful verdict | Descriptive framing only; user is the interpreter |
| Privacy promise broken by a hidden call | Network guard + telemetry off + packet-capture audit in the UI |
| Irreplaceable data loss | L0 append-only and self-sufficient; L1–L4 rebuildable from it |

---

## 13. Build order (reference)

`1 Shell → 2 Network guard → 3 LLM runtime` (offline chat) → `6 Vault & capture → 7 Semantic index` (memory base) → `11 Conversation engine` (companion comes alive) → `4 STT → 5 TTS+queue` (voice) → `8 User model → 9 Analytics → 10 Consolidation` (evolving intelligence) → `12 Corpus → 13 Guardrails` → `14 Frontend` (built incrementally throughout).

---

*Living document. The process model, component boundaries, and data rules are the stable contract; endpoint shapes and schemas are refined as components are built. Pairs with the Memory Architecture and Build Plan docs.*
