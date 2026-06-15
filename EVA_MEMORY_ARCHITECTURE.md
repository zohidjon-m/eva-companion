# Eva — Memory Architecture

**The five-layer memory that makes a 2B local model behave like it understands you**
*Design doc v1 · 2026-06-09 · companion: Eva · model: Gemma 4 E2B (QAT GGUF), fully on-device*

---

## 0. The governing principle (read this first)

Everything in this document follows from one fact about the model we're using:

> **A 2B model is a bad historian but a good clerk.** Ask it to "understand six months of my life and tell me my patterns" and it will confabulate — too much context, too much open-ended reasoning. Ask it to "read this one entry and fill these fields," or "here are ten facts I already counted, write three sentences," and it is reliable.

So the entire design goal is: **never make the model remember, count, or connect across your history. Make code do that. The model only (a) extracts one entry at a time and (b) narrates over evidence that code has already assembled.** The intelligence of Eva lives in the data structures and the pipeline — not in the weights.

If we hold that line, all nine features are achievable on E2B. Every time something feels impossible for the model, the fix is the same: move the hard part into deterministic code and leave the model a small, bounded, well-fed job.

---

## 1. The five layers

Memory is organized as five layers, from raw truth at the bottom to computed views at the top. Lower layers are the source of truth; higher layers are *derived* and can always be rebuilt from below.

### L0 — Raw vault (the truth)
The complete, unedited daily journal entries, in plain Markdown, plus any attached photos. Append-only. **Never rewritten, never summarized in place, never dependent on a database to be readable.** This is the stable storage contract: a future tool, a backup script, even `grep`, can read it. If every database below is deleted, L0 still holds the user's whole life in plain text. L0 alone delivers literal recall (feature 8) and the "look how naive I was" moment (just show the old entry).

### L1 — Episode records (the atoms)
For every entry, one bounded extraction call turns the prose into a tight structured record, stored in SQLite. This is the only place the model touches raw life, and it's a small job done well. Fields (keep the schema tight — a small model degrades as the schema grows):
- **mood** (scalar, e.g. −5..+5) and **discrete emotions** (a small controlled set: anger, shame, joy, anxiety, calm, …) with intensity
- **entities** — people, places, projects, normalized and linked across entries
- **themes / topics**
- **events** — what actually happened (short)
- **stated goals / values** — who the user says they want to be (carries provenance: which entry asserted it)
- **behaviors** — what the user actually did (kept distinct from goals — this distinction is the engine of feature 6)
- **decisions / intentions**
- **open loops** — unresolved feelings/threads (the lingering-argument example); a first-class object with a status (open / updated / resolved) and a timeline
- **self-judgments / regrets** — signals for mistake detection

### L2 — Semantic index (recall)
Embeddings (local, FastEmbed) of entry summaries and of individual episodic units (open loops, notable moments), in ChromaDB. Powers associative recall ("moments that feel like this one"), clustering for pattern mining, and candidate-edge generation for the graph.

### L3 — The User Model (the evolving self)
The persistent model of *who the user is* — the moat, and the heart of feature 4. Contains:
- **identity & aspirations** — the person they want to become (e.g., "a good, masculine Muslim man"), stated principles and values, with provenance
- **goals** — active, with the entries that asserted them and their current status
- **recurring patterns** — each a named claim with **pointers to the entries that evidence it** and a confidence score
- **relationships** — key people and the state of each relationship over time
- **emotional baseline** — typical moods, known triggers, what helps
- **live open loops** — currently unresolved threads
- **watch list** — candidate recurring mistakes tied to goals (the feature-6 precursor)

Stored two ways: a structured part (SQLite/JSON) the machine uses, and a human-readable `profile.md` narrative the **user can open and edit**. L3 is updated *incrementally* — never wholesale regenerated.

### L4 — Derived analytics (computed views, not truth)
Recomputed from L1/L3 by code, on demand: mood/emotion time-series (feature 5), period-vs-period deltas (feature 9), and the knowledge graph (feature 7). Nothing here is a source of truth — it's a lens over the layers below, always rebuildable.

---

## 2. The two loops over the layers

The same five layers are traversed by two processes running at very different speeds.

**Read loop — chat time, fast, E2B.** Every turn, code assembles the prompt from three cheap lookups: the last *N* days of L1 episodes (the "what's happening lately" baseline), the slices of L3 relevant to the current topic (active goals, relevant patterns, open loops, the people involved — *retrieved, not the whole profile*), and — only in advice mode — grounded corpus passages. Eva sounds like she understands the user not because the model is clever, but because it's been *handed* the consolidated understanding and only has to talk.

**Write loop — background, slow.** Builds the upper layers from the lower ones on three cadences (Section 3). The rule never changes: code generates candidates and does all counting; the model only narrates over what code assembled.

---

## 3. The consolidation pipeline

### On save (per turn)
Append full text to L0 → one bounded extraction call to L1 → embed the summary into L2. One small model call total.

### Nightly (today's data only)
1. **Update time-series** — append today's mood/emotion/metrics to SQLite. *No model.*
2. **Reconcile open loops** — embedding-match today's content against currently-open loops, then a tiny yes/no model check ("same unresolved thing?") to mark resolved / updated / new.
3. **Update L3 — never a rewrite.** Code retrieves only the few L3 sections today's facts touch; the model emits a small list of **operations** (`add pattern`, `strengthen claim X`, `note contradiction`, …), each carrying an **evidence pointer** to the justifying entry. Code applies them. Bounded input, bounded output, fully auditable.

### Weekly (the reduce step — where features 6 and 9 are computed)
1. **Deterministic pattern mining first** — over the window, code counts theme frequencies, emotion co-occurrences, open-loop recurrence, and (most important) **behavior-vs-goal contradictions**: a recurring behavior that runs against a stated goal. Output: ranked candidate patterns *with evidence counts*.
2. **Cluster** similar episodes (embeddings) to surface recurring situations.
3. **Narrate** — only now does the model describe the top-few candidates in the user's own terms, strictly from the provided evidence.
4. **Reconcile L3** — merge new patterns, decay stale claims, resolve contradictions. Incremental, section by section.
5. **Rebuild** the knowledge graph and roll up digests.

### Long horizons (6 months, years) — the rollup hierarchy
Never feed the model a long span. Entries roll up into **week digests → month digests → "era" digests**, each a map-reduce of the level below. Raw text stays in L0 forever for time-travel, but the *working memory* for any analysis is always one bounded window. This is what makes features 6, 8, and 9 computable on a small model at all.

---

## 4. How the nine features map onto the memory

| # | Feature | Delivered by |
|---|---|---|
| 1 | Daily venting companion (listen first) | Read loop primes L1 recent + L3 emotional baseline; intent classifier keeps it in "listen" mode — no advice unless asked |
| 2 | Context-aware advice (not generic) | Read loop pulls relevant L3 (goals, values, identity) + grounded corpus passages; advice is tailored to who the user wants to become |
| 3 | Advice opt-in (pulled, not pushed) | Intent mode gate — advice retrieval only fires on `ask_advice`; venting closes without a lecture |
| 4 | Evolving memory | L3 + the nightly/weekly incremental update loop; conversations improve because the read loop keeps pulling a deeper L3 |
| 5 | Mood / emotion tracking | L1 fields → L4 time-series; pure SQL aggregation |
| 6 | Mistake / pattern detection tied to goals | Behavior-vs-goal contradiction miner (deterministic candidates) + weekly narration; goals carry provenance from L1 |
| 7 | Knowledge-graph visualization | Nodes from L1 entities/themes/problems/goals; edges from co-occurrence + temporal precedence + embedding similarity (code), plus a small, evidence-gated set of model-proposed "leads-to" edges, labeled as hypotheses |
| 8 | Retrospective recall ("time travel") | Retrieve L0 by date range; "how naive I was" = raw past entry shown beside current L3; photos live in L0 |
| 9 | Comparative growth analytics | L4 period comparison: code computes deltas (mood, theme mix, open-loop resolution rate, goal-aligned vs goal-contradicting behavior counts); model narrates *descriptively* |

---

## 5. What must be considered most

These are the make-or-break constraints. Get these right and features 1–9 work; ignore any one and the system either hallucinates, slows to a crawl, or quietly harms the user.

### 5.1 Evidence pointers on every L3 claim — *no pointer, no claim*
Every statement in the user model must link to the L1 entries that justify it. This is the single strongest anti-hallucination rule: if the model wants to assert a pattern but can't cite entries, the assertion is rejected by code. It also powers verification, time-travel, and lets the user see *why* Eva believes something about them.

### 5.2 Code counts; the model narrates
Frequencies, trends, period deltas, contradiction detection — all deterministic SQL/Python. The model is never asked to do arithmetic over history or to "notice" a pattern unprompted. It receives counted candidates and writes prose. This is the line that keeps feature 6 and 9 honest.

### 5.3 Incremental operations, not rewrites
L3 is updated by a small, fixed set of operations with evidence attached — never by regenerating the whole profile. This keeps every model call's context bounded and every change auditable and reversible.

### 5.4 Confidence and decay
Each L3 claim carries a strength that **rises with corroboration and fades without it**. A one-off remark never becomes a "fact"; a genuine pattern earns its place over weeks. Decay is what keeps the model of the user *current* instead of accumulating stale beliefs.

### 5.5 Bounded windows / rollup always
No model call ever sees more than one window of data. Long horizons are handled by the digest hierarchy, not by stuffing context. Design every analysis to operate on a digest, not on raw history.

### 5.6 Tight extraction schemas
Extraction quality on a small model is inversely proportional to schema size. Keep L1 fields few and well-exampled (few-shot). Add fields only when a feature truly needs them, and capture them from **day one** — you cannot retroactively extract structure you never stored (this is what gates features 6, 8, 9 from working months later).

### 5.7 Verification pass on important claims
Before a high-impact claim (a named recurring mistake, a growth verdict) reaches the user, run a cheap second check: "is this supported by its cited evidence? yes/no." Cheap insurance against the model over-reaching.

### 5.8 Human-in-the-loop correction as the highest-confidence anchor
The user can open `profile.md` and fix anything Eva got wrong; that correction becomes an anchor the model is not allowed to overwrite. On a small model, letting the user correct the self-model is worth more than any prompt engineering — it turns user effort directly into accuracy.

### 5.9 Listen-first discipline lives in retrieval, not just the prompt
Feature 1 and 3 depend on advice being *pulled*. The intent classifier gates retrieval: in vent/process modes, the corpus is never fetched, so the model literally cannot reach for advice. The discipline is enforced by what's in the context window, not by hoping the prompt holds.

### 5.10 Grounded citations only — never generate them
For feature 2's worked example (religious teachings, hadith), Eva may only cite passages **actually retrieved from texts the user uploaded**. The model must never produce a religious or factual citation from its own memory — a misquoted or misattributed teaching is a real harm, not a glitch. If nothing relevant is retrieved, Eva engages the user's own thinking without inventing a source.

### 5.11 Growth analytics are descriptive, never a verdict
Feature 9 must report *what changed* in what the user wrote and felt, with the user as interpreter — not deliver a judgment on whether they became a worse person. A small model grading someone's character is both unreliable and harmful. Frame as reflection ("here's a shift, and a pattern — what do you make of it?"), keep the user in control.

### 5.12 Privacy is structural, not a setting
L3 is the most sensitive artifact the app will ever hold — effectively a psychological and spiritual profile of the user. It stays fully local, in plain user-owned files, fully readable and deletable by the user. The all-local architecture isn't a feature here; it's the only ethical way to hold this data.

### 5.13 The graph shows association, not proven causation
Feature 7's "this problem leads to that behavior" edges are the hardest to get right. Build the graph from co-occurrence, temporal precedence, and similarity first (all deterministic); allow only a small set of model-proposed causal edges, gated by evidence and **labeled as hypotheses the user can confirm or reject**. Never present a guessed causal link as established fact.

---

## 6. The hardest single component (next to design)

The piece that most needs careful, explicit design is the **L3 update algorithm**:
- the data structure of an L3 claim (fields: statement, type, evidence pointers, confidence, last-seen, source = model vs user),
- the exact **operation grammar** the model is allowed to emit (`add` / `strengthen` / `weaken` / `note-contradiction` / `mark-resolved` / `link-evidence`), and
- the deterministic **apply + decay + contradiction-resolution** logic that turns those operations into a coherent, current profile without ever letting the model rewrite the whole thing.

That, plus the concrete SQLite schema for L0/L1 (so capture is locked before reasoning is built on top of it), is the right next design step — and both are now specified in Section 7 below.

---

*This is a living document. Section 7 schemas are locked contracts; all other sections remain refinable as building progresses.*

---

## 7. Concrete schemas (locked before Phase 2 starts)

> These are source-of-truth contracts. Every component that reads or writes storage must implement these exactly. Changing any schema requires bumping the schema version and writing a migration.

### 7.1 L1 SQLite schema (`schema.sql`)

```sql
-- schema.sql — applied once on first launch via db.py
-- Increment PRAGMA user_version on every schema change; add a migration block in db.py.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
-- PRAGMA user_version = 1;

-- ─────────────────────────────────────────────────────────────────────────────
-- L0 index (truth lives in Markdown files; this table is the queryable index)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS entries (
    id          TEXT PRIMARY KEY,                        -- UUID v4
    date        TEXT NOT NULL,                           -- YYYY-MM-DD
    type        TEXT NOT NULL CHECK(type IN ('chat','journal')),
    text        TEXT NOT NULL,                           -- full turn/entry text
    word_count  INTEGER,
    is_seeded   INTEGER NOT NULL DEFAULT 0,              -- 1 = demo seed data; exclude from recall
    created_at  TEXT NOT NULL                            -- ISO-8601
);

-- Full-text search over raw entry text
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    text,
    content='entries',
    content_rowid='rowid'
);

-- ─────────────────────────────────────────────────────────────────────────────
-- L1 extractions — one row per entry; ALL fields the model must pull out
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS extractions (
    id                  TEXT PRIMARY KEY,
    entry_id            TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    extraction_status   TEXT NOT NULL DEFAULT 'pending'
                        CHECK(extraction_status IN ('pending','done','failed','null_stored')),
    -- Mood scalar and emotion array
    mood                INTEGER,           -- -5..+5; NULL if extraction failed
    emotions            TEXT,              -- JSON: [{name, intensity: 0..1}]
    -- Structured facts (all JSON; NULL until extraction succeeds)
    entities            TEXT,              -- JSON: [{name, type: person|place|project, normalized}]
    themes              TEXT,              -- JSON: [string]
    events              TEXT,              -- JSON: [string]   — what actually happened
    stated_goals        TEXT,              -- JSON: [{text, is_new: bool}]
    behaviors           TEXT,              -- JSON: [string]   — what user actually did (distinct from goals)
    decisions           TEXT,              -- JSON: [string]
    open_loops          TEXT,              -- JSON: [{description, status: open|updated|resolved}]
    self_judgments      TEXT,              -- JSON: [string]   — regrets, self-criticism signals
    -- Summary for ChromaDB embedding (embedded at the same time extraction runs)
    summary             TEXT,             -- 4-5 sentences; NULL until status = done
    extracted_at        TEXT              -- ISO-8601; NULL until status = done
);

-- ─────────────────────────────────────────────────────────────────────────────
-- L4 mood time-series (denormalized for fast chart queries; no LLM needed)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mood_series (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    mood        INTEGER,                   -- copied from extractions.mood
    emotions    TEXT,                      -- JSON copy from extractions.emotions
    is_seeded   INTEGER NOT NULL DEFAULT 0
);

-- ─────────────────────────────────────────────────────────────────────────────
-- L4 knowledge graph
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    type        TEXT NOT NULL CHECK(type IN ('theme','person','place','goal','problem','emotion')),
    entry_count INTEGER NOT NULL DEFAULT 0,
    entries     TEXT                       -- JSON: [entry_id]
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL REFERENCES graph_nodes(id),
    target       TEXT NOT NULL REFERENCES graph_nodes(id),
    type         TEXT NOT NULL CHECK(type IN ('co_occurrence','temporal','similarity','hypothesis')),
    weight       REAL NOT NULL DEFAULT 0.0,
    is_hypothesis INTEGER NOT NULL DEFAULT 0,   -- 1 = model-proposed; shown with confirm/dismiss UI
    label        TEXT,                           -- human-readable edge label (e.g. "may lead to")
    entries      TEXT                            -- JSON: [entry_id]
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Rollup digests (week → month → era)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS digests (
    id           TEXT PRIMARY KEY,
    level        TEXT NOT NULL CHECK(level IN ('week','month','era')),
    period_start TEXT NOT NULL,            -- ISO date
    period_end   TEXT NOT NULL,
    summary      TEXT,                     -- model-narrated prose digest
    stats        TEXT,                     -- JSON: {entry_count, avg_mood, top_themes, ...}
    created_at   TEXT NOT NULL
);
```

**Extraction retry contract:** On first JSON parse failure, retry once with `temperature=0.3` and a stricter prompt (all examples, no prose). If the second attempt also fails, write `extraction_status='null_stored'` and store NULLs — never block the save. A background sweep re-queues `null_stored` rows nightly. The mood chart skips NULL points (shows a gap) — never substitutes zero.

**Migration:** Every schema change increments `PRAGMA user_version`. `db.py` reads the version at startup and applies pending migration blocks in order. Migrations touch only L1–L4; L0 Markdown is never touched by a migration.

**ChromaDB collections:** Two separate collections, same embedding model (`bge-small-en-v1.5`):
- `journals` — entry summaries with metadata `{entry_id, date, mood, themes, is_seeded}`. Populated at the same time as extraction, not as a separate phase. Filter `is_seeded=False` on recall queries.
- `corpus` — book/PDF chunks with metadata `{source_file, page, section}`. Never mixed with journal recall.

---

### 7.2 L3 claim schema (`profile.json`)

The full structure. All writes go through the operation grammar; the model never writes to `profile.json` directly.

```json
{
  "schema_version": 1,
  "identity": {
    "stated_self": "a good, masculine Muslim man",
    "principles": ["honesty", "discipline"],
    "provenance": ["<entry-uuid>"]
  },
  "goals": [
    {
      "id": "<uuid>",
      "text": "Pray fajr consistently",
      "status": "active",
      "confidence": 0.82,
      "last_seen": "2026-06-10",
      "evidence": ["<entry-uuid-1>", "<entry-uuid-4>"],
      "source": "model"
    }
  ],
  "patterns": [
    {
      "id": "<uuid>",
      "text": "Avoids difficult conversations when tired",
      "type": "behavior",
      "confidence": 0.74,
      "last_seen": "2026-06-08",
      "evidence": ["<entry-uuid-2>", "<entry-uuid-5>"],
      "source": "model"
    }
  ],
  "relationships": [
    {
      "name": "Daniel",
      "type": "friend",
      "summary": "Close but tension around communication",
      "evidence": ["<entry-uuid-2>"],
      "last_seen": "2026-06-11"
    }
  ],
  "emotional_baseline": {
    "typical_mood": 1,
    "known_triggers": ["fatigue", "conflict"],
    "what_helps": ["prayer", "exercise"],
    "evidence": ["<entry-uuid-3>"]
  },
  "open_loops": [
    {
      "id": "<uuid>",
      "description": "Unresolved argument with Daniel about responsibilities",
      "status": "open",
      "opened": "2026-06-09",
      "last_updated": "2026-06-11",
      "evidence": ["<entry-uuid-2>", "<entry-uuid-7>"]
    }
  ],
  "watch_list": [
    {
      "pattern_id": "<pattern-uuid>",
      "conflicting_goal_id": "<goal-uuid>",
      "description": "Skipping gym when tired contradicts fitness goal",
      "evidence": ["<entry-uuid-5>"]
    }
  ],
  "anchors": []
}
```

**Anchors** are claim IDs the user manually corrected in `profile.md`. They carry `"source": "user"`. The operation grammar cannot apply `weaken` or `strengthen` to an anchor — only the user can change them via `PUT /profile`.

**`profile.md` ↔ `profile.json` sync:** `profile.md` is a human-readable rendering of `profile.json`, regenerated by `profile.py` after each update. When the user edits and saves `profile.md`, a `PUT /profile` call re-parses the Markdown into a diff of JSON operations and applies them as `set_anchor` ops. The parse is lenient: unparseable sections are left unchanged and the user is warned. The `anchors` list tracks any claim IDs the user corrected.

---

### 7.3 L3 operation grammar

The exact verbs the model may emit in nightly/weekly update calls. Code validates and applies them; the model never touches `profile.json` directly. **Any operation without at least one valid `entry_id` in the evidence array is silently rejected** — this is the primary anti-hallucination gate.

| Operation | Required fields | Effect |
|---|---|---|
| `add_goal` | `text`, `evidence[]` | Append to goals; confidence = 0.5, status = active |
| `update_goal_status` | `goal_id`, `status`, `evidence[]` | Change status (active → paused → achieved → abandoned) |
| `add_pattern` | `text`, `type`, `evidence[]` | Append to patterns; confidence = 0.5 |
| `strengthen` | `claim_id`, `evidence[]` | confidence += 0.1 (cap 1.0); add evidence pointers |
| `weaken` | `claim_id`, `reason` | confidence -= 0.15 (floor 0.0); flag for review if < 0.2 |
| `note_contradiction` | `claim_id_a`, `claim_id_b`, `evidence[]` | Add to watch_list; surface to user |
| `mark_resolved` | `loop_id`, `evidence[]` | Set open_loop.status = resolved |
| `update_loop` | `loop_id`, `note`, `evidence[]` | Append note; status = updated |
| `add_relationship_note` | `name`, `note`, `evidence[]` | Append to relationship summary |
| `set_anchor` | `claim_id` | Mark as user-corrected; blocks model overwrite (user-only op) |

**Decay:** Nightly, confidence on each non-anchor pattern and goal is reduced by `0.01 × days_since_last_seen`. A claim not corroborated in 60 days falls below 0.5 and is flagged stale. Anchors do not decay.

---

### 7.4 Knowledge graph API schema (L4)

The contract for `GET /insights/graph`. Both the Phase 14 stub and the real L4 builder must return this exact shape.

```json
{
  "nodes": [
    {
      "id": "n-uuid",
      "label": "prayer",
      "type": "theme",
      "entry_count": 12,
      "entries": ["entry-uuid-1", "entry-uuid-3"]
    }
  ],
  "edges": [
    {
      "id": "e-uuid",
      "source": "n-uuid-a",
      "target": "n-uuid-b",
      "type": "co_occurrence",
      "weight": 0.85,
      "is_hypothesis": false,
      "label": null,
      "entries": ["entry-uuid-1"]
    },
    {
      "id": "e-uuid-2",
      "source": "n-uuid-c",
      "target": "n-uuid-d",
      "type": "hypothesis",
      "weight": 0.6,
      "is_hypothesis": true,
      "label": "may lead to",
      "entries": ["entry-uuid-5", "entry-uuid-8"]
    }
  ]
}
```

Node `type` values: `theme` · `person` · `place` · `goal` · `problem` · `emotion`
Edge `type` values: `co_occurrence` · `temporal` · `similarity` · `hypothesis`
Hypothesis edges are rendered with a dashed line and a confirm/dismiss affordance in the UI. They are never presented as established fact.

---

### 7.5 Sentence-splitter rules (TTS boundary detection)

`backend/voice/sentence_queue.py` must split the token stream at correct sentence boundaries. Rules in priority order:

1. **Do not split after known abbreviations:** `Dr.` `Mr.` `Mrs.` `Ms.` `Prof.` `Sr.` `Jr.` `vs.` `etc.` `e.g.` `i.e.` `approx.` `est.` `fig.` `vol.` — these are checked as whole tokens before the period.
2. **Do not split inside a number:** a period followed by a digit (`$3.50`, `v1.2`, `3.14`) is never a sentence boundary.
3. **Do not split inside an open quotation:** an unmatched `"` or `'` means the sentence is still open.
4. **Split on:** `. ` `! ` `? ` (punctuation + space + uppercase letter or end-of-stream), or `.\n` `!\n` `?\n`.
5. **Minimum chunk length:** do not emit a TTS chunk shorter than 4 words — buffer it into the next sentence. (Short chunks sound robotic.)
6. **Maximum chunk length:** flush at 80 words even without a boundary, splitting at the last word. (Prevents audio stall on very long model sentences.)

**Implementation note:** use a stateful character-level scanner with the abbreviation list and open-quote flag. Do not use `nltk.sent_tokenize` — its import latency and batch-oriented design are unsuitable for token-by-token streaming.
