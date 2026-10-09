# B1 Follow-ups — recorded, NOT fixed in B1 (scope control)

- F1 document-store reader (serving side): `serve_live` passes empty
  `jfull_by_entry` until a backend JFULL reader exists. Then live
  `/judgments/query` can quote. Requires PG/archive read design; no fabrication.
- F2 live `judgements` index population: needs embedding-model authorization
  (E05-D-D successor rules) + Qdrant ops + frozen-index isolation proof.
  B1 wrote nothing to any collection.
- F3 statute name aliases (e.g. short names resolving to full corpus names):
  refused in B1 (gazetteer risk, FR-037). Needs a verified alias source first.
- F4 Level-2 retrieval-based statute linking (reason-to-article correspondence
  beyond explicit mention). Design + eval probes first.
- F5 LLM-based grounded generation for the judgement path, with gate item 7
  extended to free text (currently solved only for the extractive path).
- F6 doctrines for later batches: contradiction, currency/supersession,
  quoted-contamination, disposition roles, multi-chunk chain completeness.
- F7 `court` field: spec 004 FR-016 forbids inference; any change needs a spec
  amendment with a real source field, not a B1 workaround.
- F8 Real-data finding (no action): judgement line-wraps split article numbers
  (`第50<CRLF>3條`) and the corpus renders inserts hyphen-form (`第436-18條`)
  while judgements write 之-form — both handled in B1 with provenance flags.
