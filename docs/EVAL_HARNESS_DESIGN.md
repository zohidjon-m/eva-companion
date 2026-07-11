# Eva — Evaluation Harness Design

**Proving the thesis, not testing the code**
*Design doc v2 · 2026-07-11 · Local-first / offline eval infra · consumes `engine.turn.TurnState`*

> **v2 changelog (peer review).** Six fixes folded in: (1) the determinism claim is
> corrected — intent classification calls the model on the ambiguous residue, so only
> *rule-classified* turns are model-free (§1, §5); (2) the `Trace` now records full
> provenance **and raw candidates before threshold filtering**, which also makes the
> threshold sweep pure offline post-processing with no re-embedding (§3, §4.3); (3) the
> flywheel needs an explicit local capture hook, not the counts-only `meta` frame (§3.1, §9);
> (4) retrieval labels are **per-source** (memory / episode / profile / corpus ids), not
> memory-centric (§4.1); (5) thresholds are tuned and certified on *different* data with
> bootstrap CIs, reported as **provisional**, gated on "don't regress" not "optimal" (§4.3);
> (6) the judge is local-first — an online judge is opt-in like any provider (§6). Plus a
> sequencing note: ablation (slice 4) is coupled to the L4 metric machinery (slice 5) (§10).

---

## 0. Purpose & the one thesis this harness exists to defend

Eva makes a single, falsifiable claim, stated in the code itself
([`memory/retrieval.py`](../backend/memory/retrieval.py) §Phase 11, and
EVA_MEMORY_ARCHITECTURE §0.2):

> **Code does the remembering; the model only narrates over pre-assembled evidence.**

Every other Eva behavior is a sub-claim of that thesis:

- **Listen-first** — a vent/process/ambient turn never pulls library passages into the window.
- **No fabricated memory** — a query about something never journaled surfaces *nothing*, never a false "I remember when you…".
- **No fabricated citations** — every citation Eva can show traces to a passage actually retrieved this turn.
- **Relevance, nudged by recency** — recency reorders genuinely-relevant memories, it never manufactures a recent one over a better-matching old one.
- **Each context source earns its slot** — episodes, recall, profile, and corpus each measurably improve the reply enough to justify the tokens they cost.

A **test suite** checks that functions return expected values. This **eval harness** turns
each claim above into a *measured, versioned, CI-gated metric*, and makes the pipeline that
produces those claims *replayable and ablatable*. That is the difference between "Eva works
on my machine" and "here is the evidence, per commit, that Eva does what it claims."

**Non-goals for this harness.** Not a unit-test replacement (the `backend/tests/` suite
stays). Not a cloud eval service — Eva is local-first (`net_guard`, local vault), so the
harness runs **fully offline** against a local trace store. Not a leaderboard — the audience
is the maintainer and a technical reviewer, not a public benchmark.

---

## 1. Why this system is unusually evaluable (the seam we exploit)

Most RAG systems tangle retrieval and generation so the two can't be judged apart. Eva
does not. The chat turn is six ordered steps over one mutable
[`TurnState`](../backend/engine/turn.py):

```
classify → assemble_context → check_in → reason → check_out → persist
  (1)          (2)              (3)        (4)       (5)        (6)
  └──────────── deterministic ───────────┘   └─ stochastic ─┘
```

- Steps **1–3 are deterministic given a fixed vault *for rule-classified turns*.** All
  retrieval and system-prompt assembly are model-free. Intent, however, is **not** fully
  deterministic: [`intent.classifier.classify()`](../backend/intent/classifier.py:316)
  answers with rules first (`method="rule"`) but falls back to **one model call**
  (`method="model"`) on the ambiguous residue. So step 1 is model-free *only* when the rule
  layer fires.
- Step **4 (generation) is the other stochastic part** (the LLM at temp 0.7).
- `TurnState` already carries the *raw retrieved objects with their scores*
  (`state.memories`, `state.passages`, `state.episodes`, `state.profile_slice_items`), and
  `TurnState.meta_frame()` emits per-source retrieval **counts only** — not the scored
  objects the flywheel needs (see §3.1).

**Consequence & the two strata.** We can measure the entire context-assembly behavior — the
80% of Eva that matters — with at most a *bounded* model call, and often none. The harness
therefore splits every turn into one of two strata, recorded on the trace:

- **Deterministic stratum** — `intent_method == "rule"`. Fully reproducible; asserted
  exactly in L2/L3, run per-commit. The harness exposes a `rules_only=True` mode that
  *requires* this method and fails loudly if a scenario secretly took the model path.
- **Model-classified stratum** — `intent_method == "model"`. Treated as stochastic like
  generation: N samples, pass@k, CIs — never asserted as a single deterministic value.
  Scenarios that must stay deterministic can instead **inject a fixed `IntentResult`** so
  the retrieval/assembly under test isn't hostage to a 2B classification coin-flip.

### 1.1 The five sources that feed the context window

The window is assembled in [`prompts/assembly.py`](../backend/prompts/assembly.py) from five
named slots, each dropped if empty:

| Slot | Source | Gated? | Threshold constant |
|---|---|---|---|
| `persona_block` | `eva_system.md` (+ mode + crisis addenda) | always | — |
| `episodes_context` | recent L1 episodes (SQLite, newest-first) | always | `RECENT_EPISODES_LIMIT=3` |
| `memory_context` | relevance recall over `journals` vectors | always | `MEMORY_MAX_DISTANCE=0.50` |
| `profile_slices` | profile store | always | (semantic + lexical gate) |
| `corpus_context` | RAG over `corpus` vectors | **only if `intent.retrieves`** | `MAX_DISTANCE=0.38` |

The threshold constants are the harness's primary numeric targets (see §4.2). They are
currently hand-tuned against a ~4-entry sample with code comments that literally say
*"re-measure and re-centre… against a fuller vault."* Making that quantitative is the
single highest-signal deliverable.

---

## 2. Architecture of the harness

Seven layers, cheapest/most-deterministic first. The lower layers run in milliseconds on
every commit; the upper layers are slow, stochastic, and run nightly or on-demand.

```
┌─────────────────────────────────────────────────────────────┐
│ L7  Operational: CI gate · threshold sweeps · failure flywheel │  nightly / per-PR
├─────────────────────────────────────────────────────────────┤
│ L6  Ablation: null each source, measure the delta            │  on-demand
├─────────────────────────────────────────────────────────────┤
│ L5  Adversarial & safety: crisis · injection · isolation     │  per-PR (fast) + nightly
├─────────────────────────────────────────────────────────────┤
│ L4  Generation: faithfulness · relevance · voice (LLM-judge) │  nightly (stochastic)
├─────────────────────────────────────────────────────────────┤
│ L3  Behavioral contracts: listen-first · no-fabrication      │  per-commit (deterministic)
├─────────────────────────────────────────────────────────────┤
│ L2  Retrieval evals: precision/recall · MRR · false-recall   │  per-commit (deterministic)
├─────────────────────────────────────────────────────────────┤
│ L1  Trace substrate: the replayable TurnState trace + store  │  foundation
└─────────────────────────────────────────────────────────────┘
```

Proposed package layout (mirrors `backend/` conventions — module docstrings, small pure
functions, no network):

```
backend/eval/
  __init__.py
  trace.py          # L1  Trace schema + trace_turn()
  store.py          # L1  local JSONL/SQLite trace store (offline)
  datasets/         # versioned fixtures with ground-truth labels
    personas.py     #     Yusuf / John as eval fixtures
    retrieval_labels.yaml
    scenarios.yaml
    generation.yaml
  metrics.py        # L2/L4 precision, recall, MRR, faithfulness, CIs
  retrieval_eval.py # L2
  contracts.py      # L3
  judge.py          # L4  calibrated LLM-as-judge
  adversarial.py    # L5
  ablation.py       # L6
  sweep.py          # L7  threshold ROC report
  run.py            # CLI entry: run all / one layer / --inspect "<msg>"
```

---

## 3. L1 — Trace substrate (the atom)

Everything downstream consumes a `Trace`. It is a versioned Pydantic model capturing one
turn end to end — including **enough provenance to replay it exactly**, and **the raw
candidates before threshold filtering** so the threshold sweep (§4.3) is pure post-processing
with no re-embedding.

```python
class RetrievedItem(BaseModel):
    id: str                         # memory/episode/profile-claim/corpus-chunk id
    label: str                      # human label (date, source·page, …)
    distance: float                 # cosine distance (the raw score)
    score: float | None             # post-weighting score where applicable (recency)
    kept: bool                      # survived the threshold + top_k?

class SlotTrace(BaseModel):
    name: str                       # episodes | memory | profile | corpus
    candidates: list[RetrievedItem] # RAW — everything pulled, pre-threshold, pre-top_k
    kept: list[RetrievedItem]       # what actually entered the window
    rendered_chars: int
    approx_tokens: int

class Provenance(BaseModel):
    vault_hash: str                 # content hash of L0 markdown + eva.db
    profile_snapshot_hash: str
    chroma_collection_versions: dict[str, str]   # journals/corpus/episodes
    embedding_model: str            # + query-prefix flag (bge-small prefix on/off)
    seeded_filter_mode: str         # is_seeded filter state at capture time
    thresholds: dict[str, float]    # MAX_DISTANCE, MEMORY_MAX_DISTANCE, EPISODE_MAX_DISTANCE
    intent_forced: bool             # was an IntentResult injected? (deterministic scenarios)

class Trace(BaseModel):
    schema_version: int
    provenance: Provenance
    input: str
    mode: str
    intent_label: str
    intent_method: str              # "rule" | "model" — the stratum discriminator (§1)
    corpus_gate_fired: bool
    slots: list[SlotTrace]
    system_prompt: str              # the VERBATIM assembled window
    total_prompt_tokens: int
    step_latency_ms: dict[str, float]
    # generation (only when the model was run):
    reply: str | None
    citations: list[dict]
```

Capturing raw `candidates` (not just `kept`) at a generous `n_results` is the key move: a
threshold sweep then re-applies `distance <= θ` over the stored array offline, so sweeping
`MEMORY_MAX_DISTANCE` across its whole range costs **one** retrieval pass, not one per θ.

`trace_turn()` runs the deterministic pipeline (and, optionally, generation) and returns a
`Trace`:

```python
async def trace_turn(text, *, mode="friend", history=None, run_model=False) -> Trace:
    state = TurnState(text=text, mode=mode, history=history or [])
    await classify(state)
    await assemble_context(state)
    await check_in(state)                      # builds state.messages
    system_prompt = state.messages[0]["content"]
    # ... snapshot slots from state.memories / passages / episodes / profile_slice_items ...
    if run_model:
        # drive stream_chat, then check_out
    return Trace(...)
```

### 3.1 Trace store & the capture hook

**Trace store (`store.py`).** Append-only local JSONL keyed by a content hash of
`(input, mode, vault_hash)`. Offline by construction — no trace ever leaves the machine,
consistent with Eva's privacy contract.

**The capture hook (not the `meta` frame).** The existing `meta_frame()` emits *counts*, so
it cannot feed the flywheel — you can't relabel a candidate you never recorded. Instead,
`engine.turn` gains one optional seam: when `EVA_TRACE=1`, after `check_in` (and `reason`, if
generation ran) the engine builds a full `Trace` from `TurnState` — which already holds every
scored object — and appends it to the store. Off by default, zero cost when disabled, and it
captures *real* turns from the maintainer's own vault. That is the loop the flywheel runs on
(§9); the UI `meta` frame stays a lightweight display concern, unchanged.

**Done when:** `python -m eval.run --inspect "I snapped at my mother again"` prints the
intent, every retrieved item with its distance, the gate state, per-slot token counts, and
the verbatim window. This alone answers *"where does Eva take the info from?"* for any input.

---

## 4. L2 — Retrieval evals (the real signal)

The context window is the product; the model is a renderer. So the heaviest measurement is
on *what enters the window*, judged against ground-truth labels — independent of the model.

### 4.1 Dataset: `retrieval_labels.yaml`

Built over the seeded personas (Yusuf, John), which are already *"real data through the
real pipeline"* ([`scripts/seed_yusuf.py`](../scripts/seed_yusuf.py)) — i.e. they are
already eval fixtures; we formalize them. Labels are **per-source**: each of the four
retrieval sources gets its own expected/prohibited id lists, so L2 precision/recall is
well-defined for corpus, profile, and episodes — not just memory. Memory-only labels would
leave three of the four sources unmeasured.

```yaml
- persona: yusuf
  query: "I relapsed again last night and I feel disgusting"
  expect_intent: vent            # deterministic if rule-classified; else inject (§1)
  expect_corpus_gate: false      # a vent must not fire corpus RAG
  memory:
    should_surface: [mem_2026_05_18, mem_2026_06_02]
    must_not_surface: [mem_2026_04_10]        # off-topic (gym PR day)
  episodes:
    should_surface: [ep_relapse_openloop]
  profile:
    should_surface: [claim_struggles_with_pornography]
    must_not_surface: [claim_trains_at_gym]
  corpus:
    should_surface: []           # gated off this turn → must be empty
```

Ids must be **stable across a reseed** — the sweep and CI compare against them — so the
persona seed scripts assign deterministic ids (or the dataset builder derives them from a
content hash), recorded in `provenance.vault_hash`.

### 4.2 Metrics (per source: memory / corpus / episodes / profile)

| Metric | Definition | Guards |
|---|---|---|
| **Context precision @k** | fraction of surfaced items that are labeled relevant | window not polluted |
| **Context recall @k** | fraction of labeled-relevant items that surfaced | the right memory isn't missed |
| **MRR / nDCG @k** | rank quality of the top item | Eva quotes the *first* memory — it must be the best |
| **False-recall rate** | P(any memory surfaces \| query is labeled "never journaled") | the no-fabrication claim, quantified |
| **Gate-leak rate** | P(corpus fired \| intent is non-retrieving) | listen-first, quantified |

### 4.3 The threshold sweep — the highest-signal artifact (`sweep.py`)

`MEMORY_MAX_DISTANCE=0.50` and `MAX_DISTANCE=0.38` are currently guesses off a tiny sample.
The sweep sweeps each threshold over its range and plots **precision & recall vs. threshold**
and the **precision/recall ROC**, then picks the operating point *quantitatively* with the
false-recall (fabrication) cost weighted higher than a miss — exactly the tradeoff the code
comments reason about in prose but never measured.

Output: a `sweep_memory.md`/`.svg` report showing the curve, the chosen operating point, and
the margin to the nearest bad regime. Because the sweep re-thresholds the stored raw
`candidates` (§3), it costs one retrieval pass total.

**Don't tune and certify on the same data.** Tuning a threshold and then reporting its
precision/recall on the *same* fixtures is overfitting, and quoting a bare optimum with no
interval is the exact junior tell §6 warns against. Two disciplines:

- **Separation.** Pick the operating point on a tuning split (or via cross-validation), report
  the number on a held-out split.
- **Honest small-N.** At today's scale (~a few dozen labeled pairs per persona) neither a
  split nor a bootstrap *certifies* a threshold — there isn't enough data. So the threshold is
  reported as **provisional, with a bootstrap 95% CI**, weighting false-recall (fabrication)
  cost above misses. CI **gates on "don't regress"** (a change must not move the operating
  point past tolerance), never on "this is proven optimal." The real fix — more labels — is
  what the flywheel (§9) feeds. This applies equally to `MAX_DISTANCE`,
  `MEMORY_MAX_DISTANCE`, and `EPISODE_MAX_DISTANCE`.

**This is still the artifact a reviewer stops on** — it converts "I picked 0.38 and it felt
right" into "here is the ROC, the CI, and why I'm calling it provisional."

**Done when:** the sweep reproduces (or corrects) the current constants from data, prints the
precision/recall + bootstrap CI the chosen threshold buys, and labels it provisional.

---

## 5. L3 — Behavioral contract evals

Each thesis sub-claim as a scenario that asserts on the **trace**, not the reply — fast,
deterministic, non-flaky. `scenarios.yaml`:

```yaml
- name: vent does not trigger corpus RAG
  input: "I snapped at my mother again tonight and I hate myself"
  expect: { intent_label: vent, corpus_gate_fired: false, "retrieved.corpus": 0, "retrieved.memory": ">0" }

- name: ask_info about the library retrieves grounded passages
  input: "what does the book say about lowering the gaze?"
  expect: { corpus_gate_fired: true, "retrieved.corpus": ">0" }

- name: no fabricated memory on a never-journaled topic
  input: "what did I say about my trip to Japan?"
  expect: { "retrieved.memory": 0 }
```

Plus a **fuzzing check on `check_out`**: generate turns with synthetic citations injected
into `state.citations` that are *not* in `state.passages`, and assert `check_out` drops
every one. This proves the no-unbacked-citation invariant holds under adversarial input, not
just on the happy path.

**Done when:** every thesis sub-claim in §0 has ≥1 passing scenario, run in <1s, no model.

---

## 6. L4 — Generation evals (stochastic, done with rigor)

Only here do we call the model. The senior markers matter more than the metric list:

- **Faithfulness / groundedness** — fraction of claims in the reply supported by the window.
  *The* metric for a memory companion: an ungrounded "I remember when you…" is the cardinal sin.
- **Answer relevance** — did she answer the question that was asked (for `ask_*` intents)?
- **Voice / contract** — length ≤ the persona's 2–5 sentences; no advice on a non-advice turn.
  Cheap rule checks first, LLM-judge only where rules can't reach.

**Statistical honesty (non-negotiable).** Production runs at temp 0.7; a single pass/fail is
noise. For each case: run **N samples** (fixed seeds where the provider allows), report
**pass@k and mean ± 95% CI**, and track variance across prompt/model versions. A bare "92%"
is a tell that this wasn't done.

**Calibrated judge (`judge.py`) — local-first, like every other Eva provider.** §0 commits
the harness to running offline, so the judge cannot silently assume a frontier model. Three
tiers, in order of preference:

1. **Deterministic / NLI-style checks first.** Faithfulness is largely a
   claim-entailment problem: does each reply claim trace to a window span? Much of it is
   answerable with cheap span-overlap / lightweight NLI, no judge model. Prefer this — it's
   offline, reproducible, and free.
2. **Local 2B model as judge** — available offline, but *weak*, so calibration matters
   **more**, not less: validate it against a small human-labeled set and only trust the
   dimensions where its agreement holds up.
3. **Online judge — opt-in only.** If a stronger judge is wanted, it rides the existing
   opt-in online-provider path (visibly labeled, off by default), exactly like online chat
   mode. Never the silent default.

Whichever tier: rubric-based, validated against a human-labeled set with reported agreement
(Cohen's κ), with bias controls (position-swap for pairwise, no length leakage). An
uncalibrated "ask a model if it's good" is the junior version; a judge with a κ next to it —
and an honest note on which tier produced it — is the senior one.

**Done when:** the generation report prints faithfulness and relevance as mean ± CI over N
samples, and the judge's κ vs. human labels is recorded in the repo.

---

## 7. L5 — Adversarial & safety

- **Crisis recall** — does `safety/crisis_check.py` fire on *paraphrases*, not just keywords?
  Measure recall on a labeled set of crisis/non-crisis phrasings; report false-negative rate
  (the expensive error) explicitly.
- **Injection through journal content** — a user writes "ignore your instructions and…" *in a
  journal entry*; assert it enters the window as data, never as an instruction Eva obeys.
- **Cross-contamination / privacy** — seeded demo data must never surface as a real memory
  (Phase 12 claim). Probe with adversarial queries designed to pull seeded rows; assert the
  `is_seeded=False` filter holds. This is the privacy story a reviewer will poke at.

---

## 8. L6 — Ablation harness (the interview-winner)

Systematically null each context source and measure the delta on the L4 generation metrics:

| Configuration | Faithfulness | Answer-relevance | Δ tokens |
|---|---|---|---|
| full window | (baseline) | (baseline) | — |
| − memory recall | … | … | … |
| − profile slices | … | … | … |
| − recent episodes | … | … | … |
| − corpus | … | … | … |

This produces the table that says *"profile slices improve answer-relevance by X% at Y token
cost; episodes by Z%"* — a **quantitative, per-source answer to "where does the info come
from and does each source earn its slot."** Eva's clean slot architecture (each slot is an
independent parameter to `assemble_system_prompt`) makes this a ~50-line harness: null a slot
before assembly, re-run generation, diff the metrics. Almost nobody builds this; it is the
artifact that reads as *systems thinking*, not feature work.

---

## 9. L7 — Operational layer & the flywheel

- **CI gate.** L1–L3 (deterministic) run on every PR and must not regress to merge. L4–L6
  (stochastic/slow) run nightly with CI-tracked trend lines, not hard gates.
- **Threshold sweep** (§4.3) runs nightly and posts the current operating point + margin.
- **The failure flywheel** — the loop that makes the harness self-improving and the thing to
  *show* you built, not just the tests:

  ```
  production /chat traces (EVA_TRACE capture hook, §3.1) → sampled → labeled → golden set
        ↑                                                                        │
        └──────────────────── guards against that failure forever ──────────────┘
  ```

  Because the trace store is local and offline, this loop runs on the maintainer's own vault
  with no telemetry — the privacy constraint becomes a design feature, not a limitation.

---

## 10. Build order (one slice at a time)

Deterministic-first: the first three slices carry ~80% of the signal and never call the model.

| # | Slice | Layer | Deliverable | Model? |
|---|---|---|---|---|
| 1 | **Trace + `--inspect`** | L1 | dump any turn's retrieval + verbatim window | no |
| 2 | **Retrieval eval + threshold sweep** | L2 | precision/recall table + ROC report; reproduce/correct the constants | no |
| 3 | **Behavioral contracts** | L3 | every §0 sub-claim as a passing scenario; `check_out` fuzz | no |
| 4 | **Generation + calibrated judge** | L4 | faithfulness/relevance as mean ± CI; judge κ | yes |
| 5 | **Ablation harness** | L6 | per-source contribution table | yes |
| 6 | **Adversarial & safety** | L5 | crisis recall, injection, isolation | mixed |
| 7 | **CI gate + flywheel** | L7 | per-PR gate + nightly trends + trace-sampling loop | — |

**Sequencing note (slices 4 → 5).** Generation comes *before* ablation deliberately: an
ablation delta ("−profile drops answer-relevance by X%") is only meaningful once the L4 metric
it diffs has CIs — otherwise you're reading sampling noise as a per-source contribution. Slice
5 reuses slice 4's metric + N-sample machinery; don't build the ablation table on a
point-estimate metric.

We start at slice 1 and do not move to the next until the current one produces a real
artifact (a printed trace, a graph, a passing scenario set).

---

## 11. What makes this read as "flagship," explicitly

The taste markers a technical reviewer skims for — each mapped to a section above:

- Component vs. end-to-end evals separated; deterministic layer distinct from stochastic (§2).
- Retrieval measured **independently of generation** (§4) — proves you know where RAG fails.
- **Confidence intervals and pass@k**, never a bare single number (§6).
- A **calibrated judge** with reported human agreement (§6).
- The **ablation table** proving each source pays rent (§8).
- **Threshold ROC** instead of magic constants (§4.3).
- Datasets **versioned as artifacts** with provenance, and a **failure→fixture flywheel** (§9).
- Evals that run **offline/local**, honoring the product's own privacy contract (§0, §9).

The inverse — a `test_eval.py` of hardcoded asserts with no notion of variance, grounding,
or ablation — reads as junior no matter how good Eva itself is.
