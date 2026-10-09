# B2-D — Evidence-Grounded Judicial Answer

Status: **implemented + gated — extractive only, no LLM, no model introduced.**
Code: `backend/app/b2d_answer.py` (new) + `POST /judgments/answer` (thin honest
wrapper; live abstains until store/roles wired). Tests: `tests/test_b2d_*.py`
+ `tests/b2d_helpers.py`. Fixture: `tests/fixtures/b2d_answer_set.json`
(4 reviewed questions).

## 1. Product objective

Answer real legal questions from the authorized chain only (B2-R1 judgment →
B2-C graph → B2-B citations → exact statutes), with every material claim
traceable and abstention where evidence is insufficient.

## 2. Complete evidence architecture

serve_question (B2-R1 max aggregation selects; B1 quote assembly reused) →
build_answer (fixed-section extractive composition) → answer_gate A1–A10 →
ANSWERED or abstention. No independent statute/judgment retrieval inside the
answer layer (signature-asserted + functionally tested); no LLM anywhere.

## 3. Answer contract

B1's shape extended: status/question/judgment{jid,number,date — no court per
FR-016}/answer/judgements/statutes/evidence{judgment,statutes}/claims/citations
[{evidence_id, display}]/abstention. Displays re-derived from evidence (A9).

## 4. Claim model

DISPOSITION/HOLDING/REASONING/STATUTE claims quote verbatim evidence;
LIMITATION claims match a 3-string allowlist with zero evidence;
ANSWER_SYNTHESIS/JUDGMENT_FACT exist in the kind vocabulary for later layers.
APPLIED/DECISIVE kinds cannot pass the gate.

## 5. Grounding gate (A1–A10)

Evidence-per-claim, id existence, single-judgment confinement (parsed from
evidence IDs), B2-B linkage for every statute, verbatim-or-allowlisted text
(strict remainder-empty), no decisive phrasing outside quotes (allowlist
pinned by test), display re-derivation, mandatory-evidence presence.
Gate failure → REJECT → ABSTAINED(GROUNDING_FAILURE).

## 6. Answer generation strategy

Extractive floor first (this batch): fixed headers + verbatim quotes + fixed
limitations. No model introduced, so §19 model vetting was vacuous — recorded,
not skipped. Model-assisted wording stays a later batch with the gate as the
unchanged enforcer.

## 7. Abstention strategy

NO_JUDGMENT / INSUFFICIENT_JUDGMENT_EVIDENCE / NO_REASONING_EVIDENCE /
STATUTE_EVIDENCE_UNRESOLVED (opt-in via require_statutes) →
INSUFFICIENT_EVIDENCE; GROUNDING_FAILURE / CONTRADICTORY_EVIDENCE (duplicate-
disposition tripwire, tested) → ABSTAINED. Never best-effort.

## 8. Reviewed answer set (§16)

4 questions: A-01 demo dismissal (ANSWERED, 12 claims), A-02 detention cause
(ANSWERED, real golden query), A-03 reasoning-only excerpt (contract-correct
abstain), A-04 payment order + require_statutes (abstain, correct reason).
Builder-generated, determinism + fixture-match verified by the evaluator.

## 9. Automated metrics (B2-D-EVIDENCE.json)

C1 14/14 supported, C2 0 unsupported, C3–C6 structural via gate + verbatim
quotes, C7 abstentions reason-correct; regeneration byte-identical.

## 10. Human verification results (§18, reviewer-stamped in evidence file)

4/4 PASS with per-question notes (disposition/holding/statute/reasoning
fidelity confirmed verbatim; abstentions contract-correct; recorded design
feedback: reasoning-sufficient questions, no-synthesis-sentence floor).

## 11. Real-case demonstration

A-01: question → STEV店小450 → 主文駁回 + 難認有據/為無理由 + 法院理由 +
184/195/436-18 exact texts + scope/citation limitations, gate PASS.

## 12. Regression results → validation report.

## 13–15. Limitations / follow-ups / terminal

- L1 extractive floor (no synthesis sentences; fluency last by design).
- L2 single-judgment scope; L3 mandatory-evidence contract is conservative
  (reasoning-only excerpts abstain); L4 live endpoint abstains until
  store/roles wired; L5 court omitted by spec decision.
- Follow-ups → B2-D-FOLLOWUPS.md. Terminal: PASS → STOP (B3 not started).
