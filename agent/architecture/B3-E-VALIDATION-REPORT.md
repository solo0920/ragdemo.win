# B3-E Validation Report

## 1. Real-evidence audit (all 10 reviewed cases read against source)

| case | source passage (essence) | label | audit |
|---|---|---|---|
| A-01 | 「原告依民法第184條第1項…為無理由，應予駁回」結論句含引用 | APPLIED | hold: court adjudicates under the cited article |
| A-02 | 同句 scoped「第195條第1項」 | APPLIED | hold |
| A-03 | 「第2條第1款定有明文」定義性援引，無結論句 | UNRESOLVED | hold |
| A-04 | 上訴意旨當事人引用第17條第1項 | UNRESOLVED | hold |
| A-05 | 起訴書罪名（第21條第1項），且被法院否決 | UNRESOLVED | hold |
| A-06 | 附錄全文重現第339條之4 | UNRESOLVED | hold |
| A-07 | 「依第436條之18…僅記載主文及理由要領」程序格式依據 | UNRESOLVED | hold (audited exclusion) |
| A-08 | 「訴訟費用負擔之依據：第78條」 | UNRESOLVED | hold |
| A-09 | 引用與結論故意不成對（跨 chunk） | UNRESOLVED | hold (refusal correct) |
| A-10 | checkbox 續收容結論，無 holding pattern | UNRESOLVED | hold (v1 limitation, documented) |

Reviewed recomputation (`agent/scripts/b3e_evaluate.py` §1): **10/10 match**
(relation_type + reason + rule_id).

## 2. Metrics (§9, reviewed + corpus)

- Reviewed classification precision: 10/10; applied precision 2/2.
- **False APPLIED claims on reviewed set: 0** (highest-priority metric).
- False non-application rate: 0 on reliable labels (A-10 is an acknowledged
  limitation, not a reliable positive).
- APPLICATION_UNRESOLVED rate: 2/10 reviewed; 1016/1023 (99.3%) corpus.
- Corpus-wide (`B3-E-EVIDENCE.json`, same 1023 citations as B3-D): 7 APPLIED,
  all spot-read sane (grants and rejections alike — application is not
  decisiveness, e.g. case 6 applies 票據法123 to dismiss).
- Attribution leakage: party/prosecutor→applied 0, quoted→applied 0,
  non-court→applied 0.
- Missing-provenance rate: 0 (every APPLIED carries chunk_ids + source_spans +
  verbatim_text + conclusory_marker + deterministic relation_id).
- Continuity violations: 0 (multi-chunk refused by rule; A-09 proves it).
- Temporal overclaim violations: 0 (IDs never renamed; INVALID excluded
  upstream; no VERIFIED invented).
- B2-D/B4-B claim-eligibility violations: 0 (no APPLIED_STATUTE claim kind;
  A6-tested; A11 unit-pinned).

## 3. Catch verification（問題抓不抓得到）

- 8 個非適用 case：8/8 擋下（`not-court-used` ×6、`covering-not-holding` ×1、
  跨 chunk 拒絕 ×1），無一升級為 APPLIED。
- 偽造攻擊（A11）：偽 citation_id、懸空 evidence_id、非法院 attribution、
  `DECISIVE_STATUTE` 型別 —— 全數 `GROUNDING_FAILURE`（`tests/test_b3e_gate.py` 7 條）。
- 全庫掃描：party／quoted／unresolved 晉升 applied 皆為 0。

## 4. Regression

- New: `tests/test_b3e_link.py` (12) + `tests/test_b3e_gate.py` (7) — 19 passed.
- B2-B/B2-C/B2-D/B3-A/B3-B/B3-C/B3-D/B4-A/B4-B/B4-B-F1 suites: green inside
  full run.
- Full suite: **1688 passed, 119 skipped, 2 deselected** (slow), 0 failures.
- B3-A/B3-B/B3-C/B3-D semantics untouched (read-only imports; no rule edits);
  answer prose byte-identical (no wording change); E03/E05 frozen artifacts
  untouched (no files under their paths modified).

## 5. Terminal state

**PASS.** The statute-application relation is established with traceable real
evidence (2/2 reviewed APPLIED, 0 false), all restrictions preserved
(temporal HOLD, contradiction PASS, attribution/continuity gates intact), and
all regressions green. B4-C not started.
