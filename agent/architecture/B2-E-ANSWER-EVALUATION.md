# B2-E — Judicial Answer Evaluation & Abstention

Status: **measurement-only — no product code changed for evaluation.**
Terminal: **PASS → STOP** (B3 not started).

## 1. Objective

Answer experimentally: does the pipeline either produce a faithful
evidence-grounded answer from the correct judgment, or abstain when evidence
is insufficient — across a broader reviewed set including abstention cases.

## 2. Evaluation architecture

Reviewed set (10 real cases) → deterministic rebuild (retrieval result frozen
for retrieval-sourced cases; evidence+answer recomputed live) → automated
structural metrics C1–C9 → human H1–H8 verdicts → matrix → decision. No LLM,
no services, no re-retrieval at eval time.

## 3. Reviewed dataset construction

- E-01/E-02: B2-D reviewed answers, rebuilt independently (byte-identical).
- E-03/04/05: frozen BM25 rank-1 chains (GQ-001/002/005), chunks vendored.
- E-06/07: frozen BM25 wrong-judgment chains (GQ-020/035), chunks vendored.
- E-08/09: B2-D abstention patterns. E-10: refused-only excerpt (real PCDM).
- No synthetic legal questions; no invented gold answers. UNRESOLVED_STATUTE
  E2E proved unconstructible from real data (searched holdings×refused-only:
  none exist) — unit-covered only, explicitly recorded.

## 4–5. Positive / negative cases

Positive: E-01–E-05 ANSWERED (5). Negative: E-08/09/10 abstained with correct
reasons (3). Retrieval-failure probes: E-06/07 ANSWERED-but-H1-FAIL (2).

## 6. Human review rubric (H1–H8, stamped in B2-E-EVIDENCE.json)

Reviewer read all 7 answers fully + checked abstentions against contract.
8/10 overall PASS (5 faithful + 3 correct abstentions); 2 FAIL both H1-only
with faithful answers (understood retrieval failures, bounded, C3 domain).

## 7. Automated metrics

C1 87/87 supported · C2 0 unsupported (required zero holds) · C4/C5/C6
structural 0 (gate-enforced) · C7 0 provenance failures · C8 0 (no
answered-where-evidence-insufficient; wrong-selection cases live under C3 by
definition) · C9 0 (all abstentions contract-correct).

## 8. Per-case matrix

In B2-E-EVIDENCE.json `answer_matrix`: per case judgment_correct,
status, H1–H8, overall. No dimension hidden: E-06/E-07 show H1 FAIL + H7 FAIL
with H2–H6 PASS.

## 9. Failure analysis

Only failures: E-06 (TA QUANG SAM vs expected) and E-07 (PCDM vs KSHM) —
both rank-1 wrong-judgment retrieval errors; answers faithful to SELECTED
evidence (31/31 and 8/8 claims grounded). Layer attribution: retrieval
(B2-R1/B2-A domain), not evidence, statute, or answer. No answer-layer defect
found in 10 cases.

## 10. Abstention analysis

3/3 abstentions correct with exact reasons; 0 false negatives (nothing
answered on insufficient evidence); 0 false positives (no sufficient case
abstained). Refused-only zones (刑法) demonstrably cannot anchor claims.

## 11. Reproducibility fingerprint

evaluation_set sha, answer_set sha, git HEAD, metric refs, timestamp — all in
B2-E-EVIDENCE.json `reproducibility`. Set changes alter the fingerprint by
construction (test pins it).

## 12. Regression results → validation report.

## 13–15. Limitations / follow-ups / terminal

- L1 n=10 (bounds, not a certificate); L2 single-judgment scope;
  L3 UNRESOLVED_STATUTE E2E gap (unit-covered); L4 retrieval-confidence gating
  is the structural C8/C3 gap (pipeline cannot detect wrong selection).
- Follow-ups → B2-E-FOLLOWUPS.md. Terminal: PASS → STOP.
