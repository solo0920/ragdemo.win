# B3-B Follow-ups — recorded, NOT done in B3-B

- F1 Versioned statute source: the only path to HISTORICAL_VERIFIED on real
  data (amendment-level article intervals + pinned texts). 286 single-
  promulgation laws are the analyzable starting pool, but free-text history
  parsing was deliberately declined — needs a structured upstream source.
- F2 JDATE threading: corpus records lack dates (payloads/docs have them);
  serving must carry decision dates into temporal validation (serve path
  reads payload jdate when enforcing).
- F3 Open-ended continuity: T2-strict always UNKNOWN; a continuity-proof
  record shape (full version list) is future work, not assumed.
- F4 Law-level effective_date coverage (90/1347) and semantics: unanalyzed;
  do not build validity on it without a spec.
- F5 Abandoned/repealed flags in temporal reasoning: recorded in rows but
  uninterrupted — citing an abandoned law is not temporal-invalidity without
  dates (currency doctrine stays out of scope).
- F6 Full rollout of temporal limitations in served answers (currently
  opt-in; fixture updates required if defaulted later).
