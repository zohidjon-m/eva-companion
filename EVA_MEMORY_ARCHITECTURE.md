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

That, plus the concrete SQLite schema for L0/L1 (so capture is locked before reasoning is built on top of it), is the right next design step.

---

*This is a living document. The layers and the "what must be considered most" rules are the stable contract; the schemas and algorithms beneath them will be filled in as we build.*
