# B4-B-F1 — Live Serving Adoption of Multi-Evidence Answers

Status: **integrated + verified — sectioned mode live behind explicit opt-in.**
Code changes: `JudgmentAnswerIn` (+`answer_mode` Literal, +`sections`),
route validation/forwarding (`main.py`), `serve_live` mode+sections params
(`b2d_answer.py`). No composer, gate, or ranking changes.

## 1. Live call chain (verified from code + runtime captures)

HTTP → `JudgmentAnswerIn` validation → `judgments_answer` →
`serve_live(answer_mode, sections)` → `serve_question` (confidence →
retrieval → evidence → contradiction precheck → continuity → attribution →
temporal → single/sectioned composition → contradiction postcheck) → response.

## 2. Enabled mode

`answer_mode="sectioned"` + optional `sections` subset reach the verified
`compose_sectioned` implementation unchanged. Default stays `"single"`.

## 3. Default compatibility

Omitted `answer_mode` → single; explicit single → identical behavior;
`serve_live` signature defaults pinned by test. All single-mode suites green.

## 4. Section validation

Schema rejects unknown modes (422). Route rejects single+sections,
unknown names, and empty lists (400 each, verified live). No silent
downgrade exists on any path; `serve_question`/`serve_live` fail fast with
ValueError for programmatic misuse.

## 5. Direct/live comparison (§10)

`compose_sectioned` vs `serve_question(sectioned)` on identical reviewed
demo evidence: **equivalent** (status, section order, claims, evidence IDs,
abstention). Methodology note: the first run compared mismatched enforcement
inputs and diverged on one statute — the B3-A bridge correctly drops 436-18
(its sentence has no court-voice node) under enforcement. Apples-to-apples
comparison passes; the narrowing is conservative-by-design.

## 6. Safety gates live

Confidence, attribution, temporal, contradiction (pre+post), continuity,
grounding A1–A10, and synthesis re-gating all execute on the sectioned path
(captured kwargs + behavior tests). Gate rejection → existing abstention.

## 7. Limitations

- L1 live route untested with a real HTTP client in-repo (no fastapi in test
  venv); proven via route-function capture in backend venv + source pins.
- L2 attribution enforcement narrows statute coverage vs unenforced direct
  composition (436-18 case) — conservative, documented.
- L3 fixture demo chunks lack spans; continuity-on equivalence covered by
  span-bearing unit tests instead.
- Follow-ups → B4-B-F1-FOLLOWUPS.md. Terminal: PASS → STOP.
