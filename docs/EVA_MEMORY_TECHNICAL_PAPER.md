# Eva: An Evidence-Linked, Self-Evolving Memory Architecture for Small Language Models

**Technical systems paper — implementation-grounded draft**

**Date:** October 26, 2026

**Implementation baseline:** `5684ec20078660210fd999a7556cb1c661a65aa8`, inspected with existing working-tree changes present.

**Scope:** Architecture and engineering validation; model-quality and target-device experiments remain to be conducted.

## Abstract

Persistent personalization requires more than retaining a conversation transcript: an assistant must distinguish events from aspirations, revise its interpretation as evidence changes, and preserve the user's authority over that interpretation. These requirements become especially demanding when inference runs on a resource-constrained personal computer. We describe Eva, a desktop journaling companion with a five-layer external memory architecture targeting a quantized Gemma 4 E2B model through native llama.cpp. Eva separates durable Markdown journals, structured episode records, local semantic indexes, an incrementally updated user profile, and derived analytics and temporal digests. A fast read loop selects relevant memory for conversation; a background write loop proposes and applies evidence-linked profile operations. Deterministic code owns persistence, counting, confidence arithmetic, and mutation rules, while the language model performs extraction, interpretation, and narration. User corrections become protected anchors, and edits to underlying entries trigger claim revalidation. Here, self-evolution means revision of external memory under fixed model weights. The implementation establishes this mechanism, but does not yet establish superior recall, longitudinal personalization quality, or an 8 GB deployment envelope through controlled experiments. We present its algorithms, implementation boundaries, validation evidence, and an evaluation protocol for testing those claims.

**Keywords:** external memory; small language models; personal AI; journaling; retrieval-augmented generation; memory consolidation; provenance; local inference.

## 1. Introduction

A journaling assistant encounters an evolving account of a person's life. Yesterday's unresolved disagreement may be resolved today; a stated ambition may be paused; an apparent recurring pattern may disappear after a source entry is corrected. Simply adding past messages to a prompt preserves neither a reliable distinction between these states nor a compact representation of what matters now.

Eva addresses this problem through a memory system outside the generative model. Its central hypothesis is that a small model can support useful longitudinal personalization when software constructs the evidence and manages memory updates, leaving the model narrower language tasks. This is a hypothesis about the division of work between model and system, not a claim that a small model acquires the general reasoning ability of a larger model.

This paper documents four implemented mechanisms:

1. A layered storage architecture that preserves original journals independently of derived representations.
2. Separate conversational retrieval and background consolidation paths.
3. A restricted, evidence-linked operation interface for updating the user profile.
4. User anchors and source-edit revalidation for correcting accumulated interpretations.

These mechanisms are a systems contribution in combination and application. We make no priority claim for external memory, forgetting, retrieval, or evolving memory in general. We also distinguish implementation tests from experiments on model accuracy or user benefit.

## 2. Related work and positioning

Retrieval-augmented generation combines model parameters with an external information store. Lewis et al. establish this approach for knowledge-intensive language tasks. Eva uses retrieval at inference time but does not reproduce their jointly trained retrieval-and-generation formulation. Its memory also includes mutable claims about a user, with explicit correction rules. [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401)

MemGPT organizes information across memory tiers to work beyond a model's immediate context window. Eva shares the distinction between working context and persistent storage; its consolidation schedule and permitted profile mutations are primarily controlled by application code. [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)

MemoryBank explicitly studies long-term companion memory, including reinforcement and forgetting. Consequently, neither companion personalization nor confidence decay alone constitutes a novelty claim for Eva. [MemoryBank: Enhancing Large Language Models with Long-Term Memory](https://arxiv.org/abs/2305.10250)

A-MEM describes dynamically linked memories whose representations can change as new material arrives. Eva also evolves stored representations, while constraining profile changes through a fixed operation vocabulary and preserving user edits as anchors. Establishing comparative advantages would require matched experiments. [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)

Eva's distinguishing engineering emphasis is therefore the combination of journal ownership, evidence-linked profile revision, deterministic aggregation, protected human corrections, and a local small-model deployment target.

## 3. System model

Let the memory at time t be:

\[
M_t = (V_t, E_t, I_t, P_t, D_t),
\]

where V is the raw vault, E the structured episodes, I the semantic index, P the user profile, and D the digests and derived views. For a journal entry x_t, the conceptual update is:

\[
M_{t+1} = F(M_t, x_t; \theta), \qquad \theta_{t+1}=\theta_t.
\]

The language model's parameters theta remain fixed. F combines model-generated proposals with deterministic validation and storage operations. This notation describes the architecture; it is not a claim that the full update is deterministic, because model outputs can vary.

We use **self-evolving memory** to mean that new entries can change the contents, confidence, status, and retrieval eligibility of existing external representations. It does not imply online training, autonomous architecture modification, or guaranteed monotonic improvement. A memory can evolve incorrectly.

### 3.1 Five layers

| Layer | Representation | Function and authority |
|---|---|---|
| L0: raw vault | Plain Markdown journal files; preserved edit history | Authoritative journal content, readable independently of Eva |
| L1: episodes | SQLite entry index and structured extractions | Queryable summaries, moods, emotions, entities, themes, events, goals, behaviors, decisions, open loops, and self-judgments |
| L2: semantic index | Local FastEmbed vectors in ChromaDB | Associative recall over summaries and individual episode units; separate uploaded-corpus collection |
| L3: user model | Structured `profile.json` and editable `profile.md` | Goals, patterns, identity, principles, relationship notes, baseline, open loops, watch items, provenance, and user anchors |
| L4: derived views | Statistics, graph structures, and temporal digests | Computed perspectives on lower layers; never authoritative replacements for journals |

The hierarchy is a dependency structure, rather than a single retrieval chain. Chat accesses recent L1 episodes, L2 summary recall, and selected L3 claims directly. L4 digests exist in the consolidation path; they are not a general long-horizon chat retriever in the inspected turn pipeline.

Journal-derived representations can be regenerated from lower layers. Exact replay of model-authored interpretations is not guaranteed. Human corrections are additional inputs: a rebuild preserves anchors from the existing profile, but deleting every profile artifact would lose corrections that were never recorded in journal text. Thus, rebuildable journal indexes and recoverable human edits are separate concerns.

### 3.2 Deployment target

Eva uses a Tauri shell, React/Vite interface, and Python FastAPI backend. Its recommended local provider runs the native `llama-server` binary with the configured `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` artifact. The target is an Apple Silicon MacBook M1 Air with 8 GB of unified memory. This is a deployment target, not a measured result of this paper.

The prescribed local configuration enables full GPU layer offload, an 8,192-token server context, q8_0 K/V caches, flash attention, the embedded Jinja chat template, and disabled reasoning output. Voice models are lazy-loaded to avoid adding their full startup memory cost. Model requests share a lock with chat priority; priority does not imply preemption of a generation already in progress.

The memory interfaces also support explicitly selected online providers. Therefore, claims of fully on-device processing apply to local mode. Selecting an online provider changes the disclosure boundary for prompts and their included memory.

## 4. Capture: preserve first, interpret second

The capture path writes the full entry to L0 before indexing it in SQLite. It then creates a pending extraction record. Background extraction enriches that record and requests summary and episode embeddings.

The extractor receives one entry and produces a structured JSON object. Its output allowance is 900 tokens at temperature 0.3. Parsing and validation tolerate some formatting and key variations, normalize fields, and reject unusable summaries. After two unsuccessful model or parsing attempts, the extraction is marked `null_stored`; the Markdown remains saved. Embedding errors likewise leave the entry and successful extraction intact.

This ordering separates preservation from successful inference. It does not make the filesystem, SQLite, and ChromaDB one atomic transaction: failures can leave representations needing repair. Capture also records a source hash to recognize unchanged extraction inputs, and entry edits preserve prior text in journal history.

Structured goals and behaviors serve different purposes. “I want to exercise regularly” is an aspiration; “I skipped exercise twice” describes behavior. Keeping those fields separate makes later comparisons possible without asking a model to reread an entire history.

However, extraction is already an interpretation. Deterministic arithmetic over extracted fields can reproduce the stored counts exactly while still reflecting an inaccurate extraction. Raw text remains essential for audit and correction.

## 5. The fast read loop

For a query q, the conversation engine assembles three memory sources concurrently:

1. Up to three recent completed episode summaries, providing chronological context.
2. Semantically recalled journal summaries, providing relevant older context.
3. Up to six eligible user-profile slices, providing consolidated context.

Recent entries already returned by semantic recall are removed from the recent-episode block. This avoids repeating the same entry in both blocks. The engine separately gates retrieval from uploaded reference material on `ask_info` or `ask_advice`; venting, processing, and ambient conversation do not fetch that corpus. Journal recall still operates in those modes.

### 5.1 Summary recall

The default summary-recall path requests six candidates, rejects cosine distances greater than 0.50, and keeps at most three. Survivors are ranked by:

\[
s(m,q)=\max(0,1-d(m,q)/2)\,[0.8+0.2\cdot 2^{-a(m)/30}],
\]

where d is cosine distance and a is nonnegative age in days. The relevance gate is applied before recency weighting. Older memories retain a floor of 80 percent of their relevance score. Empty queries, unavailable stores, or no sufficiently close candidates return no memories.

The threshold is provisional: implementation comments document tuning against only four real entries. It must not be interpreted as an established boundary between true and false memories. Returning real source material prevents invented retrieval records, but does not prevent the generator from misinterpreting that material.

Individual open loops and events also have an indexed recall interface. The inspected chat engine uses summary recall, rather than automatically calling every available episode-recall interface.

### 5.2 Profile selection

A profile candidate matches when it shares a content word with the query or has local embedding cosine similarity of at least 0.62. Semantic matching augments lexical matching and degrades to lexical matching when embedding fails. The embedding model is `BAAI/bge-small-en-v1.5`, with distinct query and passage handling.

Selection also checks provenance and quality. Model-owned claims need resolvable evidence; stale and review-flagged claims are excluded, and confidence-bearing model claims below 0.2 are omitted. Explicit user-authored claims can qualify without journal evidence. Eligible slices are ranked by type priority, confidence, evidence count, and identifier, then capped at six. Semantic similarity decides eligibility; it is not the final ranking score.

This profile mechanism can retrieve a fitness goal for a paraphrased workout question even when the original wording differs. The decision record also notes that semantic threshold calibration with real embeddings remains necessary.

### 5.3 Context bounds

Count caps and output limits encourage compact prompts, but do not establish a strict input token bound. Entry lengths and claim lengths vary. A 450-token reply cap limits generated output; it does not reserve enough context for an arbitrarily long prompt. The chat path keeps a limited history, but requires tokenizer-aware accounting to guarantee:

\[
T_{instructions}+T_{history}+T_{memory}+T_{query}+T_{reply}\leq8192.
\]

The project's larger consolidation allowance of up to 32,768 tokens is a policy ceiling, not capacity available from a server launched with an 8,192-token context. A configured deployment must respect its actual server limit.

## 6. The background write loop

### 6.1 Operation-based profile revision

The model proposes JSON operations; application code validates and applies them. The supported vocabulary includes:

| Purpose | Operations |
|---|---|
| Goals and patterns | `add_goal`, `add_pattern`, `update_goal_status`, `strengthen`, `weaken` |
| Tensions and unresolved threads | `note_contradiction`, `mark_resolved`, `update_loop` |
| Identity and baseline | `set_identity`, `add_principle`, `add_baseline_item` |
| Relationships | `add_relationship_note` |

Every model operation must include the required fields and cite at least one entry identifier from the supplied batch. Application checks additionally reject missing targets, invalid enumerated values, and protected-anchor modifications. Supporting and counter-evidence are stored separately when a claim is weakened.

Conceptually:

```text
episodes := select completed entries for the consolidation pass
proposals := generate operations(episode summaries, compact current profile)
for proposal in proposals:
    validate operation, required fields, and known evidence identifiers
    check target existence and anchor protection
    apply permitted mutation with code-owned confidence arithmetic
apply elapsed-time decay to eligible goals and patterns
refresh code-derived mood baseline
persist the profile
```

An evidence identifier proves that a referenced entry exists in the permitted set. It does not prove that the entry entails the claim. A model can cite a real entry incorrectly. The operation grammar limits mutation authority and provides traceability, rather than proving semantic correctness.

Operation prompts currently receive entry identifiers, dates, summaries, and themes. Rich L1 fields such as explicit behaviors and goals are used by other consolidation logic, but are not all directly passed into this generator. Information omitted from summaries can therefore be missed during profile updates.

### 6.2 Confidence and decay

For model-authored goals and patterns, initial confidence is 0.5. A strengthen operation adds 0.1, and a weaken operation subtracts 0.15, clipped to [0,1]. Let delta be the number of days since the later of `last_seen` and `decayed_through`. Decay applies:

\[
c'=\max(0,c-0.01\max(0,\Delta)).
\]

Recording `decayed_through` makes repeated same-day decay idempotent and avoids charging the same elapsed days twice. Claims unseen for at least 60 days with confidence below 0.5 become stale. User anchors are exempt. These rules apply to the implemented goal/pattern confidence mechanism, not uniformly to every profile field.

The confidence value is a heuristic priority and persistence signal, not a calibrated probability. Deduplicating evidence identifiers does not guarantee that a repeated operation cannot add confidence again. Weekly processing intentionally revisits entries, so evidence independence and repeated reinforcement require measurement. Likewise, nightly decay is invoked through the profile-update path; a pass with no selected entries does not itself call that update function, so decay should not be described as unconditionally running every calendar day.

### 6.3 Nightly consolidation

The nightly pass selects the oldest completed, unconsolidated entries through its cutoff, capped at 25. This drains late extractions instead of discarding them when their original date window has passed. It lifts open loops into the profile, uses lexical topic overlap to find candidates, and asks a bounded yes/no question before resolving a thread from later evidence.

It then updates L3 and marks entries consolidated only when operation generation succeeds. Valid `[]` output means no change; failure after both attempts remains distinguishable and leaves entries eligible for retry. Claims flagged after source edits undergo revalidation against current summaries.

The rebuild path batches 15 entries per operation-generation call. This should not be confused with a universal live-consolidation batch limit: nightly selection permits 25, while the weekly operation call receives its selected week's entries. The compact profile also lacks a hard total-length cap. Bounded windows are an architectural intention with incomplete token-level enforcement.

### 6.4 Weekly mining and digests

Weekly code counts themes, emotions, emotion pairs, and recurring open loops. It also constructs behavior-versus-goal candidates using lexical topic overlap. A candidate requires behaviors from at least two distinct entries. Shared topic is not contradiction: exercising and aspiring to exercise are aligned. A model judgment checks opposition, and a subsequent support check gates recording a linked watch item. Counts originate in code; their interpretation still depends on model judgments.

The weekly digest stores computed statistics and optionally a short model-written summary. Month digests reduce week digests, and the era digest reduces month digests. Higher-level rollups are model-free and currently retain selected aggregate fields rather than every child statistic or a recursively narrated life history. A week belongs to the month containing its end date, preventing boundary-week duplication while making these month aggregates different from exact calendar-day totals.

Rollup summaries are also lossy: top-theme truncation can discard information, and weighting child mood means by entry count can differ from weighting by valid mood-observation count when moods are missing. “Computed by code” establishes where the arithmetic occurs; it does not establish statistical sufficiency or validity.

## 7. Correction and user authority

Eva exposes an editable Markdown profile backed by structured state. Recognized user edits mark corresponding claims or fields as user-owned anchors. Model operations and decay cannot overwrite those protected values, and profile rebuilds carry them forward. The Markdown parser supports the profile's defined structure; this is not a promise to infer arbitrary free-form edits correctly.

Editing a journal entry preserves its previous version, refreshes derived representations, and flags dependent claims for revalidation, including claims linked through counter-evidence. Subsequent consolidation checks support and can clear the flag or weaken the claim. This creates a repair path from changed source material to accumulated interpretation.

The resulting control hierarchy distinguishes original journal content, explicit user corrections, and model-authored interpretation. None of these is a license to treat journaling as objective observation of every real-world event. A profile models what was written and corrected, with gaps and uncertainty.

## 8. Privacy boundaries

Local inference and embeddings allow the memory pipeline to operate without a hosted model. The Python backend installs a socket-level outbound guard and maintains a configured host exception for authorized downloads or an explicitly selected online provider. Embedding code sets offline environment defaults; these defaults and the network guard are complementary controls, not an operating-system sandbox.

The guard covers the Python socket paths it patches. It is not proof that native processes, the desktop shell, or every library transport obey the same restriction. Likewise, host/IP allowlisting does not itself establish every application-level destination policy. Whole-application privacy needs process-level network observation alongside code review.

Markdown ownership also does not imply encryption at rest. Journals, extracted summaries, profiles, indexes, and diagnostic traces can all contain sensitive material. Local traces are observability artifacts, not evidence that private contents are absent from disk. Evaluation should use synthetic or explicitly consented material, and exported traces need deliberate handling. No telemetry service is required by the memory architecture.

## 9. Engineering validation and reproducibility

The existing tests cover extraction parsing and failure handling; durable capture; evidence validation; anchor protection; confidence arithmetic; source-edit dependencies; consolidation retries; delayed extractions; loop resolution; contradiction rejection; digest boundaries; retrieval filtering; profile slicing; conversation assembly; scheduler behavior; privacy auditing; and index rebuilding.

These tests primarily use scripted model callers, synthetic entries, and mocked retrieval results. They can validate control flow and deterministic rules, but cannot establish real-model extraction accuracy, embedding relevance, truthful generation, or longitudinal user benefit. A test that survives missing embeddings may validate lexical fallback instead of semantic quality.

The validation record for this draft is provided in Appendix B. No benchmark scores, target-device latency, peak-memory measurements, clinical outcomes, or comparisons with larger models are claimed.

## 10. Proposed empirical evaluation

The following protocol is proposed work, not a completed experiment.

### 10.1 Research questions

- Does evolving L3 memory improve evidence-grounded personalization over recent history and summary retrieval alone?
- How accurately does the small model extract and revise claims, especially after contradictions or source edits?
- Do anchors prevent repeated reintroduction of corrected interpretations?
- Does the local configuration remain responsive within the target device's memory constraints during background consolidation?

### 10.2 Data and temporal protocol

Construct consented or synthetic multi-week journals annotated with events, aspirations, behaviors, relationships, unresolved threads, corrections, and supporting evidence. Include paraphrases, negation, hypothetical statements, sparse weeks, goal changes, late extractions, edited evidence, and topics never mentioned.

Process entries in timestamp order. At each query checkpoint, expose only entries available by that time. Reserve subjects or independent synthetic timelines for held-out evaluation; tune retrieval thresholds separately. Record dataset version, model artifact checksum, quantization, llama.cpp build, prompts, provider settings, and memory snapshots. Separate deterministic retrieval measurements from stochastic extraction and generation outcomes.

### 10.3 Baselines and ablations

Use the same generative model and comparable context budgets for recent-history-only, summary-RAG, and full Eva conditions. Evaluate ablations removing L3 updates, semantic slice matching, decay, source-edit revalidation, or anchors. A whole-profile prompt baseline can test whether selective injection helps under matched token limits. Larger-model comparisons should be reported separately from the effect of memory architecture.

### 10.4 Measures

| Component | Suggested measures |
|---|---|
| Extraction | Field-level precision/recall, parse success, unsupported extracted assertions |
| Retrieval | Recall@k, precision@k, irrelevant-memory injection rate, paraphrase recall |
| Profile revision | Human-rated claim support, contradiction precision/recall, duplicate-claim rate, unsupported-operation acceptance |
| Correction | Time to repair after source edits, anchor mutation rate, recurrence of corrected claims |
| Generation | Evidence-supported personalization, temporal consistency, unsupported personal assertions |
| Runtime | Time to first token, full-turn latency, consolidation duration, model-call counts, peak system memory, swap activity |
| Privacy | Process-level network destinations in local mode and allowed destinations in online mode |

Use blinded independent annotation with adjudication for semantic correctness and report agreement. Sample multiple generations where applicable. Report uncertainty clustered by independent journal timeline, not only by individual turns. Measure the complete target-device workload, including optional voice and concurrent consolidation, rather than using model-file size as a proxy for memory demand.

## 11. Limitations and threats to validity

The strongest current claim is architectural: Eva implements external-memory evolution with provenance and human correction. Several stronger conclusions remain unsupported.

First, existing-entry citations are necessary traceability checks but insufficient semantic support. Verifiers using the same model family can repeat the original error; the current yes/no parser recognizes answer text permissively and is not a formal verifier.

Second, compression can propagate omissions. Profile updates rely on summaries; higher digests retain selected statistics. Important minority experiences can disappear from working context even while raw text remains recoverable.

Third, count-based limits do not ensure constant prompt size or constant retrieval cost as history grows. Profile semantic scoring currently considers candidate texts across the profile, and compact profile generation can grow with claim count. The all-time era reduction can also grow in child count even though it makes no model call.

Fourth, lexical miners miss paraphrased goals and can overmatch related but distinct concerns. Distinct supporting entries need not be independent experiences. A single cited episode can currently seed a pattern claim; recurrence is not universally enforced at insertion.

Fifth, evolving does not mean improving. Extractor errors, model judgments, repeated reinforcement, weakly calibrated thresholds, and writing-selection bias can degrade the profile. Journaling frequency is not a direct measurement of real-world behavior frequency.

Finally, the 8 GB target and small-model usefulness require hardware and longitudinal evaluation. Neither source inspection nor model-free tests establish superiority over simpler memory or suitability for psychological assessment. Analytics should describe recorded changes and support reflection, rather than deliver character judgments or causal conclusions.

## 12. Conclusion

Eva implements self-evolution in an external memory system: new journals become structured episodes, selected episodes change an evidence-linked profile, later conversations retrieve relevant profile slices, and user corrections constrain future updates. Model weights remain fixed throughout this process.

The architecture supplies a concrete mechanism for small-model personalization by moving durable storage, aggregation, and mutation policy into software. Its effectiveness, resource envelope, and comparative advantages remain empirical questions. The defensible description is an implemented, evidence-linked, self-evolving external memory architecture designed for small local language models.

## References

1. Patrick Lewis et al. (2020). [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401).
2. Charles Packer et al. (2023; revised 2024). [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560).
3. Wanjun Zhong et al. (2023). [MemoryBank: Enhancing Large Language Models with Long-Term Memory](https://arxiv.org/abs/2305.10250).
4. Wujiang Xu et al. (2025). [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110).

## Appendix A. Implementation evidence map

Paths below identify repository artifacts, rather than external research references. Descriptions reflect source inspection at the baseline above, including existing uncommitted changes; this paper does not modify those changes.

| Paper mechanism | Implementation evidence |
|---|---|
| Raw journal durability and revisions | `backend/memory/vault.py`, `backend/memory/capture.py` |
| Structured extraction and retry | `backend/memory/extract.py`, `backend/prompts/extract_entry.md`, `backend/memory/schema.sql` |
| Local embeddings and indexed units | `backend/memory/vector.py` |
| Summary recall and recency score | `backend/memory/retrieval.py` |
| Profile evidence, slices, Markdown edits, anchors | `backend/memory/profile.py` |
| Semantic selection decision and calibration caveats | `docs/decisions/2026-07-06-semantic-profile-slice-relevance.md` |
| Operation validation, application, and decay | `backend/memory/operations.py`, `backend/prompts/profile_operation.md` |
| Nightly/weekly updates, verification, and digests | `backend/memory/consolidate.py`, `backend/memory/verification.py` |
| Profile reconstruction | `backend/memory/rebuild_profile.py` |
| Conversational assembly and history limit | `backend/engine/turn.py`, `backend/prompts/assembly.py`, `backend/app.py` |
| Scheduling | `backend/scheduler.py` |
| Native inference, providers, and shared model access | `backend/llm/server.py`, `backend/llm/client.py`, `backend/llm/providers.py` |
| Network guard | `backend/net_guard.py` |
| Local context tracing | `backend/eval/trace.py`, `backend/eval/run.py` |

## Appendix B. Validation record

On October 26, 2026, the following existing suites completed with **170 passed in 12.74 seconds** using Python 3.12.10 and pytest 8.3.4 on Windows:

```powershell
cd backend
.\venv\Scripts\python.exe -m pytest `
  tests/test_extract_parse.py tests/test_capture.py `
  tests/test_profile_operations.py tests/test_profile.py `
  tests/test_consolidate.py tests/test_retrieval.py `
  tests/test_engine_turn.py tests/test_scheduler.py `
  tests/test_privacy_audit.py tests/test_reindex.py `
  -q -p no:cacheprovider --basetemp=../.paper-validation-full `
  -o faulthandler_timeout=30
```

The successful run used the normal installed dependencies without the temporary dependency-disabling experiments attempted during diagnosis. Earlier sandboxed attempts stalled while Windows asyncio created its loopback socket pair; the successful run required execution outside that sandbox. Its results establish the selected engineering checks, not live-model quality, whole-application network isolation, or Apple Silicon performance. Generated test vaults were removed after validation.

### Manual review and remaining work

This documentation change adds only `docs/EVA_MEMORY_TECHNICAL_PAPER.md`; it changes no runtime behavior. Review the file in a Markdown viewer and compare mechanisms against Appendix A. On a separate test vault with the local model and embeddings installed, save a goal and later relevant entries, run consolidation, inspect evidence-linked profile changes, then ask a paraphrased question. Edit a profile claim and verify that subsequent consolidation preserves its anchor. Edit a supporting journal entry and inspect the dependent claim's revalidation. These are proposed manual checks, not actions performed on the user's journals for this paper.

Before a research submission, complete the chronological evaluation in Section 10, calibrate thresholds with real embeddings, measure the target device, and replace proposed experiments with reproducible results. Token-budget enforcement, repeated reinforcement, rollup statistics, and verifier robustness remain implementation limitations described by this paper; no runtime fixes are included in this writing task.
