# B2-A Follow-ups — recorded, NOT done in B2-A

- F1 Live serving-path retrieval measurement: populate a `judgements`
  collection and measure the real embed→Qdrant→sort path per query. Blocked on
  embedding-model authorization (E05-D-D successor rules) + Qdrant ops. B2-A
  wrote nothing to any collection and ran zero retrieval.
- F2 HN threshold declaration: R3 needs a product-owner-declared
  hard-negative-rate threshold (per k, per query class) before it can PASS.
- F3 Query normalization study: the judgement path normalizes nothing; whether
  normalization would move wrong_judgement failures is unproven — needs an
  experiment batch, not an assumption.
- F4 Unknown-failure deep dives (7 rows with evidence in B2-A-EVIDENCE.json):
  GQ-005 pattern (expected doc retrieved, target chunk absent) recurs across
  all three retrievers — candidate for first investigation.
- F5 GQ-002 overlap (C001 = PARTIAL target + same-doc trap): accepted frozen
  property; answer-level batches must decide how a both-context-and-trap chunk
  is cited. Do NOT "fix" the Golden Set.
- F6 Demo-case corpus gap: B1's 店小450 judgement is outside the 74-doc frozen
  corpus; future eval expansion (not modification) may cover it.
