# B2-R1 Follow-ups — recorded, NOT done in B2-R1

- F1 Live serving-path numbers: populate a real `judgements` collection and
  measure the embed→Qdrant→sort path per query. Blocked on embedding-model
  authorization for the serving index + Qdrant ops. B2-R1 wrote only temp
  collections (deleted) and never touched serving collections.
- F2 Significance sample: n=76 leaves most pairwise gaps non-significant;
  eval expansion (new queries, never modifying frozen 76) before any promotion
  re-vote on C.
- F3 HNSW-approximate recall: live test used exact search; production HNSW
  behavior (ef, recall/latency curve) unmeasured.
- F4 Query normalization study (carried from B2-A F3): judgement path still
  normalizes nothing.
- F5 N-cutoff study: N=20 inherited from frozen list length; serving cutoff
  (recall/latency tradeoff) undecided.
- F6 C holding review: re-vote promotion when F1/F2 evidence lands; the D-D
  lineage note (non-vendor modeling-code provenance, on record in the D-D
  candidate file) carries forward to any promotion review.
- F7 GQ-005-pattern unknowns (expected doc retrieved, target chunk absent):
  aggregator support-window (top-3) sensitivity analysis for B2-B+.
