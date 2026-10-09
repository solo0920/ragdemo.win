# B3-A — Quoted Contamination & Evidence Attribution

Status: **implemented + gated — deterministic, no LLM, no model.**
Code: `backend/app/b3a_attribution.py` (new) + narrow B2-D enforcement
(attribution map, citation span-bridging, `enforce_attribution` flag).
Tests: `tests/test_b3a_{attribution,gate,serve}.py` (16).
Fixture: `tests/fixtures/b3a_attribution_set.json` (12 reviewed passages).

## 1. Motivation

B2-E proved grounded answers from wrong judgments; B3-A closes the adjacent
hole: passages that merely APPEAR inside a judgment (party claims, quoted
prior courts, reproduced statutes, procedural boilerplate, prosecutorial
filings) must never enter answers as the current court's reasoning.

## 2. B2-F HOLD state (unchanged, not revisited)

Confidence gate stays as calibrated (HOLD: precision-1.0, narrow coverage).
Attribution applies after confidence ACCEPT; no threshold touched.

## 3. Attribution model

Six statuses; only COURT_VOICE supports material court claims. Ordered rules
R-DISP (structural disposition) → R-PARTY (incl. section framing, colon-bound
意旨 forms, 意思表示 guard) → R-PROSECUTOR (operative prosecutorial voice) →
R-QUOTE (substantial quotation, content-subclassified) → R-PRIOR (other-court
reference without own court markers) → R-PROC → R-STATUTE (opens-with-article
+ no court markers) → R-COURT (local markers minus bare 爰/爰以) → R-DOC
(prosecutorial-document cap) → R-UNKNOWN (fail closed).

## 4. Contamination taxonomy (C-01..C-07, all with real corpus cases)

PARTY_ARGUMENT / QUOTED_PRIOR_JUDGMENT / QUOTED_STATUTE /
PROCEDURAL_SUMMARY / COURT_REASONING / ADOPTION-if-provable (unused: no
adoption inference exists) / UNRESOLVED. Endorsement-mixed sentences
(原告主張…尚屬有理) fail closed as PARTY (documented limitation).

## 5. Extraction/classification rules

Above; every verdict carries rule_id + reason + (for R-DOC) framing evidence.
No similarity, no LLM, no adoption inference.

## 6. Verified contamination set

12 real passages: 3 party (incl. holding-language + prior-cite nesting),
2 prior-judgment quotes/cites, 2 statute reproductions (appendix + ConCourt
quote), 2 procedural, 1 clean court voice, 1 endorsement-mixed (party),
1 prosecutorial assessment (unresolved). Built by generator, MANUALLY read;
4 rule bugs found by review/mismatch analysis and fixed before freezing
(anchor ordering, 之-branch order, R-STATUTE overreach ×2, 意旨-colon bound).

## 7–8. Attribution metrics / contamination metrics (B3-A-EVIDENCE.json)

Verified: 12/12 correct, contamination 0/0/0, unresolved 1 (U-01, by design).
B2-C 16 nodes re-attributed: 16 COURT_VOICE after fixing 2 false positives
found by this integration (payment-order body, 憲訴59 reasoning) — both
corrected by rule tightening, not by exception.
Corpus profile (2801 sentences, unlabeled counts): 59% UNRESOLVED —
conservative by design; recall bounds unknown (limitation).

## 9. Unresolved attribution

Fails closed everywhere (gate Q5, composer exclusion, missing-map default).
59% corpus rate is the honest price of no-inference attribution.

## 10. B2-D integration

`build_answer(attribution=...)` excludes non-COURT nodes from claims AND drops
statutes cited only by excluded passages (convention-bridged span check);
missing map entries fail closed; `serve_question(enforce_attribution=...)`
computes maps over graph nodes (default off — prior suites pin default);
`serve_live` enforces. Bypass tests: refused graph → abstain; all-refused →
abstain.

## 11. B2-B interaction

Citation behavior untouched (B2-B green). EXPLICIT_CITATION ≠ COURT_VOICE ≠
APPLIED/DECISIVE — three separate namespaces, tested.

## 12. Regression results → validation report.

## 13–15. Limitations / follow-ups / terminal

- L1 section-level party scoping beyond ±120 chars uncovered.
- L2 endorsement-mixed sentences fail closed as PARTY (nuance lost, safety kept).
- L3 59% unresolved on raw corpus (precision-first).
- L4 adoption relation unmodeled (by design).
- Follow-ups → B3-A-FOLLOWUPS.md. Terminal: PASS → STOP (B3-B not started).
