# B2-A Validation Report (Verifier pass — independent re-check)

Verdict: **PASS per §16, with carried R3 = STOP.**

## Suite results (project venv, fresh runs)

- Full suite: **1447 passed, 119 skipped, 0 failed** (1552 pre-existing
  collected + 15 B2-A tests + 1 repo-hygiene param case for the new module).
- B2-A files: 15/15 pass. B1 files: 26/26 pass. Judgement selector: 735 pass.
- Pre-existing failures: none (baseline before B2-A was already green).
- Note: `test_no_undefined_globals` auto-parametrizes per backend module, so
  `b2a_eval.py` is covered by repo hygiene with no extra test written.

## Validators

- Authority (15 components): PASS · Adoption: PASS (production entries newer
  than adoption markers are all pinned in B1/B2-A-CHANGES.txt manifests) ·
  Runtime arch: PASS (same manifest rule; `opencode.json`/env untouched).

## Frozen lineage (re-read from files, not recomputed)

Golden `58e01c79…` · harness `ec30e8fd…` · corpus `893d40b6…` · D-A exp
`45a87afa…` · D-B exp `4f4ddacc…` · D-A index `2ac93716…` · D-B index
`282a0bd4…` — all MATCH. Zero writes to `~/artifacts/*` (mtime check clean).

## Regression requirements (§12, each verified)

- Full suite / judgement tests / B1 tests green: yes (above).
- Statutes serving unchanged: no statutes-path file touched (`git status` clean
  outside manifests + pre-existing entries).
- No frozen regeneration, no Qdrant writes (Qdrant untouched; analysis is
  file-read-only), no downloads (HF cache clean per runtime validator),
  no policy/`opencode.json` change, no blocked-origin strings in new files,
  no production imports of `agent/` (grep clean).

## §16 PASS conditions

Evaluation valid · metrics reproducible (harness runs; serving path recorded
unmeasured) · provenance preserved · hard negatives measured · failures
classified (130/130 rows, 7 unknowns with evidence) · B1 green · lineage
unchanged — all hold. **PASS → STOP.** B2-B NOT started.

## Carried STOP (not blocking §16, blocking future retrieval changes)

R3: no HN threshold declared; no candidate retriever fielded. Required: product
threshold + live-path measurement (F1/F2 in B2-A-FOLLOWUPS.md).
