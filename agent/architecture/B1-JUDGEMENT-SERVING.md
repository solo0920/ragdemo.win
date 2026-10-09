# B1 — Judgement Serving: Flow, Contracts, Gate, Demo, Limitations

Status: **IMPLEMENTED (this batch) — extractive-only vertical slice.**
Code: `backend/app/b1_serve.py` · endpoint `POST /judgments/query` · tests
`tests/test_b1_*.py` · fixture `tests/fixtures/b1_judgement_serving/case_00450.json`.

## 1. Serving flow (as built)

```text
User Question
  ↓ normalize (empty → abstain invalid_question)
Judgement retrieval — existing validated search_judgments (injectable search_fn;
  exceptions → abstain retrieval_unavailable, never a 500-answer)
  ↓ top_k slice
Judgement evidence assembly — reuse answer_judgments (T019/T020: sorted quotes,
  structural no_match); unquotable → abstain
  ↓ quoted chunk texts only
Statute linking Level 1 — explicit full-name mentions + corpus-exact lookup
  (same-sentence scoping, layout-collapse, orthography variant; all flagged)
  ↓ zero linked → abstain no_statute_evidence (strict B1 rule)
Extractive composition — labels + verbatim quotes + citations; one claim per quote
  ↓
Mechanical grounding gate (7 checks) → FAIL → abstain grounding_gate_fail
  ↓
Response (contract §7 of the batch brief)
```

No LLM is called on this path. Fluency is deliberately sacrificed for zero
fabrication surface (roadmap priority key).

## 2. Evidence contract (reuses authority models, no duplicates)

Judgement evidence wraps `retrieve.judgment_hit_view` fields and carries:
`evidence_id` (`jdg:{JID}#c{chunk_index}`), `evidence_type`, `source_id`
(entry_path), `document_id`/`judgement_number` (JID, opaque per FR-028),
`judgement_date`, `chunk_id`, `citation` (from `rag.judgment_citation`),
`text` (verbatim `JFULL[start:end]` slice), `provenance`
(entry_path/offsets/content_hash/jdate/score).
`court` is **None with reason** — the source provides no court field and spec
004 FR-016 forbids inferring it. The batch brief's `court` example yields to
the authority model here ("reuse, don't duplicate" rule).

Statute evidence wraps repo corpus rows (`data/laws/laws_flat.jsonl`):
`evidence_id` (`st:{pcode}#{article_seq}`), `law_name`/`article`/`paragraph`
(detail, e.g. 第1項), `citation` (`{law}{article}`), `text` (article_content),
`provenance` (pcode/article_seq/mention_surface/scoped/scoped_from/
orthography_mapped/resolved_as).

## 3. Claim contract

One claim per quoted span: `{claim_id, kind (judgement_quote|statute_text),
text (verbatim), evidence_ids}`. No claim exists without a quote; no quote
exists without evidence. The gate re-verifies this on the final artifact.

## 4. Grounding gate (mechanical, in `grounding_gate()`)

Items 1–6 per brief (claim→ids, id resolution, source/citation presence,
non-empty text, type correctness). Item 7 is **implemented, not a limitation**
on this path: the answer is stripped of every claim text, every citation, and
the allowlisted labels (`【判決引用】`/`【法規引用】` + bare brackets/markers);
any remainder fails the gate. Adversarial test: injected prose is caught.

## 5. Abstention (Cases A–D + invalid input)

`no_judgement_hits` / `retrieval_unavailable` (A, incl. Qdrant-down-as-abstain),
`no_quotable_evidence` (B), `no_statute_evidence` (B/C strict rule),
`grounding_gate_fail` (D). All return `status: insufficient_evidence`,
`answer: null`, zero legal text. Live endpoint maps only unexpected bugs to 500.

## 6. Real-case demo (B1-JUDGEMENT-SERVING)

- Question (genuine, from the case): plaintiff's 30,000 NTD non-pecuniary claim
  under the cited civil provisions — allowed or dismissed, and why.
- Judgement (real, vendored from the E03 extracted work area): 新店簡易庭
  115年度店小字第450號 (JID `STEV,115,店小,450,20260713,1`, 115/07/13,
  損害賠償, 3137-char JFULL).
- Retrieved holding quotes (verbatim): `原告之訴駁回`;
  `原告並未受有實際損害…難認有據`.
- Statutes actually cited and corpus-verified: 民法第184條/第195條,
  民事訴訟法第78條/第255條/第436條/第436-18條, 刑事訴訟法第503條.
- Honest non-links: none silently substituted (the 之→hyphen mapping is the
  corpus's own documented orthography, flagged per evidence).
- Live services note: the vector-DB roundtrip is stubbed at the `search_fn`
  boundary with payloads built by the real T015/T017 code on the real JFULL
  (see test_b1_serving_demo.py). Live Qdrant population needs embedding-model
  authorization + ops (follow-up F2) — the endpoint abstains honestly until then.

## 7. Limitations (explicit, not hidden)

- L1 extractive only: no LLM synthesis (deferred to B2+ with gate item-7 pressure).
- L2 strict abstention: judgement evidence without corpus-verified statute
  evidence abstains even when a human could answer (accuracy > completeness).
- L3 same-sentence scoping and orthography mapping are explicit mini-inferences,
  fully provenance-flagged; cross-sentence attribution and name aliases refused.
- L4 no contradiction/currency/contamination/multi-chunk doctrine (later batches).
- L5 live path abstains until document-store reader (F1) + populated index (F2).
- L6 `court` absent by spec decision (FR-016), recorded not worked around.
