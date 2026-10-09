# B3-E — Court-Applied Statute Evidence

Status: **implemented + gated — deterministic, no LLM, no similarity, no decisive inference.**

Product question: can the system prove, from actual judgment evidence, that the
court expressly *applied* a cited statute in its reasoning or disposition,
rather than merely mentioning or quoting it?

Answer: yes, for one narrow shape — a citation contained in a court-voice
conclusory holding (`holding-containment-v1`). Everything else stays
`APPLICATION_UNRESOLVED`. Application is not decisiveness: `DECISIVE_STATUTE`
is never emitted.

## 1. Relation namespaces (nine, disjoint)

`MENTIONED_IN_JUDGMENT` (informal) · `PROSECUTOR_OR_INDICTMENT_ATTRIBUTION` ·
`PARTY_ATTRIBUTION` · `COURT_VOICE_EXPLICIT_CITATION` · `COURT_EXPLICIT_APPLICATION` ·
`QUOTED_STATUTE` · `QUOTED_OTHER_JUDGMENT` · `UNRESOLVED_ATTRIBUTION` ·
`APPLICATION_UNRESOLVED`. The first eight are owned by B2-B/B3-A/B3-D; B3-E owns
only the fifth and ninth, and only as a function of the other layers' outputs.

## 2. Application relation contract (`backend/app/b3e_applied.py`)

```json
{
  "relation_id": "applied:<sha256(citation_id|node_id)[:16]>",
  "citation_id": "...",
  "judgement_id": "...",
  "relation_type": "COURT_EXPLICIT_APPLICATION | APPLICATION_UNRESOLVED",
  "citation_evidence": {"chunk_id": "...", "source_span": "...", "raw_text": "..."},
  "application_evidence": {
    "evidence_id": "...", "evidence_type": "HOLDING",
    "attribution": "COURT_VOICE",
    "chunk_ids": ["..."], "source_spans": ["..."],
    "verbatim_text": "...", "conclusory_marker": "..."
  },
  "rule_id": "holding-containment-v1",
  "verification_status": "COMPUTED",
  "reason": "..."
}
```

`application_evidence` is `None` unless applied. Every field is transported
from B2-B/B2-C/B3-D outputs; B3-E derives no span, no text, no classification.
Citation/statute evidence IDs are never renamed, so the B3-B temporal map keys
on unchanged.

## 3. Rule A1–A4 (all must hold; anything else → UNRESOLVED with reason)

- A1. Citation corpus-resolved AND its B3-D relation is
  `COURT_VOICE_EXPLICIT_CITATION` (`not-court-used` otherwise — covers party,
  prosecutor, quoted-statute, quoted-judgment, unresolved, missing relation).
- A2. Covering node is a B2-C HOLDING with provenance method
  `conclusory_pattern` (`no-covering-node` / `covering-not-holding` /
  `holding-not-conclusory`). The sentence both cites the statute and states the
  court's conclusion. A disposition containing a citation stays UNRESOLVED.
- A3. That node independently classifies `COURT_VOICE` in the attribution map;
  missing entries fail closed (`node-attribution-not-court`).
- A4. Single-text locality inherited from the B3-D relation, never re-derived;
  multi-chunk assembled nodes excluded (`multi-chunk-application-unsupported`).

`link_application` never raises on data shape; `link_all` maps every citation
(no forced binary).

## 4. Dependencies (read-only)

B3-D authoritative for speaker attribution and coverage; B3-A for passage
attribution; B4-A for continuity; B2-B for citation identity and corpus text.
No B3-A/B rule edits, no new markers, no threshold changes. Multi-chunk
application evidence is out of v1 scope (documented limitation, not merged).

## 5. Temporal and contradiction

B3-B HOLD and B3-C PASS preserved. Applied ≠ historical wording: temporal
status rides along untouched (`CURRENT_ONLY`/`UNKNOWN` stay explicit
limitations; INVALID statutes are excluded upstream in `linked_evs`).
No conflict is resolved in favor of any statute. Applied ≠ decisive, including
rejections (corpus case 6: statute applied to dismiss).

## 6. Answer-layer integration

`build_answer` computes `applied_statutes` unconditionally (transparent record)
and A11 consistency-gates them (forged citation/evidence/attribution or any
non-{APPLIED, UNRESOLVED} type fails). Prose unchanged: `H_STATUTES` /
`LIMIT_CITATION` claim only explicit citation and deny decisiveness — both
remain true for applied statutes. No `APPLIED_STATUTE` claim kind exists (A6
forbids it). Per-statute 適用 wording is a follow-up (F1), not smuggled in:
new fixed strings need product authorization + allowlist/gate/fixture updates.
