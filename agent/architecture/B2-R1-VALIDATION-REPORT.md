# B2-R1 Validation Report (Verifier pass — independent re-check)

Verdict: **HOLD → STOP** (decisions: B REJECT · C HOLD · Hybrids REJECT).

## Suite results (project venv, fresh runs)

- Full suite: **1457 passed, 119 skipped, 0 failed** (1447 pre-existing +
  9 rank unit tests + 1 hygiene param for the new module).
- B2-R1 unit tests: 9/9 (aggregation determinism/tie-break, fusion, strict
  metrics, all rank-1 categories, index-identity separation).
- B1 tests 26/26, B2-A tests 15/15, judgement selector unchanged green.
- Pre-existing failures: none.

## Validators

- Authority (15 components): PASS · Adoption: PASS (provider scan clean incl.
  all new B2-R1 docs; production entries covered by B1/B2-A/B2-R1 manifests) ·
  Runtime arch: PASS (model-weights check: Snowflake pinned snapshot verified,
  google reuse read-only, prior-era smoke-test cache untouched; `opencode.json`/env untouched).

## Frozen lineage (re-read, never recomputed)

Golden/harness/corpus/D-A/D-B fingerprints all MATCH; zero writes to
`~/artifacts/*`. Frozen numbers cited, never regenerated.

## Regression requirements (§19, each verified)

- Suites green (above); statutes behavior untouched (no statutes-path file in
  diff); 8/8 fingerprints unchanged; no frozen writes; no Qdrant writes
  (temp container/volume/collections created AND destroyed — verified absent;
  serving Qdrant never touched); no production DB mutation; no `opencode.json`
  or policy change; no blocked-origin models (both evaluated vendors cleared;
  prior smoke-test cache untouched); no `agent/` imports into production.

## Methodology incident (closed, documented in main report §6/§11)

Offline/live first diverged → diagnosed to my dot-product sloppiness
(ST norms 0.9967–1.0039) → true-cosine recompute → **0/76 mismatches both
candidates**. Initial dot-based numbers discarded, never reported as results.

## §16/§17 decision record

Quality+Rank-1-safety: nothing beats BM25 R@1 (paired stats n.s. for C,
B significantly worse). Evidence: provenance intact, deterministic (det 0.0,
fresh-process order reproduction, live equivalence). Reproducibility: identities
frozen. Operational: C 152 docs/s @ 5.06 GB. → **B REJECT · C HOLD ·
Hybrids REJECT · max aggregation adopted.** Batch **HOLD → STOP.** B2-B NOT started.
