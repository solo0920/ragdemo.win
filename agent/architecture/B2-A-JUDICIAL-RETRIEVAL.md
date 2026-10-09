# B2-A — Judicial Retrieval Accuracy Gate

Status: **evaluation + gate complete — no retrieval rebuilt, no baseline recomputed.**
Method: read frozen files only (`b2a_analyze.py`); all analysis inputs are
frozen result artifacts. Terminal: PASS per §16 with one carried STOP (R3).

## 1. Objective

Establish, by measurement rather than demo impression, whether the judicial
retrieval layer is trustworthy enough to found the legal-answer layer:
can it retrieve the right judgement chunks for real legal questions, with
provenance intact and hard negatives quantified.

## 2. Existing baseline used (cited, never recomputed)

Frozen E05-C-B BM25 baseline (`ec30e8fd…`, run1==run2 verified bit-identical
from files) plus frozen D-A (`45a87afa…`) and D-B (`4f4ddacc…`) per-query result
files and the frozen three-way comparison. Cited aggregates:

| metric | BM25 | D-A embedding | D-B embedding |
|---|---|---|---|
| Recall@1 | 0.38 | 0.3244 | 0.24 |
| Recall@5 / @10 / @20 | 0.6733 / 0.72 / 0.8133 | 0.6089 / 0.6667 / 0.7533 | 0.62 / 0.6467 / 0.7733 |
| nDCG@10 | 0.6026 | 0.5540 | 0.5074 |
| MRR | 0.6483 | 0.6194 | 0.5403 |
| Hard-negative rate@1 | 0.0395 | (per-query table) | (per-query table) |

No new retrieval was executed: Qdrant is down in this environment and no live
`judgements` collection exists, so serving-path measurement is an explicit
limitation (F2), not a silently substituted proxy.

## 3. Dataset / query count

76 golden queries × 430 frozen corpus records (74 judgements), 114 targets
(HIGH 93 / PARTIAL 21), 40 hard negatives. B2-A added no queries and no corpus
records.

## 4. Retrieval path (as evaluated)

The frozen harness path (deterministic BM25 + brute-force embedding cosine over
the frozen corpus) is what the numbers describe. The serving path
(`search_judgments`: embed → Qdrant dense → deterministic sort) shares the
embedding/query semantics but its live behavior is UNMEASURED here. Finding:
the judgement path performs NO query normalization (raw question to embed).

## 5. Metrics (frozen semantics preserved)

Recall@1/5/10/20, MRR, nDCG@10, target/evidence coverage, hard-negative rate,
quoted-contamination, wrong-current-judgement — definitions taken verbatim from
the frozen harness; B2-A re-implements only recall_at_1/MRR thinly for unit
tests on synthetic data (same formulas, no frozen numbers involved).

## 6. Hard-negative results

Per-query HN-outranks-target at rank 1 (from frozen ranked lists): BM25 3/39
failures, D-A 3/42, D-B 3/49 — the same 3 queries across all retrievers (stable
traps, listed in B2-A-EVIDENCE.json). Cited HN rate@1: 0.0395 (BM25).
No HN entries were removed or softened; difficult queries kept.

## 7. Failure analysis (Recall@1, mechanical, from frozen ranked lists)

| category | BM25 (39) | D-A (42) | D-B (49) |
|---|---|---|---|
| wrong_judgement | 20 | 30 | 34 |
| target_below_cutoff | 12 | 5 | 7 |
| hard_negative_outranks_target | 3 | 3 | 3 |
| missing_judgement | 1 | 3 | 2 |
| unknown (evidence attached, low confidence) | 3 | 1 | 3 |

Dominant mode is wrong-judgement at rank 1, growing as Recall@1 falls — the
retrieval layer's central weakness, stated plainly. 7 unknowns carry evidence
and are NOT causally explained (no normalization/implementation blame without
proof). Full per-query table: B2-A-EVIDENCE.json `failure_tables`.

## 8. Real-case regression (§10)

B1 demo case (新店簡易庭115店小450號) remains covered by the green B1
integration test; nothing hard-coded it into retrieval logic. Observation: the
demo judgement is NOT among the frozen corpus's 74 judgements, so its
retrieval-accuracy evidence is adapter-level (real T015/T017 payloads through
the real chain), not corpus-measured. No action taken; recorded for B2-B+.

## 9. Reproducibility evidence

BM25 run1/run2 ranked lists bit-identical across 76 queries (read from frozen
files); frozen determinism probes recorded 0.0 diff; repo helpers are pure
functions with double-run determinism tests. Serving-path (Qdrant) reproducibility
is unmeasured — F2.

## 10. Gate results (R1–R5, no invented thresholds)

- R1 PASS — 76/76 queries identity-valid (all targets/HNs resolve in corpus;
  grades use the frozen HIGH/PARTIAL vocabulary). One recorded observation (not
  failure): GQ-002 C001 is both PARTIAL target and same-doc trap — accepted by
  the frozen 18/18 validator, no frozen rule forbids it, reported for
  answer-level batches.
- R2 PASS — frozen runs agree; metric semantics unit-tested.
- R3 STOP — no hard-negative threshold has been declared anywhere and this batch
  fields no candidate retriever; frozen reference rates recorded (HN@1 0.0395).
  Missing evidence: declared threshold + live-path measurement.
- R4 PASS — every Recall@1 failure (130 rows across 3 retrievers) classified
  with evidence; unknowns explicitly marked.
- R5 PASS — every frozen ranked record carries chunk_id + score; `document_id`
  null throughout frozen records is a recorded observation (chunk_id still
  identifies the doc; serving views carry full provenance, unit-tested).

## 11. Limitations

L1 no live serving-path numbers (Qdrant down, no collection). L2 no query
normalization exists to ablate. L3 R3 threshold undeclared. L4 unknowns (7)
unexplained by design. L5 demo case outside frozen corpus.

## 12. Follow-ups → B2-A-FOLLOWUPS.md (F1–F6)

## 13. Exact terminal state

**PASS** per §16 (evaluation valid, metrics reproducible, provenance preserved,
hard negatives measured, failures classified, B1 green, lineage unchanged) with
carried item **R3 = STOP** (threshold + live measurement still required).
B2-B NOT started.
