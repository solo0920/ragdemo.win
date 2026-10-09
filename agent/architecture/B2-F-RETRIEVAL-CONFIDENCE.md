# B2-F — Judgment Retrieval Confidence Gate

Status: **implemented + calibrated + decided — ranking untouched, no model added.**
Code: `backend/app/b2f_confidence.py` (new) + minimal B2-D enforcement
(`serve_question` gate block, 2 abstention reasons, `serve_live` enforces).
Tests: `tests/test_b2f_{confidence,serve,eval}.py` (16).
Fixture: `tests/fixtures/b2f_confidence_eval.json` (76 reviewed rank lists).

## 1. Motivation from B2-E

B2-E proved faithful answers from wrong judgments (E-06/E-07): grounding
cannot fix selection. Rank-1 is a candidate; only sufficient retrieval
confidence permits it to become the evidence anchor.

## 2. Failure cases

B2-A/B2-R1 wrong-judgment rows (dominant mode: 20–34/76 chunk-R@1 failures);
E-06 (wrong person TA QUANG SAM) and E-07 (wrong court PCDM vs KSHM) as
mandatory probes; 3 HN@1 queries (GQ-001/008/076).

## 3. Confidence feature definitions

- margin (rank1−rank2, absolute, per-method): separates (OK med >> BAD med).
- identity (explicit 年度…字第…號 vs JID fields): REJECT on mismatch.
- REPORTED, never gating: top-5 concentration, top2/top3-same-doc (INVERTED),
  lexical coverage (saturated 0.909/0.909), cross-method agreement (weak),
  relative margin (0.243 vs 0.220, no room). Negative results kept on record.

## 4. Calibration methodology

Split declared before holdout (even-minus-GQ-020 vs odd-plus-GQ-020);
rule declared before holdout (max coverage at zero calibration wrong-accepts);
thresholds pinned in code (`b2f/v1`). No leakage: holdout numbers reproduced
exactly by the gate code path (not a parallel implementation).

## 5. Threshold/rule definition

`MARGIN_THRESHOLDS = {bm25: 38.742660905265, snowflake-C: 0.12242048428695307}`.
Unknown methods ABSTAIN (cannot judge). Malformed rankings ABSTAIN. Identity
mismatch REJECTS before margin logic.

## 6–10. Holdout results (39 queries)

| method | accepted | precision | coverage | wrong accepted | HN exposure |
|---|---|---|---|---|---|
| bm25 | 4 (003/019/031/073) | 1.0 | 0.103 | 0 | 0 (3 HN cases: 2 abstain, 1 reject) |
| snowflake-C | 1 (073) | 1.0 | 0.026 | 0 | 0 |

False rejects are many (by design: trustworthiness first). GQ-035/snowflake
abstains despite correct rank-1 (margin 0.0285 < 0.1224) — conservative cost,
documented, not tuned away.

## 11. Offline/live comparison

Not applicable as a live comparison (no serving index exists). The gate
function is ranking-source agnostic (ids + scores + query text); equivalence
between offline evaluation and serving use is by identical code path
(same `evaluate_confidence`), covered by integration tests with stubbed
retrieval at both high and low margins.

## 12. Performance

Gate cost is O(top-N) arithmetic over already-retrieved hits — negligible vs
retrieval/generation. No VRAM, no index, no model.

## 13. Regression results → validation report.

## 14. Deterministic behavior

Pure function; double-run equality tested; thresholds pinned; versioned.

## 15. Terminal decision: HOLD → STOP

Mechanism verified (precision-1.0 holdout both methods, mandatory probes
abstained, HN exposure zero, identity REJECT fires on real data, integration
enforced with bypass tests). But the calibrated operating point is too narrow
to call trustworthy-AND-useful (10%/3% coverage; vector path ~non-operational;
n=76 single corpus). Per §28 HOLD: technically correct, operating point not
established. No serving-rank change, B2-D composer untouched (default path),
live serving abstains (unknown-method) until a serving method is calibrated.

## 16–17. Limitations / follow-ups

- L1 margin thresholds are method-scale-specific; serving vector path
  uncalibrated (blocked-origin measurement avoided by policy).
- L2 n=76; L3 identity patterns cover 2/76 queries; L4 unknown-method
  abstention is blunt (all-or-nothing per method).
- Follow-ups → B2-F-FOLLOWUPS.md.
