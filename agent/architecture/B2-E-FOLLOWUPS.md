# B2-E Follow-ups — recorded, NOT done in B2-E

- F1 Retrieval-confidence gating: E-06/E-07 prove the answer layer cannot
  detect wrong-judgment selection; a retrieval-side confidence/abstention
  signal is the structural fix (B3 or retrieval batch, not answer layer).
- F2 Evaluation growth: 10 cases → expand reviewed positives (more golden
  queries with complete evidence) and abstention patterns; generator +
  review protocol exists (build_b2e_set.py).
- F3 UNRESOLVED_STATUTE E2E: no natural holding+reasoning refused-only case
  exists in the corpus; revisit if corpus grows, else keep unit coverage.
- F4 Multi-judgment answers: single-judgment scope only (carried from B2-D).
- F5 Currency/contradiction/contamination: untouched, still later work.
- F6 Metric C8 semantics: keep "answered-despite-insufficient-evidence" and
  "answered-from-wrong-selection" (C3) definitionally separate in future evals.
