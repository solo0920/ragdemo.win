# B4-B — Multi-Evidence Grounded Answer Composition

Status: **implemented + gated — extractive only, no LLM, no new relations.**
Code: `backend/app/b4b_compose.py` (new) + narrow `b2d_answer.py` extensions
(synthesis-aware A7, postcheck exemption, `answer_mode`/`sections` wiring).
Tests: `tests/test_b4b_{compose,serve,eval}.py` (25).
Fixture: `tests/fixtures/b4b_answer_eval.json` (5 reviewed cases).

## 1. Product objective

Answer questions needing several evidence items from one selected judgment —
disposition plus stated reasons, multiple reasoning passages, disposition
linked to explicit reasoning, cited statutes with resolved text — with every
material claim traceable and no invented legal relation.

## 2. Complete evidence architecture

Unchanged chain (retrieval → confidence → graph → citations → temporal →
contradiction → continuity → answer). B4-B adds a composition layer after
`build_answer`, before the contradiction postcheck; all gates keep running.

## 3. Answer contract

B2-D shape extended with `sections` (per requested kind: claim/evidence ID
lists + synthesis claim ID) and `omitted_sections` (kind + reason). Claim
model unchanged plus `ANSWER_SYNTHESIS` (multi-evidence joins). Abstain
responses carry empty sections with the abstention reason per kind.

## 4. Claim model

One claim may cite many evidence items, but only as a verbatim join of those
items' texts (mechanical decomposition proof). No causal/legal relation is
asserted by joining; sections report passages separately under fixed headers.

## 5. Grounding gate (A1–A10 + synthesis rule)

`answer_gate` accepts an optional `evidence_texts` map. Synthesis claims pass
iff their text decomposes exactly into cited evidence texts; anything else
(including all synthesis without a map) fails explicitly — closing a hole
where forged synthesis previously passed silently. All prior rules unchanged.

## 6. Answer generation strategy

Extractive floor kept: `compose_sectioned` reuses `build_answer` (same
sufficiency/abstention), partitions claims into requested sections, appends
verbatim-join synthesis claims for multi-claim sections, and re-gates the
extended response. No model, no scoring, no retrieval in the composer.

## 7. Abstention strategy

Existing statuses/reasons; subset scopes pre-filter evidence before the
unchanged pipeline (mandatory rules still apply — a subset missing mandatory
evidence abstains honestly). Partial answers omit kinds explicitly; omitted
≠ silently dropped.

## 8. Reviewed answer set (§12)

M-01 demo dismissal (full, 15 claims/4 sections/3 synthesis) · M-02 detention
(9 claims, HOLDING omitted explicitly) · M-03 partial subset (statutes
excluded by request, 10 claims) · M-04/M-05 abstentions with exact reasons.
Builder-generated, determinism + fixture-match verified.

## 9. Automated metrics (§13, all dimensions separate)

26 material claims, 0 unsupported, 0 missing/invalid IDs, 0 attribution /
continuity / statute-overclaim / temporal violations, 0 contradiction
escapes, 0 forbidden types; partial handled explicitly; 3/3 answers and 2/2
abstentions human-verified.

## 10. Human verification results

5/5 PASS with per-question notes in B4-B-EVIDENCE.json (identity,
disposition, holding, reasoning/statute fidelity, attribution, continuity,
completeness, links, abstention).

## 11. Real-case demonstration

M-01: question → STEV店小450 → 主文駁回 + holdings + reasoning + 184/195/
436-18 exact texts + limitations, synthesis claims C13–C15, gate PASS.

## 12. Regression results → validation report.

## 13–15. Limitations / follow-ups / terminal

- L1 answer body keeps single-claim quoting (synthesis lives at claim level).
- L2 subset scopes cannot waive mandatory evidence (conservative by design).
- L3 live route stays single-mode (sectioned is opt-in; adoption is a later
  product decision).
- Follow-ups → B4-B-FOLLOWUPS.md. Terminal: PASS → STOP (B4-C not started).
