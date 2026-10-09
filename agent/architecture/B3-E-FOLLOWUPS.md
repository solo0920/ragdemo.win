# B3-E Follow-ups — recorded, NOT done in B3-E

- F1 Per-statute 適用 prose ("法院在其理由中明確適用…"): `applied_statutes` +
  A11 support it machine-readably, but no fixed string was added. Needs product
  wording authorization + HEADERS/LIMITATIONS allowlist + A6/A7 gate + fixture
  updates (same path as B3-D F1). Current prose stays at the weaker verified
  引用 level, which remains true for applied statutes.
- F2 Multi-chunk application evidence: holding + citation split across chunks
  (A-09 shape) is refused by rule (`multi-chunk-application-unsupported`).
  Needs B4-A continuity composition + reviewed multi-chunk cases — not a B3-E
  rule relaxation.
- F3 Checkbox-conclusion class (A-10: detention tick-box rulings with a
  conclusion but no holding pattern): UNRESOLVED by v1 rule. Any new
  conclusory pattern needs reviewed evidence and B2-C-owner review.
- F4 UNRESOLVED rate (99.3% corpus): precision-first by design. Recall study
  needs reviewed labels, not rule loosening. Zero non-court applied is the
  invariant to preserve while studying recall.
- F5 DECISIVE_STATUTE stays out of scope permanently for this line: no batch
  may infer decisiveness from application.
