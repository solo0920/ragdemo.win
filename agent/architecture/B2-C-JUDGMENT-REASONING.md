# B2-C — Judgment Holding & Reasoning Evidence

Status: **implemented + gated — extractive only, no answer generation.**
Code: `backend/app/b2c_evidence.py` (new). Tests: `tests/test_b2c_*.py`
(structure/safety/chain). Fixture: `tests/fixtures/b2c_reasoning_set.json`
(8 reviewed cases, 16 nodes).

## 1. Product objective

Make the court's own decision and reasoning mechanically traceable: what was
decided (DISPOSITION), what was concluded (HOLDING), and which passages state
the reasons (REASONING) — all verbatim with chunk spans, feeding B2-D later.

## 2. Evidence architecture

Per-chunk extraction (disposition → holding → reasoning) → document graph with
weak relations (same_chunk, contains_citation, same_document) → G1–G8 gate.
B2-B citations plug in as citation nodes; statute evidences attach through the
unchanged B2-B gate. No causal/applied/decisive relation exists anywhere.

## 3–5. Definitions (enforced, not aspirational)

- DISPOSITION: 主文-headed span (line-start heading rule; in-text references
  like 裁定如主文/如主文所示 rejected — caught by fixture review).
- HOLDING: conclusory sentences from an explicit pattern list, flagged
  heuristic with the matched pattern; disposition territory excluded.
- REASONING: court-voiced sentences (explicit marker list + boundary rules for
  查/按/核) in REASONING/FACTS chunks, party-voice excluded (辯稱/主張/聲明/
  抗辯/答辯/辯護/上訴意旨/等語 — 等語 added after review caught an applicant
  restatement), QUOTED_* roles never sources.

## 6. Extraction strategy

Simplest trustworthy order: structural markers → pattern sentences → provenance
attach. No learned parser, no LLM. Three real bugs found by fixture review
before freezing (reference-vs-heading, anchor ordering, 等語) — the review
process is the method, recorded here as evidence it works.

## 7. Verification set

8 cases / 16 nodes, all real data (frozen corpus chunks + B1 demo excerpts):
dismissal header, home-violence reasoning, flagged payment order, full
3-chunk detention doc, constitutional review, demo disposition, demo holding,
appendix negative. Built by generator, MANUALLY reviewed node-by-node
(15-node read + delta re-review); tests assert exact equality.

## 8–9. Metrics & provenance validation (B2-C-EVIDENCE.json)

Extraction P/R/F1 1.0 (16/16 — regression value; discovery value was the
review), span validity 16/16, provenance 16/16, incomplete flags 0,
gate recomputation 8/8 match. Honest reading: the set is small; precision is
demonstrated on reviewed cases, recall bounds are NOT proven (L-section).

## 10. Reasoning gate (G1–G8)

Judgement/chunk provenance (G1–G3), verbatim-in-source (G4), continuation
flags pointed (G5), no generated text (G6), known types only (G7), no
applied/decisive relations or labels (G8). All PASS on the set; all failure
modes unit-tested with injected violations.

## 11. B2-B integration

contains_citation edges verified live (R-02: 4, R-07: 4) after fixing a
chunk-id-convention mismatch found during integration (text+span containment,
not string equality). B2-B suite green, B2-B set untouched.

## 12. B1 real-case result

Demo chain complete: 主文 DISPOSITION + 難認有據/為無理由 HOLDINGs +
court-voiced REASONING + B2-B statute links, all gated. Nothing hard-coded
(normal pipeline on excerpt chunks).

## 13. Regressions → validation report.

## 14–16. Limitations / follow-ups / terminal

- L1 small verified set; L2 holding/disposition boundary is heuristic where
  patterns overlap (structure wins ties); L3 cross-chunk continuation detected
  only for dispositions; L4 reasoning recall unmeasured (precision-first by design).
- Follow-ups → B2-C-FOLLOWUPS.md. Terminal: PASS → STOP (B2-D not started).
