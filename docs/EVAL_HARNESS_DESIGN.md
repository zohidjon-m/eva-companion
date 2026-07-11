# Eva — Evaluation Harness Design

**Proving the thesis, not testing the code**
*Design doc v1 · 2026-07-11 · Local-first / offline eval infra · consumes `engine.turn.TurnState`*

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

- Steps **1–3 are deterministic** given a fixed vault: intent classification, all
  retrieval, and full system-prompt assembly happen with no model sampling.
- Step **4 is the only stochastic part** (the LLM at temp 0.7).
- `TurnState` already carries the *raw retrieved objects with their scores*
  (`state.memories`, `state.passages`, `state.episodes`, `state.profile_slice_items`), and
  `TurnState.meta_frame()` already emits per-source retrieval counts.

**Consequence:** we can measure the entire context-assembly behavior — the 80% of Eva that
matters — without ever paying for or waiting on a model call. The harness is built on that
boundary.

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
turn end to end.

```python
class SlotTrace(BaseModel):
    name: str                       # episodes | memory | profile | corpus
    items: list[RetrievedItem]      # each with id/date/label + distance/score
    rendered_chars: int
    approx_tokens: int

class Trace(BaseModel):
    schema_version: int
    input: str
    mode: str
    intent_label: str
    intent_method: str              # keyword | embedding | llm — how intent was decided
    corpus_gate_fired: bool
    slots: list[SlotTrace]
    system_prompt: str              # the VERBATIM assembled window
    total_prompt_tokens: int
    step_latency_ms: dict[str, float]
    # generation (only when the model was run):
    reply: str | None
    citations: list[dict]
```

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

**Trace store (`store.py`).** Append-only local JSONL keyed by a content hash of
`(input, mode, vault_version)`. Offline by construction — no trace ever leaves the machine,
consistent with Eva's privacy contract. This store is what makes the L7 flywheel possible:
production traces (sampled from the real `/chat` socket via the existing `meta` frame) land
here and become future fixtures.

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
already eval fixtures; we formalize them. Each label is a judged (query, entry) pair:

```yaml
- persona: yusuf
  query: "I relapsed again last night and I feel disgusting"
  should_surface: [entry_2026_05_18, entry_2026_06_02]   # relevant journal ids
  must_not_surface: [entry_2026_04_10]                    # off-topic (gym PR day)
  expect_corpus_gate: false                               # this is a vent
```

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
the margin to the nearest bad regime. CI fails if a change moves the operating point past a
tolerance. **This is the artifact a reviewer stops on** — it converts "I picked 0.38 and it
felt right" into "here is the ROC and here is why."

**Done when:** the sweep reproduces (or corrects) the current constants from data, and prints
the precision/recall the chosen threshold buys.

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

**Calibrated judge (`judge.py`).** Rubric-based, and the judge itself validated against a
small human-labeled set with reported agreement (Cohen's κ). Include bias controls
(position-swap for pairwise, no length leakage). An uncalibrated "ask a model if it's good"
is the junior version; a judge with a κ number next to it is the senior one.

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
  production /chat traces (meta frame) → sampled → labeled → added to golden set
        ↑                                                             │
        └──────────────── guards against that failure forever ───────┘
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
| 4 | **Ablation harness** | L6 | per-source contribution table | yes |
| 5 | **Generation + calibrated judge** | L4 | faithfulness/relevance as mean ± CI; judge κ | yes |
| 6 | **Adversarial & safety** | L5 | crisis recall, injection, isolation | mixed |
| 7 | **CI gate + flywheel** | L7 | per-PR gate + nightly trends + trace-sampling loop | — |

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
