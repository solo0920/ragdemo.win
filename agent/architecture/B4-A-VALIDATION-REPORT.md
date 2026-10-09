# B4-A Validation Report (Verifier pass — independent re-check)

Verdict: **PASS → STOP.** B4-B NOT started.

## The live-serving bypass that was found

`serve_live` never passed `enforce_continuity` (and never accepted
`require_statutes`, which the `/judgments/answer` route was already sending —
every live call raised TypeError → HTTP 500). The gate existed but the
production path bypassed it entirely.

## The exact fix (`backend/app/b2d_answer.py`, 1 edit)

`serve_live` now accepts `require_statutes: bool = False`, forwards it, and
passes `enforce_continuity=True` alongside the four existing enforcements.
`serve_question` defaults unchanged (`enforce_continuity=False`), preserving
all direct-call contracts pinned by prior suites.

## How the endpoint was proven to enforce continuity

- Unit: `serve_live` monkeypatched-`serve_question` capture asserts
  `enforce_continuity is True` plus all four prior flags.
- Route: `/judgments/answer` handler stubbed-`serve_live` capture proves
  `require_statutes` forwarding (full import needs fastapi; additionally
  proven live in backend venv: route registered, `/query` intact,
  forwarding `True`).
- Behavior: material UNKNOWN → INSUFFICIENT_CONTINUITY_EVIDENCE with
  `answer is None`; gate FAIL → `build_answer` call-count stays zero;
  honest single-chunk path not blocked.

## Source-continuity and attribution test results

- Assemble/gate/chain/serve: 25 tests green (ordering, gaps, documents,
  spans, attribution splits, type splits, remap, M1–M10, reviewed-set
  equality, disposition completion verified/unverifiable, provenance).
- Evidence evaluator: 5/5 reviewed assemblies recompute exactly; corpus
  census 74 docs / 73 multi-chunk / 356 contiguous adjacencies; 0 false
  concatenations; 0 attribution contaminations.

## Full-suite and validator results

- Full suite: **1624 passed, 119 skipped, 0 failed** (1598 pre-existing +
  25 B4-A tests + 1 hygiene param).
- Authority: PASS (15 components) · Adoption: PASS (B4-A manifest covers
  new paths) · Runtime arch: PASS (no new weights; env untouched).
- Frozen lineage: 8/8 fingerprints match; zero frozen/Qdrant/download/policy
  violations; B2-F/B3-B HOLDs unaltered.

## Remaining limitations

Per main report L1–L3 (split citations, chunk_id convention, 500-char cap).
No silent flattening exists: M10 rejects multi items without span provenance.

**PASS → STOP.**
