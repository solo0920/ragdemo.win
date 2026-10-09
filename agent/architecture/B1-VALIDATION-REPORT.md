# B1 Validation Report — Judicial Retrieval + Evidence-Grounded Answer

Verdict: **PASS** (all acceptance criteria A–L hold; evidence below).
Method: Builder implemented; this report records the independent Verifier re-check
(fresh runs, file evidence, no trust in Builder assertions).

## Acceptance evidence

- A (real judgement retrievable): integration test builds REAL T015/T017 payloads
  on the REAL vendored JFULL (`STEV,115,店小,450,20260713,1`, 3137 chars, source:
  E03 extracted work area) and drives the full chain through the `search_fn`
  boundary. Live vector-DB population is follow-up F2 (needs embedding-model
  authorization + Qdrant ops); nothing was fabricated to fake it.
- B (court/number/date/source/evidence): number (JID opaque, FR-028), date, source,
  offsets, verbatim quotes — asserted in test_b1_serving_demo. `court` is None
  by spec decision FR-016 (recorded, not worked around).
- C (judgement-cited statute evidence): 民法184/195, 民訴78/255/436/436-18,
  刑訴503 — all corpus-verified; 之-form→hyphen mapping flagged per evidence.
- D (claims have evidence refs): every claim carries evidence_ids; gate verified.
- E (citation backtrace): citation contains JID/date/offsets; quote == JFULL slice.
- F (abstention): Cases A–D + invalid input, all `insufficient_evidence`/`answer:null`.
- G (mechanical gate): 26 B1 tests incl. 7 negative gate cases + item-7 prose injection.
- H (statutes regression): full suite 1431 passed, 0 failed (incl. worker
  sensitive-paths: new endpoint added to SENSITIVE per repo guardrail).
- I (judgement tests): all pre-existing judgement tests still pass — selector
  `-k 'judgement or judg'` → 735 passed (734 pre-existing + 1 B1 test whose
  name contains 'judgement'), 4 skipped, 0 failed.
- J (frozen experiments): 8/8 fingerprints re-verified from files (see below).
- K (no downloads): HF cache untouched; no download commands in new files
  (runtime validator check 6 PASS).
- L (no blocked providers): B1 path calls no model at all (extractive);
  `serve_live` defaults to the existing validated runtime config; provider scan PASS.

## Frozen fingerprints (re-verified this batch, file evidence)

E03 `3c9e102e…651000ab24d` · E05-A-R1 `5a567299…bdb8918d` ·
E05-B-R1 `c6e41fe2…777d65108c93` · Golden `58e01c79…339e7f2c` ·
Harness `ec30e8fd…` + corpus `893d40b6…` · D-A exp `45a87afa…` + model
`34af6022…` + index `2ac93716…` · D-B exp `4f4ddacc…` + model `9c1ab1c9…` +
index `282a0bd4…` · E05-D-D record sha256 `4cc062c1…` — all MATCH.

## Validators

- Authority (15 components): PASS · Adoption: PASS (check #7 made
  batch-manifest-aware; B1-CHANGES.txt pins this batch's production footprint) ·
  Runtime arch: PASS (boundary check likewise manifest-aware).
- `opencode.json` + provider env untouched; no Qdrant writes performed.

## Production footprint (== B1-CHANGES.txt)

New: `backend/app/b1_serve.py`, `tests/b1_helpers.py`, `tests/test_b1_*.py` (5),
`tests/fixtures/b1_judgement_serving/case_00450.json`.
Modified: `backend/app/main.py` (+import, +`POST /judgments/query` only),
`frontend/.../+server.ts` (+`'judgments'` in SENSITIVE, required by
test_worker_sensitive_paths).
`/query` and all statutes behavior byte-unchanged.

## Gate result: PASS → STOP (B2 not started)
