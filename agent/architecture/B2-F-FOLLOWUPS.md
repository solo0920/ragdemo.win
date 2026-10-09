# B2-F Follow-ups — recorded, NOT done in B2-F

- F1 Serving-method calibration: the live vector ranking has no calibrated
  threshold (policy-avoided measurement on the pre-existing embedding
  runtime). Until an authorized calibration exists, enforced serving abstains
  with unknown-method — the safe state, not a bug.
- F2 Coverage recovery: the zero-wrong-accept rule yields 10%/3% coverage.
  Any relaxation (e.g., bounded wrong-accept budget) needs product-owner
  decision + fresh calibration/validation split, never post-hoc tuning.
- F3 Retrieval-confidence features round 2: margin is the only working
  feature; concentration/coverage/agreement failed. New features need
  pre-declared rules and disjoint validation (same protocol as this batch).
- F4 Identity coverage: only 2/76 queries carry explicit case references;
  party/person fuzziness stays forbidden (no entity extraction added).
- F5 Threshold generality: n=76 single corpus; re-validate before any
  reliance growth (eval expansion, not threshold refit).
- F6 B2-E F1 (retrieval-confidence gating) is now implemented as specified.
