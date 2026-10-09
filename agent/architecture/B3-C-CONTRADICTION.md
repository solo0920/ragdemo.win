# B3-C — Evidence Contradiction Safety

Status: **detector implemented + gated — resolution never attempted.**
Code: `backend/app/b3c_contra.py` (new) + narrow B2-D wiring (pre/post
answer checks, `enforce_contradiction` flag). Tests:
`tests/test_b3c_{normalize,detect,gate,eval}.py` (22).
Fixture: `tests/fixtures/b3c_contradiction_set.json` (3 reviewed multi cases
+ 5 vendored multi-disposition docs).

## 1. Motivation

A grounded answer from contradictory evidence is a product failure even when
every claim is verbatim. B3-C detects conflict; it never picks winners
(no resolve/adjudicate API exists — test-pinned).

## 2–4. Taxonomy, semantics, attribution/temporal interaction

C-DISPOSITION/HOLDING/REASONING/STATUTE/ANSWER/METADATA/MULTI-JUDGMENT/
TEMPORAL/UNRESOLVED. Findings are DIRECT (rule-proven exclusive),
COMPATIBLE, or UNKNOWN. Only COURT_VOICE participates in court-contradiction
checks (party/quoted/unresolved never promoted). CURRENT_ONLY/UNKNOWN are
never contradiction evidence (assertion-tested).

## 5. Detection rules D1–D7

D1 same-judgment dispositions with normalized opposed outcomes
(DISMISSED×GRANTED only; split-frame/partly/remanded/appeal/criminal-UNKNOWN
never DIRECT). D2 same-chunk court holdings only. D3 answer claims vs
evidence (containment + normalized agreement). D4 citation-identity
invariant. D5 metadata identity. D6 multi-judgment opposed outcomes in one
context. D7 temporal excluded by assertion.

## 6. Severity model

MATERIAL (touches answer claims/selected outcome) → abstain; NON_MATERIAL →
logged; UNKNOWN → abstain iff material comparison required (D3 unknowns),
else logged. Gate: MATERIAL DIRECT→FAIL, MATERIAL UNKNOWN→ABSTAIN, else PASS.

## 7. Reviewed contradiction set

MC-01 real opposed pair (dismissal vs grant across courts) → DIRECT/FAIL;
MC-02 compatible pair → PASS; NC-01 single → PASS. No real same-judgment
DIRECT exists in corpus (courts self-consistent — finding, not gap);
synthetic-minimal fixtures prove the DIRECT machinery (labeled as such).

## 8–10. Detection/false-contradiction/escape metrics (B3-C-EVIDENCE.json)

Corpus-wide D1 scan: 5 docs, 5 pairs, **0 DIRECT** (false-contradiction
rate 0.0). Escape: synthetic tampered answers always rejected (unit-tested);
no resolve path exists to escape through.

## 11. Offline/live comparison

N/A as a vector comparison (pure rules). Offline evaluation and serving use
the identical code path (same `b3c_contra` functions); integration tests
exercise serve-level pre/post checks with stubbed retrieval.

## 12. Performance

Negligible (pairwise string rules over already-retrieved evidence). No VRAM,
no index, no model.

## 13. Regression results → validation report.

## 14–16. Limitations / follow-ups / terminal

- L1 criminal-sentence dispositions normalize UNKNOWN (sentence comparison
  out of scope) — recall gap, documented, never a false DIRECT.
- L2 cross-chunk holdings UNKNOWN (reference frame unprovable).
- L3 B2-D text-difference tripwire remains broader than B3-C (overlap
  documented; weakening it is a B2-D-owner decision, not B3-C).
- L4 multi-doc pairs beyond dispositions (e.g., holding-level) unmodeled.
- Follow-ups → B3-C-FOLLOWUPS.md. Terminal: PASS → STOP (B4 not started).
