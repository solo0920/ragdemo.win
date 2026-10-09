# Feature Specification: M0 判決問答止血與解鎖（已實作，2026-10-09 驗收）

- Status: **Implemented**（驗收完成並關閉；主機層驗收見 `SCOPE.md`〈M0 驗收結果〉）
  ⚠ 本規格**未走完 clarify／plan／tasks／analyze** 就先實作（先止血後補規格），
  下方 SC-001~SC-004 的逐條對照是關閉時回頭補的，不是事前驗收表。
- Scope: SCOPE.md `M0`（2026-10-09）。北極星：金融法律問題 → 真實判決 →
  判決引用的法條全文 → 逐點可追溯的答案（jid→chunk→span→條號→法條全文）。
- Constitution Check：M0 只做止血（隱藏誤導入口、恢復容器可達、重現基線），
  不新增模組／抽象層（III），不碰 B2/B3/B4 判定與凍結語料（VI、XI），
  每次交付都是可用小切片（I）。無已知違規。

## User Scenarios & Testing

### User Story 1 — 判決摘引不誤導（Priority: P1）

使用者切到「判決摘引」時，要嘛看到標示實驗性的入口或看不到該入口；
有證據時看到逐字引文＋出處，無證據時看到明確拒答而非同一份判決硬答。

驗收：前端「判決摘引」隱藏或標實驗性（截圖或 DOM 斷言二選一）。

### User Story 2 — 容器內服務可達（Priority: P1）

容器內 `/health`、`/query`、`/judgments/query` 皆回 200（證據不足的
200＋誠實 abstain 亦算；500 算失敗）。

驗收（x570 本機實跑）：三個端點 HTTP 狀態全 200；`/judgments/query`
空問題回 `invalid_question`。

### User Story 3 — 基線可重現（Priority: P2）

`pytest -q` 全綠；`agent/scripts/eval_judgements_slice.py` PASS
（grounded 3/3、拒答正確、fabricated spans 0）。

驗收：兩條指令 exit 0＋數字與基線一致（1717 passed／SLICE PASS）。

### Edge Cases

- ollama／Qdrant 任一不可達：端點必須 200＋abstain 或明確錯誤，不得 500
  無訊息（現況容器內 ollama 不可達，見 User Story 2 驗收）。
- `LLM_MODEL` 指向未安裝模型：必須有可觀測的錯誤而非靜默換模型
  （本次只驗證行為，不改預設值）。

## Requirements

### Functional Requirements

- FR-001：前端「判決摘引」入口隱藏或標示實驗性（二選一，不做 UI 改版）。
- FR-002：容器內三端點全 200（含誠實 abstain 的 200）。
- FR-003：基線兩指令可重現（pytest 全綠、S4 eval PASS）。
- FR-004：ollama 綁定位址與 `LLM_MODEL` 預設只出提案，不在本里程碑執行。
- FR-005：不修改 B2/B3/B4 判定、不重 chunk、不裝軟體、不解壓、不碰 `.env`、
  不 push（見 SCOPE.md `M0`「不做什麼」）。

### Key Entities

- 判決摘引入口（前端 kind toggle）、容器服務（api／qdrant）、基線數字。

## Success Criteria

### Measurable Outcomes（技術無關、可量測）

- SC-001：任一使用者在「判決摘引」模式下不會被誤導（入口不可見或標實驗性）。
- SC-002：三端點可用率 3/3（分母＝上述三端點；abstain 的 200 計為可用）。
- SC-003：基線重現率 2/2（pytest exit 0、S4 eval exit 0）。
- SC-004：授權事項三項（`M` 處置、ollama 綁定、`LLM_MODEL` 預設）皆有書面
  提案＋使用者回覆記錄，未經回覆零執行。

## Assumptions

- x570 容器可正常啟動；ollama／Qdrant 主機端可達（容器內 ollama 已知不通，
  列為待授權事項，不在本草稿解決）。
- SCOPE.md `M` 佔位問題由使用者裁決（見 M0 條目待授權第 1 項）。

## 驗收結果（2026-10-09 實測，回頭補錄）

| 條款 | 對應實測 | 結果 |
|---|---|---|
| SC-001 不誤導 | 前端加 kind toggle（law/judgment）＋「實驗性：目前僅收錄 1 份判決」提示 | ✅ 入口不再假裝是完整功能 |
| SC-002 三端點 3/3 | `/health`、`/query`（14/14 hit、5/5 neg）、`/judgments/query` 200 | ✅ 3/3 |
| SC-003 基線 2/2 | `pytest -q` 1723 passed／`eval_judgements_slice.py` `SLICE: PASS` | ✅ 2/2 |
| SC-004 授權事項 3 項 | `M` 擱置、ollama 綁定走選項 B、`LLM_MODEL` 走總表 | ✅ 3/3 皆有回覆，無未授權執行 |

- FR-001~FR-005：見 `SCOPE.md`〈M0 驗收結果〉。FR-004「只提案不執行」已被使用者
  授權改寫（實際執行了，且範圍限授權項），**該條與實作不一致，如實記錄**。
- 已知未驗（UNVERIFIED）：瀏覽器實際 render、mbp/wsl 視角、開機後綁定順序。
