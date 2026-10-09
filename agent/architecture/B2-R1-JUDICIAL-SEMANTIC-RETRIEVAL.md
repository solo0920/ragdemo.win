# B2-R1 — Judicial Semantic Retrieval (Question → Correct Judgment)

Status: **evaluated + decided — no serving path changed.**
Terminal per candidate: B → REJECT · C → HOLD · Hybrids → REJECT.
Batch terminal: **HOLD → STOP.** B2-B NOT started.

Product rule (binding): optimize Question → Correct Judgment → Judgment
Citations → Exact Statute → Evidence → Answer. This batch covers only the
first arrow, at judgment level with chunk provenance intact.

## 1. Product objective

Determine whether a semantic vector path retrieves the correct real judgment
for a real legal question more reliably than the BM25 reference — measured at
judgment level (the product unit), with Rank-1 safety, provenance, live
equivalence, and operational feasibility weighed together.

## 2. Architecture

Chunk retrieval (frozen corpus, 430 records) → deterministic judgment
aggregation (`max`, tie-break by judgement_id; `top2mean` evaluated and
dropped) → judgment ranking with supporting chunks → optional deterministic
RRF hybrid → Rank-1 audit. Serving code added: `backend/app/b2r1_rank.py`
(pure, tested); experiments isolated in `~/projects/b2r1/`.

## 3. Baseline (Candidate A — BM25, cited frozen, plus new judgment view)

Frozen chunk numbers (cited, untouched): R@1 0.38 / R@20 0.8133.
New judgment-level view over frozen top-20 ranks (same N=20 cutoff as all
candidates): R@1 **0.6316**, R@20 0.8553, MRR 0.7127; rank-1: 48 TARGET /
21 WRONG / 7 UNKNOWN. Judgment aggregation rescues same-doc HN traps that
bite at chunk level (frozen chunk HN@1 0.0395 → judgment HN@1 0.0).

## 4. Candidate retrievals

- **B (EmbeddingGemma2)**: Google vendor (allowed), pinned rev `914f7f89…`,
  official query/document APIs, batch doc=1 (16GB VRAM), det probe 0.0,
  fresh-process order reproduction 3/3 exact.
- **C (Snowflake Arctic m-v2.0)**: vendor Snowflake US re-verified this batch
  (live HF API: id/sha/license Apache-2.0/public-ungated all match the D-D
  record), pinned rev `95c27414…`, CLS + `query: ` prefix, det 0.0.
- Judgment-level max aggregation, fair N=20 cutoff for all:
  B R@1 0.4737 (36/36/4) · C R@1 **0.5789** (44/25/7, 1 HN) · hybrids R@1 0.5658.
- Paired strict-TARGET: A∩C differ on 20 queries (12/8, McNemar p≈0.37 —
  noise); A∩B differ 17/5 (p≈0.01 — B significantly worse).

## 5. Model/index identities

Per-model `index_id` (`b2r1-4d7732982687e36b` for B, `b2r1-8bc23df5d3af540e`
for C) binding model/revision/snapshot-fingerprint/dim-768/cosine/frozen
chunking/prompt/index config. No index shared across models. Full records in
B2-R1-EVIDENCE.json.

## 6. Chunking and embedding configuration

Frozen E05-B-R1 corpus (430 records) unchanged. B: batch-1 (batch-8 OOMs on
long chunks — same constraint as frozen D-B). C: batch-16 docs, bf16.
Methodology correction recorded: sentence-transformers `normalize_embeddings`
leaves norms at 0.9967–1.0039, so pure dot product is systematically off by
few-e-3; all reported numbers use TRUE cosine (float64, norm-dividing).
An initial dot-based run was discarded and recomputed — numbers below are the
corrected ones.

## 7. Judgment aggregation

`max` selected (C 0.5921 vs top2mean 0.5 full-ranking; B tie → simpler wins).
Deterministic, explainable, tested (9 unit tests); no LLM, no query-specific
weights, no hard-coded judgments.

## 8. Metrics (judgment-level, strict target-support definition)

| candidate | R@1 | R@5 | R@10 | R@20 | MRR | nDCG@10 | HN@1 |
|---|---|---|---|---|---|---|---|
| A BM25 | 0.6316 | 0.7895 | 0.8553 | 0.8553 | 0.7127 | — | 0.0 |
| B | 0.4737 | 0.7368 | 0.7763 | 0.7895 | 0.5933 | — | 0.0 |
| C | 0.5789 | 0.6711 | 0.7368 | 0.7500 | 0.6321 | 0.7152* | 0.0 |
| H+B / H+C | 0.5658 | 0.80/0.78 | 0.84 | 0.86/0.84 | 0.666/0.660 | — | 0.0 |

(*nDCG@10 computed for C full-ranking; judgment-level definitions are NOT
comparable to frozen chunk-level numbers — different units, stated explicitly.)

## 9. Rank-1 audit

Full per-query tables in B2-R1-EVIDENCE.json (N=20 view) and experiment
outputs. Dominant mode remains wrong-judgement at rank 1 for every candidate;
C closes roughly half the BM25 gap (25 vs 21 wrong) but does not cross it.

## 10. Hard-negative analysis

Judgment-level HN@1/5/10 = 0.0 for all candidates (strict: HN support without
target support in top-3 supporting chunks). Same-doc traps that score as chunk
HN hits are absorbed when the expected judgement surfaces with target support
— a genuine product property of judgment-level ranking, not hidden: chunk
HN@1 stays 0.0395 frozen and is still reported. No HN entries removed.

## 11. Offline/live comparison (§14 — diagnosed, then satisfied)

First attempt DIVERGED (76/76 order mismatches) → diagnosed per §14, not
declared: (a) Qdrant engine exonerated (stored-vector self-test exact);
(b) embeddings exonerated (fresh-process order reproduction 3/3);
(c) root cause isolated to my offline math — pure dot on loosely-normalized
vectors vs Qdrant's true cosine (proven: true cosine reproduces live scores to
6 decimals). After correction: **0/76 chunk-order mismatches for B and C**
(temp Qdrant exact search, own container/volume/port, torn down and verified
gone; serving collections never touched). Judgment order follows deterministically
from identical chunk orders (same tested function). Residual documented
difference: production would use HNSW-approximate, tested exact — HNSW recall
measurement is follow-up F3.

## 12. Performance (measured, x570 RTX 5070 Ti 16GB)

C: 152 docs/s, 76 queries in 0.49 s, peak 5.06 GB. B: 31 docs/s (batch-1),
peak 5.25 GB. Both fit 16 GB with headroom; C is the operational standout.
Index size: 430×768 float32 (~1.3 MB) — trivially portable to all four hosts'
budgets as data (compute is the constraint, measured).

## 13. Regression results

Full suite green (see validation report); B1 demo test green (demo judgement
outside frozen corpus — unchanged status, no hard-coding); 8/8 frozen
fingerprints match; no frozen writes; no serving-path behavior change
(nothing promoted); validators green.

## 14. Promotion decision

- **B → REJECT** (promotion): significantly worse Rank-1 than BM25 (paired
  p≈0.01). Experiment stands as a valid reference only.
- **C → HOLD**: works, operationally excellent, statistically tied with BM25 —
  superiority unproven. BM25 stays the reference path. Remaining evidence:
  larger query sample, live serving-path numbers, significance.
- **Hybrids → REJECT**: RRF 0.5658 < BM25 0.6316 at Rank-1 — evidence stopped
  improving, complexity stops here.
- **Aggregation max**: adopted as the tested rule for future batches.

## 15. Limitations

L1 n=76 (paired gaps mostly non-significant). L2 N=20 cutoff inherited from
frozen list length. L3 live test used exact search on temp data, not serving
HNSW. L4 no query normalization anywhere (finding, not fix). L5 demo case
outside frozen corpus.

## 16. Follow-ups → B2-R1-FOLLOWUPS.md

## 17. Terminal state

**HOLD → STOP.** No serving change, no B2-B. The evidence anchor question —
"can the system reliably locate the correct judgment" — is answered: BM25
reference at judgment R@1 0.6316 with 21 wrong-judgement failures remaining;
no candidate earns promotion today.
