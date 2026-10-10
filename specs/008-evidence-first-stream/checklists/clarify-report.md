# Clarification Report: 判決＋法條 evidence 快速回傳（證據先行）

**Feature**: [spec.md](../spec.md)
**Command**: `/speckit.clarify`
**Date**: 2026-10-10
**Max markers allowed**: 3

---

## Session 1 — 2 markers raised, both answered

### Q1 — 判決原文於查詢時的來源

**Question**: qdrant `judgements` payload 不含 `text`（`retrieve.py:169-172` 實測），
而 FR-001 禁止查詢時讀 parquet。evidence 裡的判決原文從哪裡來？

**Options presented**: (A) pg `judgment` 存全文 / (B) `text` 加進 qdrant payload /
(C) 兩者皆存

**✅ RESOLVED — Option A**（maintainer 選擇）

**Rationale**: 單一來源；FR-011 的 sha256 驗證可直接對上；與既有 `laws` 層的
pg-authoritative 慣例一致。代價是 pg 容量須於 plan 階段實測。

**Additional instruction**: plan 階段用實際 parquet 大小重算全庫 108,409 卷的
pg 容量，附指令與分母。→ 已寫入 **FR-003 容量約束** 與 **Assumptions**。

---

### Q2 — `not_applicable` 的適用範圍

**Question**: 實測 `unsupported_court_type` 命中 103/200 卷（刑事 68／民事 20／
行政 11／憲法 1／訴願 1／懲戒 2），但結構上真正無法解析的只有 12 卷。
`not_applicable` 涵蓋哪些？

**Options presented**: (A) 全部 103 卷 / (B) 僅 12 卷 / (C) 三態（刑事標 `degraded`）

**✅ RESOLVED — Option B**（maintainer 選擇）

**Rationale**: 刑事判決大量引用刑法／刑事訴訟法，放棄抽取可惜；12 卷行政／特別法域
才是結構性無法解析。

**Additional instruction**: 新增 SC 驗證刑事抽取品質。→ 已寫入 **SC-009**。

---

## Spec corrections applied in this session

| # | Maintainer instruction | Applied |
|---|---|---|
| 1 | 「現有資源」改為 qdrant 只有 laws，judgements 不存在，列為前置 task | 新增〈前置 task〉節：PRE-1（judgements collection 不存在）／PRE-2（payload 無 `text`）／PRE-3（pg `judgment` 不存在），全部附實測證據 |
| 2 | 已知問題一節改用實測數字，標明原數字已被推翻 | 標題改為「原『已知問題』數字**已全部作廢**」，表格改為「已作廢的說法 → 重測實測值（取代）→ 作廢原因」，並新增第三列（容器依賴說法部分不成立） |
| 3 | SC-003 若基線 RSS 增量接近 0，改為有意義的指標並說明理由 | **已改寫**。實測母體 A 525 MB／母體 B 29 MB／增量 496 MB，而 `jfull_map` 僅 +1 MB。新門檻：峰值 RSS ≤ 100 MB ＋ 靜態 grep ＋ 執行期 trace 三重證明 |

---

## Open items after this session

**None.** Both markers resolved; all three corrections applied.

### Items deliberately NOT resolved here (they belong to plan)

| Item | Why not in specify | Where it is recorded |
|---|---|---|
| pg capacity for 108,409 volumes | Requires measurement against actual parquet size — a plan-stage task | FR-003 容量約束、Assumptions |
| `judgements` collection build | Implementation task | PRE-1 |
| SC-009's sampling frame mechanics | Implementation detail; the threshold and escalation rule are specified | SC-009 |

---

## Verification against constitution XII

- [x] No quantitative claim lacks a re-runnable command
- [x] Raw output archived under `specs/008-…/evidence/`（2 個檔案）
- [x] Every number states its denominator
- [x] Superseded figures are marked as superseded, not silently replaced
- [x] Verified facts separated from inferences
- [x] No `[NEEDS CLARIFICATION]` markers remain in spec.md (verified: 0)

## Recommended next step

`/speckit.plan` — with the note that plan MUST start by resolving **PRE-1**
(`judgements` collection missing), or FR-002 and SC-008 cannot be implemented.