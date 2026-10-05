# Specification Quality Checklist: moj-format-ingest-audit

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-10-05
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

### 驗證紀錄（本次逐項檢查）

**Content Quality**

- 規格以「回答與司法院一致」為核心價值陳述，非以工具／資料庫為主軸。
  三個儲存層（parquet／PostgreSQL／Qdrant）只出現在 FR-008～FR-011，
  且描述的是「三者內容必須一致」這個結果，不是「要用哪個資料庫」。
- User Story 1 的驗收描述為「與司法院頁面並排比對」，非技術操作。
- 四個必填段落皆已完成：User Scenarios & Testing、Requirements、Success Criteria、Assumptions。

**Requirement Completeness**

- 無 [NEEDS CLARIFICATION] 標記。三個原本可能需要澄清的點已改為可驗證的陳述：
  - 「一致」的定義 → FR-001 指定以司法院資料庫為基準，SC-001 指定抽樣規模與 100% 相符。
  - 「項級切分是否要切」→ FR-012 直接要求「該條的項排在其他條文之前」，
    這是使用者已明確表達的期望，不需要再問。
  - 是否要求官方 HTML 版面一致 → Assumptions 明確排除（不追求版面，只求內容與結構）。
- 每條 FR 都可獨立驗證。FR-001～FR-007 可用格式檢查驗證；
  FR-008～FR-011 可用三層抽樣比對驗證；FR-012～FR-017 可用查詢結果觀察驗證；
  FR-018～FR-020 可用失敗情境觀察驗證。
- SC-001～SC-007 皆含具體數字（抽樣規模、比對筆數、相符率、門檻百分比），
  且不指涉特定工具或語言。

**Feature Readiness**

- User Story 1（P1）可獨立測試：查一條法條、比對官方頁面。
  即使 P2／P3 完全未做，Story 1 仍交付「回答與官方一致」這個價值。
- 三個 User Story 涵蓋使用者的兩項回報（項次消失＝P1、引用粒度＝P2）
  以及可信度前提（三層一致＝P3）。
- 邊界情境涵蓋六種：已廢止、超長切塊、全形縮排、項款並存、上游失效、三機不一致。
- 與 constitution 的對應已在 plan 階段處理（VI 追溯性、IX 階段可觀察），
  spec 本身不列 constitution 條號（那是 plan.md 的 Constitution Check 職責）。

### 範圍邊界（避免 plan 階段擴張）

本功能**包含**：
- 條文內容與結構的清洗規則
- 三層儲存一致性驗證
- 引用粒度（項級）
- 回答與官方逐字一致

本功能**不包含**（避免 constitution X 的 scope creep）：
- 規則題庫（rules_store）比對邏輯的變更
- JEV 驗證閾值或模型的調整
- 切分機制（rerank/confidence 分級）的重新設計
- 官方 HTML 版面、導覽列、字級等呈現層
- 更換上游資料來源
- 詞庫與 embedding 模型更換

### 已知的實測前提（非推測，供 plan 引用）

以下為 2026-10-05 在 x570 端實測所得，plan 可直接引用而不必重新驗證：

1. 條文在 corpus 中以 `\n` 分隔各項。實測「殺傷性地雷管制條例」第3條：
   `'本條例用詞定義如下：\n一、殺傷性地雷：…\n二、佈雷區域：…\n三、移交：…'`
   → 換行確實存在於資料層。
2. 「證券交易法第14條」在 corpus 中為**單一資料點**，
   `article_seq=15`、`article_no='第 14 條'`、`item` 顯示「第1項」。
   → 資料層沒有項級粒度，故引用不可能召回 14-1～14-6（FR-012 目前無法滿足）。
3. 司法院證券交易法頁面確實列有「第 14-1 條」至「第 14-6 條」六條。
   → FR-006／FR-012 有資料來源依據。
4. `law_struct.structure()` 以 `\n` 計算「1項6款」、`cite_item()` 以 `\n` 顯示「第N項」。
   → 換行是結構資訊，FR-002 有實作依據。
5. 回答層曾以 `collapse_ws()` 壓縮條文，把換行壓成空格，造成項次看似消失。
   → 該項已於本次修正並加測試釘住（`test_exact_article_keeps_paragraph_newlines`）。

### 未驗證、plan 階段需釐清的事實

- parquet 與 PostgreSQL 實際保存的正文是否與 Qdrant 的 `full_content` 逐字相同
  （規格假設相同，尚未逐層抽樣比對）。這是 SC-004 的執行內容。
- 超長條文的實際切塊行為：切塊函式以 `\n` 為界，但未驗證是否可能在項中間切。
  這是 FR-010 的執行內容。
- 三台機器的資料版本現況：x570 為 2026/9/24，wsl 先前實測為 2026/09/18，
  mbp 未知。SC-005 的跨機驗證需先對齊版本。