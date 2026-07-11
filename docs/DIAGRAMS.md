# Eva — System Design & Harness (Mermaid)

Copy-pasteable Mermaid diagrams of Eva's architecture, grounded in the code
(`backend/engine/turn.py`, `backend/app.py` routes, `backend/memory/consolidate.py`,
`backend/llm/providers.py`) and `docs/EVA_SYSTEM_DESIGN.md`.

---

## 1. Process & deployment architecture (3 processes, one machine)

```mermaid
flowchart TB
    subgraph MACHINE["USER'S MACHINE — local-first (provider network only in opt-in online mode)"]
        subgraph P1["PROCESS 1 · Tauri shell (Rust + React/Vite)"]
            UI["Chat / Journal / Insights / Library<br/>Settings · First-run wizard<br/>Mic (push-to-talk) · Provider & network status"]
        end

        subgraph P2["PROCESS 2 · Backend (Python 3.11 · FastAPI · asyncio)"]
            ENG["Conversation engine (read loop)"]
            CONS["Consolidation pipeline (write loop)"]
            SCHED["APScheduler (nightly / weekly)"]
            GUARD["Net guard + light guardrails"]
            VOICE["Voice workers (in-process, lazy)<br/>STT faster-whisper · TTS Kokoro"]
        end

        subgraph P3["PROCESS 3 · llama-server (sidecar)"]
            LLM["Gemma 4 E2B QAT GGUF<br/>OpenAI-compatible :11500"]
        end

        subgraph VAULT["THE VAULT — plain user-owned files (~/EvaVault)"]
            L0["Markdown (L0): full entries + photos — source of truth"]
            SQL["SQLite +FTS5 (L1/L4): episodes, time-series, graph, digests"]
            CHROMA["ChromaDB (L2): journal summaries + corpus embeddings"]
            PROF["profile.json + profile.md (L3): evolving user model"]
            CORP["corpus/: user-uploaded books"]
        end
    end

    NET(["Internet — blocked in local mode; provider host only in online mode"])

    UI <-->|"localhost HTTP + WS /chat<br/>tokens · audio · state · intent"| ENG
    UI -->|"mic audio"| VOICE
    ENG -->|"stream_chat (asyncio.Lock)"| LLM
    CONS -->|"bounded narration"| LLM
    SCHED --> CONS
    ENG <--> VAULT
    CONS <--> VAULT
    GUARD -. "intercepts all outbound sockets" .-> NET
    LLM -.->|"online mode only"| NET
```

---

## 2. Component architecture (14 components, 5 groups)

```mermaid
flowchart LR
    subgraph A["A · Platform & runtime"]
        A1["1 Desktop shell & setup"]
        A2["2 Privacy & network guard"]
        A3["3 LLM runtime & client"]
    end
    subgraph B["B · Voice"]
        B4["4 Speech-to-text (faster-whisper)"]
        B5["5 TTS + sentence queue (Kokoro)"]
    end
    subgraph C["C · Memory (5 layers + loop)"]
        C6["6 Vault & capture (L0+L1)"]
        C7["7 Semantic index (L2)"]
        C8["8 User model (L3)"]
        C9["9 Derived analytics (L4)"]
        C10["10 Consolidation pipeline"]
    end
    subgraph D["D · Intelligence & interaction"]
        D11["11 Conversation engine"]
        D12["12 Corpus ingest + grounded retrieval"]
        D13["13 Guardrails & wellbeing"]
    end
    subgraph E["E · Surfaces"]
        E14["14 Frontend (chat + insights)"]
    end

    E14 --> D11
    D11 --> A3 & C7 & C8 & C6 & D12 & D13
    D11 --> B4 & B5
    B5 --> A3
    A3 --> A2
    D12 --> C7
    C10 --> C8 & C9 & C7 & C6
    C6 --> C7
    C9 --> C6
```

---

## 3. Data architecture — 5 memory layers → 3 physical stores

```mermaid
flowchart TB
    subgraph LAYERS["Five logical memory layers"]
        L0["L0 · Full daily entries + photos"]
        L1["L1 · Episode records / extraction"]
        L2["L2 · Semantic summaries + corpus chunks"]
        L3["L3 · Evolving user model (claims + evidence)"]
        L4["L4 · Mood series · deltas · knowledge graph"]
    end
    subgraph STORES["Three physical stores (inside the vault)"]
        MD["Markdown files"]
        SQ["SQLite +FTS5"]
        CH["ChromaDB<br/>journals + corpus collections"]
        PJ["profile.json + profile.md"]
    end

    L0 --> MD
    L1 --> SQ
    L4 --> SQ
    L2 --> CH
    L3 --> PJ

    MD -->|"rebuildable"| SQ
    MD -->|"rebuildable"| CH
    MD -->|"rebuildable"| PJ
    classDef truth fill:#1f6f43,color:#fff,stroke:#0d3;
    class MD truth
```

> Rule: every L3 claim carries an evidence pointer to an L1 entry; L1–L4 are all
> rebuildable from L0 (the only irreplaceable store).

---

## 4. Chat turn — the read loop (`engine/turn.py`, 6 steps)

```mermaid
sequenceDiagram
    participant U as UI (WS /chat)
    participant E as Engine (TurnState)
    participant I as Intent classifier
    participant R as Retrieval (L1/L2/L3/corpus)
    participant G as Guardrails
    participant M as llama-server
    participant Q as Sentence queue + Kokoro
    participant V as Vault (L0/L1/L2)

    U->>E: text (or STT'd audio) + mode + history
    E->>I: 1 classify() → intent
    Note over E,R: 2 assemble_context() — concurrent
    par independent slots
        E->>R: recent L1 episodes
        E->>R: recall memories (L2)
        E->>R: profile slices (L3, advice-aware)
    end
    alt intent.retrieves (ask_info / ask_advice)
        E->>R: retrieve_corpus → cited passages
    else vent / process / ambient
        Note over E,R: corpus BYPASSED (listen-first, structural)
    end
    E->>G: 3 check_in() — crisis scan → build system prompt
    E->>M: 4 reason() — stream tokens
    loop each token
        M-->>E: piece
        E-->>U: token frame
        E-->>Q: on_token → ordered audio chunks
        Q-->>U: audio
    end
    E->>G: 5 check_out() — drop any unbacked citation
    E->>V: 6 persist() → L0 append · L1 extract · L2 embed · queue on_save
    E-->>U: meta frame (intent, persona, retrieved counts)
```

---

## 5. Listen-first intent gate (why the model *can't* over-advise)

```mermaid
flowchart TD
    IN["User turn"] --> CL{"classify_intent"}
    CL -->|vent| NG["No corpus fetched"]
    CL -->|process| NG
    CL -->|ambient| NG
    CL -->|ask_info| RG["Retrieve corpus + goals/values"]
    CL -->|ask_advice| RG
    NG --> CTX["Context window:<br/>recent L1 + recall L2 + profile L3"]
    RG --> CTX2["Context window + cited passages"]
    CTX --> GEN["Model narrates — no advice reachable"]
    CTX2 --> GEN2["Model advises — citations only from retrieved passages"]
```

---

## 6. Consolidation — the write loop (`memory/consolidate.py`)

```mermaid
flowchart TB
    SAVE(["Entry saved"]) --> ONS["on_save(entry_id)<br/>L0 append · 1 L1 extraction · L2 embed"]

    subgraph SCHEDULER["APScheduler — only when /chat is idle"]
        NIGHT["run_nightly() · today only<br/>update time-series (SQL)<br/>reconcile open loops (embed match + tiny yes/no)<br/>apply bounded L3 operations (evidence pointers, never rewrite)"]
        WEEK["run_weekly() · reduce<br/>deterministic pattern mining → cluster<br/>model narrates top candidates<br/>reconcile L3 (merge/decay/contradictions)<br/>rebuild graph · roll up digests"]
    end

    ONS --> NIGHT --> WEEK
    WEEK --> ROLL["Rollups: week → month → era digests<br/>(no model call sees > one bounded window)"]
    NIGHT -.->|"serialized via asyncio.Lock,<br/>priority=False yields to chat"| LLMX["llama-server"]
    WEEK -.-> LLMX
```

---

## 7. Provider mode + network guard (privacy hard law)

```mermaid
stateDiagram-v2
    [*] --> FirstRun
    FirstRun --> Local: choose local AI
    FirstRun --> Online: choose online API (opt-in)

    state Local {
        [*] --> Serving
        Serving: llama-server :11500
        note right of Serving
            Outbound blocked except
            loopback + EVA_ALLOW_HOST
            (first-run download only)
        end note
    }
    state Online {
        [*] --> ProviderCall
        ProviderCall: selected provider host only
        note right of ProviderCall
            net_guard permits only the
            configured provider host;
            everything else raises
            OutboundBlocked
        end note
    }
    Local --> Online: switch in Settings
    Online --> Local: switch in Settings
```

---

## 8. Backend API surface (frontend ↔ backend)

```mermaid
flowchart LR
    subgraph FE["Frontend surfaces"]
        chat["Chat"]:::s
        jrnl["Journal"]:::s
        ins["Insights"]:::s
        lib["Library"]:::s
        prof["Profile"]:::s
        set["Settings"]:::s
    end
    subgraph API["FastAPI (127.0.0.1:8000)"]
        H["GET /health"]
        WS["WS /chat  (tokens·audio·meta)"]
        STT["POST /stt"]
        J["GET /journal · /journal/day · /journal/entries"]
        E["POST /entry · POST /journal · PUT /entries/{uid}"]
        P["GET/PUT /profile · /profile/evidence/{uid}"]
        IM["GET /insights/mood · /graph · /growth"]
        CO["POST /corpus/upload · GET /corpus"]
        AI["GET /ai/providers · /ai/config · POST /ai/test"]
        SE["GET/PUT /settings"]
        PR["GET /privacy/audit"]
        CN["POST /consolidate (test trigger)"]
    end

    chat --> WS & STT
    jrnl --> J & E
    ins --> IM
    lib --> CO
    prof --> P
    set --> AI & SE & PR
    classDef s fill:#2b3a55,color:#fff;
```

---

## 9. Test / dev harness

```mermaid
flowchart TB
    subgraph DEV["Dev entrypoints"]
        DS["dev.sh / dev.ps1 — backend venv + frontend/Tauri"]
        RD["run_demo.sh --reset — demo-mode launch"]
    end
    subgraph SEED["Demo & seed scripts"]
        DR["demo_reset.py — ~3wk mood + graph + L3 + book"]
        DD["demo_drills.py — failure-mode PASS/FAIL"]
        SJ["seed_john.py / seed_yusuf.py"]
    end
    subgraph PYTEST["pytest suite (backend/tests)"]
        direction LR
        T1["engine/turn · assembly · intent · chat_rag · chat_surface"]
        T2["memory: capture · retrieval · profile · profile_ops · consolidate · db_schema · vault · reindex · reextract"]
        T3["insights: mood · graph · growth"]
        T4["voice: stt · tts · sentence_queue"]
        T5["safety/privacy: crisis_check · net_guard · privacy_audit · failure_drills"]
        T6["platform: health · settings · llm · llm_providers · scheduler"]
    end
    subgraph MANUAL["Manual checks"]
        CNG["check_net_guard.py — loopback ok, web blocked"]
        VR["verify_rag.py · verify_stepB_e2e.py · ws_test.py"]
        SPIKE["packaging/spike/build_spike.sh — sidecar freeze proof"]
    end

    DS --> PYTEST
    RD --> DR --> DD
    DD --> T5
    PYTEST -.mirrors.-> MANUAL
```
