# B2-F Validation Report (Verifier pass — independent re-check)

Verdict: **HOLD → STOP.** B3 NOT started.

## Suite results (project venv, fresh runs)

- Full suite: **1544 passed, 119 skipped, 0 failed** (1527 pre-existing +
  16 B2-F tests + 1 hygiene param for the new module).
- B2-F files: 16/16 (7 confidence units, 4 serve-integration, 5 eval-freeze).
- B1 (26), B2-A (15), B2-R1 (9), B2-B (25), B2-C (16), B2-D (21), B2-E (5)
  suites green — B2-D default path untouched (gate defaults off, pinned).
- Endpoint import verified in backend venv (routes intact; sensitive-paths green).
- Pre-existing failures: none.

## Validators

- Authority (15 components): PASS · Adoption: PASS (provider scan clean incl.
  all new B2-F docs/json/fixture; production entries covered by B*-CHANGES) ·
  Runtime arch: PASS (model-weights check — Snowflake reuse read-only,
  no new downloads in-repo; `opencode.json`/env untouched).

## Frozen lineage (re-read, never recomputed)

Golden/harness/D-A/D-B fingerprints all MATCH; zero writes to
`~/artifacts/*`. B2-A/B2-R1/B2-E evidence read-only inputs, untouched
(their suites re-green).

## Regression requirements (§25, each verified)

- All batch suites green (above); ranking untouched (no retrieval file in
  diff); no model added (gate is arithmetic); no Qdrant writes (no Qdrant
  contact); no downloads; no policy/`opencode.json` change; no
  blocked-origin strings; no `agent/` imports.

## §28 decision record

Mechanism verified but operating point too narrow (10%/3% coverage at
precision 1.0; vector path ~non-operational; n=76). HOLD is honest:
technically correct gate, trustworthy operating point NOT established.
**HOLD → STOP.**
