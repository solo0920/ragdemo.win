# B3-D — Judgment Statute Attribution and Court-Use Boundary

Status: **implemented + gated — deterministic, no LLM, no applied/decisive
inference.** Terminal: PASS → STOP (B4-C not started).

## 1. Motivation and audit answer (§3)

B4-B-F1's equivalence run dropped 民事訴訟法第436-18條 under attribution
enforcement. Audit verdict: **option 3 — genuinely ambiguous from
deterministic evidence.** The citation sentence carries no B3-A speaker
marker (R-UNKNOWN) and no B2-C node covers its span, so no deterministic rule
can establish court voice; exclusion under enforcement is policy-correct
(fail closed) but substantively a recall gap (a human reads the sentence as
court procedural explanation). Forcing it in would be recall invention;
weakening rules to catch it is forbidden. The sentence cites no party,
prosecutor, or quoted source — it is unresolved, not misattributed.

## 2. Available temporal metadata — unchanged (B3-B HOLD stands)

## 3. Temporal contract — unchanged

## 4. Validity/text semantics — unchanged, carried per relation

## 5. Rules T1–T8 — unchanged

## 6. Citation attribution contract (§4)

`backend/app/b3d_attribution.py`: every citation binds to
citation_id/judgement/chunk/span, raw/normalized text, corpus resolution +
evidence id, attribution status + rule id + source span, covering node,
temporal status (from map), downstream eligibility
(`court_used` + `display_as`), and provenance (covering node + sentence
rule). Computed from real spans and real B3-A rules — never from names,
numbers, or similarity.

## 7. Reviewed temporal set — unchanged (B3-B fixture green)

## 8. Evaluation metrics (§9, B3-D-EVIDENCE.json)

Reviewed 9/9 match; court precision 3/3; false exclusions/inclusions 0.
Corpus-wide (1023 citations): court-used 126, PARTY 45, PROSECUTOR 21,
QUOTED_OTHER 41, QUOTED_STATUTE 237, UNRESOLVED 553 (54% — fail-closed
price, reported not hidden); party/quoted-as-court 0/0. Claim eligibility:
A4b gate unit-pinned.

## 9. False historical claims — still 0 (no historical claims added)

## 10. Temporal-invalid handling — unchanged (exclusion + note, tested)

## 11. B2-D integration

`build_answer` computes relations for every citation, filters to court-used
only under enforcement (legacy direct path byte-identical), exposes
`statute_attributions` + `attribution_enforced` on the response, and the A4b
gate rejects non-court statutes in enforced responses. Answer prose
unchanged: the existing header now provably means court-used. Prose
templates for mention/prosecution display are follow-up (needs product
wording authorization).

## 12. B3-A interaction

B3-A rules untouched (read-only import). Sentence-level speaker evidence
overrides covering nodes (2 real payment-order citations corrected);
R-UNKNOWN/R-COURT never override; R-PROSECUTOR maps to the distinct
prosecutor status (not collapsed into UNRESOLVED at this layer).

## 13–16. Regression / limitations / follow-ups / terminal

- Full suite + validators green → validation report.
- L1 54% corpus UNRESOLVED (precision-first). L2 436-18-class procedural
  sentences without markers stay unresolved (recall gap, recorded).
- L3 applicant-request coloring (狀請鈞院) uncaptured by any rule (D-08b
  accepted as reviewed limitation). L4 mention-only display prose is future.
- Follow-ups → B3-D-FOLLOWUPS.md. Terminal: PASS → STOP.
