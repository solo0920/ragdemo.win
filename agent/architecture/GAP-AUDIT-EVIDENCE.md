# GAP-AUDIT-EVIDENCE (RAG-MASTER-GAP-AUDIT, 2026-10-08, read-only commands)

## Frozen lineage (file evidence)

- E03 classifier: `3c9e102e6e89b2b508d7814f039baf46ff7790e1596b8d577f89c651000ab24d`
  (t007e03 BASELINE.md + reconciliation_manifest.json) — MATCH
- E05-A-R1: `5a567299e856454924ba821eae16571630ebeeb966a037d49efe9b64bdb8918d`
  (t007e05-r1 e05a_r1_fingerprint.txt; cross-cited in golden/harness files) — MATCH
  (audit brief printed `...64dbb...`; file has `...64bdb...`; files win)
- E05-B-R1: `c6e41fe26a205196eeeb435345794b55a15c1cb04cc2d6f8310d777d65108c93` — MATCH
- Golden Set: `58e01c798c647b0ebbbf5f7b93f1c61c0aad9908be3cf7ff8ce5c94a339e7f2c` — MATCH
- Harness: `ec30e8fdcb70c8fa9c2fd4fdeec467dd448e8adcf6a40196683cbdb6ad579401`,
  corpus `893d40b68907e6221c548b721cd93991bd85e6f370c584eb662497a3f6071a8d` — MATCH
- E05-D-A: model `34af6022…`, experiment `45a87afa…`, index `2ac937161f349fe6c1589bc4780250f423a06a4321f57344d9e75beab25ecc9c` — MATCH
- E05-D-B: model `9c1ab1c9…`, experiment `4f4ddacc…`, index `282a0bd485f9c245260a85eb86caf324e77811fee00e879b1548a490246ad4b7` — MATCH
- E05-D-D record: sha256 `4cc062c1b60da7190218a9960f03afd7d8261e7b21ec4ce0d894449608b21386` — MATCH

## Implementation evidence (read-only)

- `ingest/judgements/`: 3556 total lines (artifact, chunk, citations, decoder, document,
  extract, index_load, inventory); chunk.py enforces byte-exact reassembly; index_load.py
  is pure payload construction (no I/O); point IDs deterministic sha256(entry|index).
- `backend/app/retrieve.py`: `JUDGMENTS_COLLECTION="judgements"` (fixed, not env);
  `search_judgments` + `_judgment_filter(jid/jyear/jdate/jcase)` + `judgment_sort_key`
  + `judgment_hit_view` + `validate_judgment_payload` all defined.
- Serving-path wiring: `answer_judgments` / `search_judgments` have NO callers outside
  tests (grep backend/app + frontend/src) — implemented + tested, NOT integrated.
- `rag.answer()` serves statutes path; `_decide()` yields `no_match` abstention;
  SYSTEM prompt (rag.py:152) instructs source-only answering with the no-match sentence.
- Post-generation grounding/contradiction/currency logic: NO matches in backend
  (grep verify|ground|anchor|entail|faithful|contradict) — MISSING confirmed.
- `evals/questions.json`: 19 questions (< 50-question acceptance bar in evals/README).
- Golden lineage metrics are Recall/nDCG only — retrieval correctness, not answers.

## Test evidence (project venv, no services needed)

- `pytest tests/ -k 'judgement or judg'`: **734 passed, 4 skipped, 787 deselected**
- Full-suite collection under system python errors on missing `httpx`
  (environmental; venv is the supported runner) — recorded as R5, not fixed here.

## Validators (post-write, this batch)

- authority `validate.py`: PASS (15 files)
- adoption `validate_adoption.py`: PASS
- runtime `validate_runtime_arch.py`: PASS

## Production boundary

- `git status`: only pre-existing entries + `?? agent/`; HEAD `7b40222`.
- No writes performed to backend/frontend/ingest/evals/tests/settings/artifacts/Qdrant.
