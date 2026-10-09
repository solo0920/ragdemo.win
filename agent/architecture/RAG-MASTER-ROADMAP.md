# RAG Master Roadmap — Gap Audit + Priority Plan (RAG-MASTER-GAP-AUDIT)

Status: **AUDIT + PROPOSAL — no implementation, no behavior change.**
Terminal of this batch: `PASS` / `FAIL` / `STOP`.
Evidence file: `GAP-AUDIT-EVIDENCE.md` (same directory; command transcripts + fingerprints).

Product principle (binding on all batches below):

```text
Precision > Fluency · Evidence > Guess · Grounding > Generative Creativity
Traceability > Convenience · Reproducibility > Hidden heuristics
```

A RAG Engine must never fabricate judgement content, statute content, case numbers,
courts, dates, legal conclusions, or non-existent evidence. `insufficient_evidence`
is a legal terminal state, never a prompt to guess.

---

## 1. Product goal

For Taiwan judicial judgements and statutes: given a user question, the RAG Engine
finds correct, relevant, current, legally meaningful evidence and answers in an
evidence-grounded way — every legal conclusion traceable, every citation resolvable,
abstention when evidence is insufficient.

## 2. Current system state (evidence-backed)

**Frozen retrieval lineage — all verified intact this batch (see evidence file):**
E03 classifier `3c9e102e…651000ab24d`; E05-A-R1 segmentation `5a567299…bdb8918d`
(note: task brief dropped one `b`; file evidence wins, artifacts correct);
E05-B-R1 chunking `c6e41fe2…777d65108c93`; Golden Set `58e01c79…339e7f2c` (76 queries /
114 targets / 40 hard negatives); harness `ec30e8fd…` + corpus `893d40b6…`;
E05-D-A experiment `45a87afa…` + model `34af6022…` + index `2ac93716…`;
E05-D-B experiment `4f4ddacc…` + model `9c1ab1c9…` + index `282a0bd4…`;
E05-D-D selection record sha256 `4cc062c1…` (research complete, NOT benchmarked).

**Serving system (`ragdemo.win`, HEAD `7b40222` + pre-existing worktree):**
`/query` → `rag.answer()` serves the statutes path: law-name pre-filter, dense+sparse
hybrid over Qdrant collection `laws` (1024-dim dense + sparse, capability-probed),
dense-threshold `_decide()` with `no_match` abstention, rerank, multi-provider
generation with a grounding SYSTEM prompt ("only answer from provided material, cite
case/article numbers, say the no-match sentence instead of fabricating").
Judgement pipeline (spec 004, Batch 5): lossless chunking (T015), PG store (T016),
Qdrant payload builder (T017), `search_judgments` + `answer_judgments` with structural
refusal (`no_match: True`, zero statute-text / zero case-numbers on abstain),
provenance chain tests — **734 judgement tests pass, 4 skipped** (project venv).

## 3. Completed capabilities

- C1 frozen retrieval evaluation lineage (E03→E05-D-B) with fingerprints and gates.
- C2 statutes serving path: hybrid retrieval, threshold abstention, rerank, cited generation.
- C3 judgement ingest units: lossless chunking (byte-exact reassembly), deterministic
  point IDs, payload validation, provenance chain — all unit-tested.
- C4 judgement refusal contract (`answer_judgments([])` → structural `no_match`),
  statically checkable (0 statute-text / 0 case-numbers).
- C5 prompt-level grounding instruction in generation SYSTEM prompt.
- C6 citation hit-rate eval framing (`evals/`, 19 questions; POST /eval design).

## 4. Partial capabilities

- P1 judgement retrieval *"judgements"* collection code exists (`search_judgments`,
  `_judgment_filter`, deterministic sort) but **no caller in the serving path** —
  units without integration.
- P2 judgement answer (`answer_judgments`) composes cited blocks with jid/jdate but
  has **no post-generation grounding verification** (prompt asks, nothing checks).
- P3 citation/provenance fields exist (jid/jyear/jdate/jcase/offsets) but the served
  answer has no machine-checkable citation→chunk→offset contract.
- P4 `evals/questions.json` (19) is below its own 50-question acceptance bar.
- P5 repealed-statute handling: retrievable-but-marked design exists for statutes;
  no equivalent currency doctrine for judgements.

## 5. Missing capabilities

- M1 serving-path integration of judgement retrieval/answer (no caller of
  `search_judgments`/`answer_judgments` outside tests).
- M2 mechanical grounding validation (every claim ↔ cited chunk text).
- M3 contradiction detection across retrieved holdings.
- M4 currency doctrine for judgements (superseded / latest-interpretation handling).
- M5 quoted-contamination guard at answer level (a judgement quoting another
  judgement's holding must not be cited as its own).
- M6 disposition-aware evidence roles (holding vs reasoning vs cited precedent vs
  party claims) in answer composition.
- M7 multi-chunk evidence-chain completeness check.
- M8 answer-correctness evaluation (all current metrics are retrieval-level).
- M9 measured host budgets / model registry / planner (deferred to runtime roadmap).

## 6. Retrieval vs answer gap

Current evaluator output (Golden Set + `evals/` hit-rate) measures **retrieval
correctness**: did the right chunks rank highly. Nothing measures whether the final
answer is legally correct, fully cited, contradiction-free, or properly abstained.
**Retrieval quality ≠ legal answer quality.** A high Recall@20 plan can still produce
an ungrounded, mis-cited, or self-contradicting answer. Required evolution:

```text
Retrieval Evaluation (exists, frozen) → Evidence Evaluation (MISSING)
→ Grounded Answer Evaluation (MISSING)
```

This batch proposes the ladder only; no evaluator is implemented here.

## 7. Grounding requirements (engineering, not prompting)

- G1 every legal conclusion in an answer resolves to ≥1 cited chunk (byte offsets).
- G2 citations are machine-checkable: artifact → entry → field → offset (INV-PROV).
- G3 answer text quoting a source must match the chunk verbatim (lossless chain).
- G4 un-cited legal claims are a hard FAIL of the answer, regardless of fluency.
- G5 grounding is verified by code (verifier role), never by asking the generator.

## 8. Abstention requirements

- A1 `insufficient_evidence` / `no_match` is a first-class terminal state with a fixed
  surface (no statute text, no case numbers — already specified for judgements, FR-020).
- A2 abstention thresholds (`_decide()`-family) are calibrated artifacts with provenance,
  not magic constants.
- A3 partial-evidence answers must label exactly which sub-questions lack support.
- A4 abstention must survive rerank/generation pressure (nothing downstream may
  "rescue" a no-evidence case into a fluent answer).

## 9. Evidence / citation requirements

Minimum resolvable citation: court, judgement number, date, source artifact, chunk
(start/end offsets), quoted relevant text. Payload fields for the first five exist
(P3); the quoted-text↔answer linkage (G3) and the machine contract (G2) are missing.

## 10. Contradiction handling — MISSING

No detection exists. Requirement: when top evidence contains mutually contradicting
holdings, the answer must surface the conflict (both sides cited) or abstain on the
contradicted point — never silently pick the higher-scoring chunk.

## 11. Current-judgement handling — PARTIAL→MISSING at answer level

`jdate` is stored and filterable, but no currency doctrine exists: no supersession
graph, no latest-authoritative-interpretation preference, no "old judgement cited as
current law" guard. Requirement mirrors the statutes repealed-marking doctrine,
extended with supersession awareness.

## 12. Multi-chunk evidence — MISSING

No chain-completeness check: when an answer needs N chunks, nothing verifies all N
are present, mutually consistent, and each actually cited. Required as a verifier
check before release of any grounded answer.

## 13. Golden Set evolution (proposal only)

Keep the frozen 76-query retrieval set immutable. Layer forward without touching it:
(a) evidence-level labels on the same 43 chunks (supporting / contradicting /
quoted-only roles); (b) answer-level fixtures (good answer, ungrounded answer,
correct-abstention triplets); (c) contamination probes (quoted-holding traps).
No labels created in this batch.

## 14. Answer evaluation proposal (proposal only)

Three rungs, each with PASS/FAIL gates: evidence selection (did the answer use the
right chunks, with roles), grounding (verbatim quotation + offset resolution),
answer verdict (correct / correct-abstention / FAIL classes: fabricated citation,
misattributed holding, silent contradiction, missed currency). Retrieval metrics
remain frozen and untouched; answer metrics are new, separate artifacts.

## 15. Agent workflow required to complete RAG

Existing authority (frozen) already provides Research/Build/Review/Verify/Gate/STOP
roles. RAG completion needs two instantiated workflows (future batches, specified here):

```text
Research → Design → Implement → Test → Evaluate → Verify → Gate → STOP
RAG-Task → TaskSpec → Builder → Evaluator → Verifier
→ Evidence Gate → Regression Gate → STOP
```

Agent hard constraints (enforced by gates, not trust): no scope self-expansion
(scope gate); no "done" without evaluator PASS (test + evidence gates); frozen
artifacts read-only (fingerprint gate); registry-only models (policy gate);
retrieval gain never reported as answer correctness; fluency never scored as
correctness (answer-eval rung, §14).

## 16. Runtime architecture placement

`RAG-RUNTIME-ARCHITECTURE.md` + `runtime-schema.json` stay PASS and untouched.
Placement on the master roadmap: **P3–P4** (runtime model selection, multi-host
optimization) — strictly after correctness/grounding (P0), retrieval quality (P1),
and answer engine (P2). Rationale: planner optimization over an ungrounded answer
engine optimizes the wrong objective. No Host/Model Registry work starts now.

## 17. Priority roadmap

```text
P0 correctness / grounding:  M2 G1–G5, A1–A4, §9 citation contract
P1 retrieval quality:         M1 integration, E05-D-D successor benchmark (authorized separately)
P2 answer engine:             M3–M7, §13–§14 eval ladder
P3 runtime model selection:   runtime arch P0–P2 (profiles, registry, planner dry-run)
P4 multi-host optimization:   runtime arch P3 + per-host plans
P5 Composer / Console:        composition + observability UI
```

Ordering key: legal answer correctness > evidence fidelity > grounding > abstention >
retrieval quality > reproducibility > runtime performance > multi-host > convenience >
Composer/Console.

## 18. Proposed implementation batches (next first)

- B1: wire judgement retrieval+answer into a non-default serving path behind an
  explicit flag; Evidence Gate on citations; Regression Gate on statutes path.
- B2: mechanical grounding verifier (G1–G5) + abstention hardening (A1–A4).
- B3: contradiction + currency + contamination guards (M3–M5).
- B4: evidence/answer eval ladder fixtures (§13–§14); `evals/` to its 50-question bar.
- B5+: runtime registry/planner per `RAG-RUNTIME-ARCHITECTURE.md` phases.
Each batch separately authorized; each ends STOP.

## 19. Explicit non-goals (this audit and the roadmap)

No production edits; no frozen-artifact changes; no re-benchmarking; no model
downloads; no smoke tests; no Qdrant writes; no registry/planner implementation;
no Composer/Console UI; no policy or `opencode.json` changes.

## 20. Risks

- R1 fluent-but-ungrounded answers shipping as "correct" (mitigation: B2 verifier gate).
- R2 judgement integration silently changing statutes behavior (mitigation: flag +
  regression gate).
- R3 currency errors presented as current law (mitigation: B3 doctrine + eval probes).
- R4 benchmark pressure re-running frozen experiments (mitigation: fingerprint gates;
  this audit re-verified all eight).
- R5 test-suite signal rot: full-suite collection currently errors under system Python
  (missing deps; project venv is the supported runner — 734/734 judgement tests pass
  there). Not fixed here (out of scope), recorded so a later batch owns it.

## 21. Acceptance criteria (for the roadmap itself)

Repository state verified from files (not descriptions); every answer-layer claim
classified with test/code evidence; retrieval≠answer gap explicit; grounding and
abstention stated as engineering requirements; priorities justified by the §17 key;
zero production/frozen changes; gate below computed from validators.

## Decisions / observations

- D1 answer engine (P0–P2) precedes runtime optimization (P3–P4).
- D2 frozen retrieval lineage is evaluation infrastructure — cite, never re-run.
- D3 E05-D-D successor benchmarking is a P1 item requiring separate authorization.
- OBS-1 brief text typo (E05-A fingerprint missing one `b`) — artifacts correct, no action.
