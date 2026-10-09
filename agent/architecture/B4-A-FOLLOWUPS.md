# B4-A Follow-ups — recorded, NOT done in B4-A

- F1 Split-citation reassembly: citations split across chunk boundaries are
  not reassembled (B2-B per-chunk extraction); needs span-bridged citation
  detection with B2-B-owner review.
- F2 Disposition-completion cap: the 500-char continuation cap is arbitrary;
  section-header-bounded in practice; revisit with longer real cut cases.
- F3 Multi-node `chunk_id` convention: first-chunk id with full list in
  provenance; consumers must read `chunk_ids`, not `chunk_id`.
- F4 Assembly of HOLDING groups across chunks: machinery exists; no reviewed
  real multi-chunk holding case yet (single-chunk holdings dominate).
- F5 Currency/contradiction interplay beyond current gates: unchanged status.
