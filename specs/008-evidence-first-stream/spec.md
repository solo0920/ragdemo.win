# Feature Specification: 判決＋法條 evidence 快速回傳（證據先行）

**Feature Branch**: `008-evidence-first-stream`

**Created**: 2026-10-10

**Status**: Draft

**Input**: User description: "判決＋法條 evidence 快速回傳（證據先行）"

**Clarified**: 2026-10-10 — Q1 選 **A**（pg 存全文）、Q2 選 **B**（僅 12 卷行政／特別法域
為 `not_applicable`）。詳見〈澄清結果〉。

---

## 前置 task（spec 階段實測發現，plan 必須先處理）

| # | 前置項 | 實測證據 | 影響 |
|---|---|---|---|
| **PRE-1** | **`judgements` qdrant collection 不存在** | `GET /collections` 只回 `laws`（222,151 points）。feature 描述的「現有資源」宣稱含 judgements collection，**該敘述不成立** | **FR-002 無法實作**。必須先建 collection 並灌入 200 卷 chunk |
| **PRE-2** | **`JUDGMENT_PAYLOAD_FIELDS` 不含 `text`** | `retrieve.py:169-172` = `entry_path, jid, chunk_index, start_offset, end_offset, content_hash, jyear, jdate, jcase` | 查詢時無自帶原文。已由 Q1=A 解決（原文改由 pg `judgment` 提供） |
| **PRE-3** | **pg `judgment` 表不存在** | 實測 pg 僅 `law`／`article`／`law_import`／`backends`／`model_usage`／`host_settings` | FR-003 需先建表 |

⚠ 這三項是**實測事實，不是推測**。plan 階段若未先處理 PRE-1，FR-002／SC-008 直接失敗。

---

## 澄清結果（2026-10-10）

### Q1 → 選 A：pg `judgment` 存全文，sha256 於 pg 內驗證

- 判決全文存於 pg `judgment`，查詢時一次撈回，不讀 parquet（符合 FR-001）。
- **容量須於 plan 階段用實際 parquet 大小重算全庫 108,409 卷的 pg 容量**，
  並附可重跑指令與分母定義（constitution XII / R1）。spec 階段**不填入未經量測的容量數字**。

### Q2 → 選 B：僅 12 卷（行政／特別法域）為 `not_applicable`

- reason code 固定為 **`admin_special_jurisdiction`**。
- 其餘 91 卷（含**刑事 68 卷**）維持正常抽取法條。
- 新增 **SC-009**：刑事卷抽樣 30 筆人工核對（見 SC 節）。

---

## 實測基線（constitution XII / R1、R2）

> 以下每個數字都附：①可重跑指令 ②原始輸出檔 ③分母定義。
> 指令：`.venv/bin/python specs/008-evidence-first-stream/verify/measure-baseline.py | tee specs/008-evidence-first-stream/evidence/baseline-<ts>.txt`
> 證據：`evidence/baseline-20261010-192445.txt`

### ⚠ 原「已知問題」數字**已全部作廢**，以下為重測取代值

feature 描述提供的數字，經 constitution XII（R1/R2）重測後**均不成立**。
**原數字僅作為「為何需要重測」的記錄保留，不得再被引用。**

| 已作廢的說法 | 重測實測值（取代） | 作廢原因 |
|---|---|---|
| `jfull_map()` 載入約 **619 MB、約 1.9 秒** | **0 ms、RSS 15→16 MB（+1 MB）** | **歸因錯誤**：619 MB／1.92 s 實為 `StatuteCorpus.from_jsonl()`，非 `jfull_map()`。`jfull_map()` 只讀 `data/judgements/seed/`，目前**只有 1 個檔案**。 |
| `extract_statute_mentions()` 約 **25 ms/卷** | min **6.7**／median **175.5**／p95 **957.5**／max **2718.1** ms | **樣本誤導**：25 ms 量自**單一 3,137 字元**的 seed 文档。母體改為 200 卷（6,688,933 字元）後 median 為其 **7.0 倍**、max 為 **109 倍**。 |
| 「容器依賴 `laws_flat.jsonl`（被 gitignore）」→ 暗示讀不到 | **部分不成立**：`compose.yaml:212` 有 bind mount，容器讀得到 | 真正的問題較窄：**fresh clone** 時主機無該檔（gitignored），mount 會是空的。 |

**這三點正是 constitution XII 要求「先量再說」的實例**：未帶分母的數字會把
單一樣本的極小值誤報成整體特性；錯誤歸因則會把成本記到錯的元件上，導致修錯地方。

### 重測基線（可引用）

| 項目 | 實測值 | 分母（母體） |
|---|---:|---|
| `jfull_map()` 首次 | 0 ms；RSS +1 MB | `data/judgements/seed/` 的 **1 個檔案** |
| `jfull_map()` 快取後 | 0.002 ms | 同上 |
| `StatuteCorpus.from_jsonl()` | **1.92 s**；RSS **526 MB** | `laws_flat.jsonl` 的 **222,109 條文／11,803 部法規** |
| `extract_statute_mentions()` median | **175.5 ms** | 評估 manifest 的 **200 卷**（6,688,933 字元） |
| `extract_statute_mentions()` p95 | **957.5 ms** | 同上 |
| `extract_statute_mentions()` max | **2718.1 ms** | 同上 |
| 抽出結果為空的卷 | **1/200** | 同上 |

### 環境事實（已 grep／實測確認，非推論）

| 事實 | 證據 |
|---|---|
| `laws_flat.jsonl` **不在版控** | `git check-ignore` rc=0，規則 `.gitignore:52 data/laws/*`；`git ls-files --error-unmatch` rc=1 |
| 該檔本機存在 | 179,384,373 bytes |
| 容器**沒有** COPY 資料層 | `backend/Dockerfile` 只有 `COPY pyproject.toml uv.lock ./` 與 `COPY app ./app` |
| 但容器**有** bind mount | `compose.yaml:212` → `- ./data/laws:/app/data/laws:ro` |
| `JUDGMENT_PAYLOAD_FIELDS` **不含 `text`** | `retrieve.py:169-172` = `entry_path, jid, chunk_index, start_offset, end_offset, content_hash, jyear, jdate, jcase` |
| **`judgements` collection 不存在** | 實測 `GET /collections` 只回 `laws`（222,151 points） |

⚠ **修正本專案先前的錯誤說法**：先前曾稱「容器裡沒有 `laws_flat.jsonl`，
判決路徑會直接拋例外」。**不成立**——`compose.yaml:212` 的 bind mount 讓容器
讀得到。真正的問題較窄：**在 fresh clone 上**，因為該檔 gitignored，主機不會有它，
bind mount 會是空的。

⚠ **feature 描述的「現有資源」有一項不成立**：它說 qdrant「含 judgements
collection」。實測**不存在**（只有 `laws`）。這直接影響 FR-002 的前提。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 證據先於生成抵達（Priority: P1）

使用者在前端送出法律問題後，**在 AI 開始生成之前**就看到最相關的判決原文與該判決
引用的法條原文。畫面先呈現「證據卡」，LLM 敘述隨後以串流補上。

**Why this priority**: 這是整個功能的存在理由。使用者目前必須等生成完才看到任何東西，
而生成可能耗時數十秒；在金融法律場景，先看到可自行核對的原文比先讀到流暢敘述重要
（constitution XI）。

**Independent Test**: 關閉 LLM 後端，只送一個查詢，若仍收到完整 `evidence` 事件，
即證明證據不依賴生成（SC-008）。

**Acceptance Scenarios**:

1. **Given** 使用者已在前端輸入問題且 LLM 可用，**When** 送出問題，**Then** 使用者在
   任何 `token` 事件之前先收到 `evidence` 事件，且 evidence 內含至少一段判決原文
2. **Given** 命中判決引用了法條，**When** evidence 事件送達，**Then** evidence 同時含
   該法條的原文與可點擊載入其餘法條的識別碼
3. **Given** LLM 後端不可用，**When** 使用者送出問題，**Then** evidence 事件仍完整送達，
   並以 `done` 事件收尾（不因缺 LLM 而失敗）

---

### User Story 2 - 點擊展開完整法條（Priority: P2）

使用者看到 evidence 卡片上的法條摘要（法名＋條號）後，可點擊任一筆查看完整條文；
未內嵌的法條也能點擊載入。

**Why this priority**: 前 N 條內嵌、後��條給識別碼，是為了讓 evidence 事件保持小體積
與低延遲；完整內容必須仍可取得，否則引用就成了死連結。

**Independent Test**: 取一個已知 `(pcode, seq)`，請求展開端點，應回傳該條全文；
再取一個不存在的 `(pcode, seq)`，應誠實回報找不到而非空白。

**Acceptance Scenarios**:

1. **Given** evidence 內嵌了前 N 條法條，**When** 使用者點擊其中一筆，**Then** 顯示的
   條文與官方公布內容一致
2. **Given** evidence 只回傳了 `(pcode, seq)` 識別碼，**When** 使用者點擊，**Then** 後端
   載入並顯示該條全文
3. **Given** 請求的 `(pcode, seq)` 不存在，**When** 展開，**Then** 回報「找不到該條文」，
   不得回傳空白或猜測內容（constitution XI）

---

### User Story 3 - 看得見哪一段慢（Priority: P3）

當使用者感覺回應慢時，能從回應的 Server-Timing 標頭看出時間花在嵌入、檢索、
資料庫、還是生成。

**Why this priority**: SC-002 要求「若超過門檻，必須能指出是哪一段」。沒有分段計時，
超標時無法診斷，等於沒有驗收手段。

**Independent Test**: 送一個查詢，讀取 Server-Timing，確認至少含 embed、qdrant、pg、
first_evidence、llm_first_token 五個段。

**Acceptance Scenarios**:

1. **Given** 任一查詢完成，**When** 讀取回應標頭，**Then** Server-Timing 含規定的五個段
2. **Given** first_evidence 超過門檻，**When** 讀取 Server-Timing，**Then** 可分辨是
   嵌入、檢索還是資料庫階段造成

---

### Edge Cases

- **命中判決無法解析**（`unsupported_court_type`）：statutes 必須回傳明確的
  `not_applicable` 狀態與 reason code，**不得回空陣列**——空陣列語意是「沒引用法條」，
  與「無法解析」不同（FR-010）
- **引用是靠游標推斷的**（「前開／上開／同法」）：必須標 `is_inferred=true` 並隨
  evidence 回傳；不得與字面直述的引用混為一談（FR-005）
- **同一判決被多個 chunk 命中**：只出現一次，不得重複（FR-002）
- **法條引用對不到條文**：列入 `unlinked` 並誠實標示，不得猜測或靜默丟棄
- **判決已廢止／條文已刪除**：回傳時明確標示，不得只顯示「（刪除）」四個字
- **LLM 生成失敗**：evidence 已送達即為有效；錯誤只影響 `token` 階段，不得回溯撤銷
  已送的 evidence
- **p95 門檻超標**：SC-002 要求以 Server-Timing 指出階段，故分段計時不是選配
- **預計算重跑**：必須冪等，不得產生重複列（SC-006）

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 查詢路徑**只允許**下列四步：query embedding → qdrant 檢索 → pg 主鍵批次
  查詢 → 推送。**不得**在查詢時讀 parquet 或 seed JSON，**不得**在查詢時執行法名 regex。
- **FR-002**: qdrant 以**判決（jid）為單位分組**取 top-K，同一判決不得因多個 chunk
  重複出現。
- **FR-003**: 新增 pg 表 `judgment` 與 `judgment_statute`。
  - `judgment`：**jid 主鍵；存全文**（Q1 選 A）；欄位含 `sha256`（該列全文的 hash，
    於 pg 內驗證）。
  - `judgment_statute`：主鍵 `jid, pcode, seq`；含 `is_inferred`、在判決中的 offset。
  - statutes 放**判決層級**，不放進 qdrant 每個 chunk 的 payload。
  - ⚠ **容量約束**：plan 階段必須以實際 parquet 大小重算**全庫 108,409 卷**存入 pg 的
    容量，附可重跑指令與分母（constitution XII / R1）。本 spec 刻意不填入未經量測的
    容量估算——上一輪的「180 倍」與「5.7 GB」皆為**外推值非實測**，不得沿用。
- **FR-004**: 入庫時**一次性預先計算**每份判決的 statutes，寫入 `judgment_statute`。
  預計算須**可增量**（只處理尚未處理的 jid）且**可重跑不產生重複**。
- **FR-005**: `is_inferred` 必須保存並隨 evidence 回傳；「前開／上開／同法」等靠游標
  推斷的引用標為 `true`。
- **FR-006**: 法條原文以**單一 SQL 批次**取回（`WHERE (pcode, article_seq) IN (...)`），
  **不得 N+1**。前 N 條隨 evidence 內嵌（N 為設定值，預設 10），其餘回傳 `(pcode, seq)`
  供點擊載入。
- **FR-007**: 新增條文展開端點。**已查證**：既有條號精準分支（`rag.py` 中
  `if an and top and retrieve._exact_match(top, an):`）**在呼叫 `generate()` 之前
  return**，故**未綁定 LLM 路徑**，可安全重用。
- **FR-008**: 回應採 SSE：`event "evidence"` 先送，`event "token"` 隨後串流，
  `event "done"` 收尾。
- **FR-009**: 回應帶 `Server-Timing` header，至少含 `embed`、`qdrant`、`pg`、
  `first_evidence`、`llm_first_token`。
- **FR-010**: 行政／特別法域判決的 statutes 欄位必須回傳
  **`not_applicable`**，reason code = **`admin_special_jurisdiction`**；**不得回空陣列**
  （空陣列語意是「沒引用法條」，與「無法解析」不同）。
  - **適用範圍＝12 卷**（200 卷中結構上真正無法解析者），**不含刑事 68 卷**
    （Q2 選 B）。刑事卷維持正常抽取，其品質由 **SC-009** 驗證。
  - 其 reason code 由 parser 現有 `unsupported_court_type` 分流而來；
    本功能**不新增 parser 能力**（行政／獨立 parser 屬範圍外）。
- **FR-011**: 判決原文須可還原：pg 存的內容與 parquet 以 sha256 對得上。

### Key Entities

- **Judgment（判決）**: 一份法院文書。屬性：jid（主鍵）、法院、案號、日期、**全文**、
  sha256。全文可逐位元組還原至來源（constitution XI）。
- **JudgmentStatute（判決引用法條）**: 一份判決對一部法規某一條的引用。屬性：jid、
  pcode、article_seq、**is_inferred**（是否靠游標推斷）、offset（在判決中的位置）。
- **StatuteArticle（法條）**: 一條法規條文。**已存在於 pg `article`**，主鍵
  `(pcode, article_seq)`，本功能直接重用，不新建。
- **Evidence（證據包）**: 送給前端的證據事件內容 = 命中判決的原文切片 ＋ 該判決的
  statutes 清單 ＋ 前 N 條法條全文。
- **NotApplicable（不適用狀態）**: 判決無法解析時的 statutes 替代值，帶 reason code。
  與「空清單」語意不同。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

> 下列門檻由 maintainer 指定。每條對應一支可重跑腳本（`specs/008-…/verify/`），
> 輸出存 `specs/008-…/evidence/`（constitution XII / R1）。

### 量測證據檔

| 檔案 | 產出指令 | 涵蓋 |
|---|---|---|
| `evidence/baseline-20261010-192445.txt` | `.venv/bin/python specs/008-…/verify/measure-baseline.py \| tee <檔>` | 改動前基線：`jfull_map`、`StatuteCorpus`、`extract_statute_mentions` 耗時分佈、`laws_flat.jsonl` 的 git 事實 |
| `evidence/rss-probe-20261010-193429.txt` | 同上（RSS 探針） | 母體 A 525 MB vs 母體 B 29 MB，增量 496 MB（SC-003 門檻依據） |

- **SC-001**: evidence 事件在 qdrant 命中後，**pg 階段（server 端，含往返）p95 ≤ 20 ms**；
  以 **200 個不同查詢**量測，附原始 timing。
- **SC-002**: `first_evidence` 的 **p95 ≤ 150 ms**（含 embedding）；若超過，
  Server-Timing 必須能指出是哪一段。
- **SC-003** *(2026-10-10 改寫)*: 查詢路徑**不得載入 `StatuteCorpus`**，以三種獨立證明：
  1. **靜態**：grep 顯示查詢路徑無 `from_jsonl` 呼叫點；
  2. **執行期 trace**：`jfull_map` 不再被呼叫；
  3. **記憶體**：查詢路徑**峰值 RSS ≤ 100 MB**。

  **為什麼改寫**（原本的「RSS 相對基線下降」不可驗收）：

  | 量測 | 值 | 分母（母體） |
  |---|---:|---|
  | 母體 A：現行查詢路徑（含 corpus） | **525 MB** | 單一 Python 程序，`ru_maxrss` |
  | 母體 B：同一程序但不載 corpus | **29 MB** | 同上 |
  | corpus 造成的增量 | **496 MB** | A − B |
  | `jfull_map()` 造成的增量 | **+1 MB**（15→16） | 單一 seed 檔案 |

  原 SC-003 有兩個致命缺陷：
  - **目標錯了**：它針對 `jfull_map` 的 RSS，而那只有 **+1 MB**，本來就接近 0，
    「下降」無從成立。
  - **門檻不可驗收**：「相對基線下降」沒有絕對值，無法判定通過與否。

  新門檻 **100 MB** 的依據：母體 B 實測 29 MB，即使加上 pg 撈回的判決全文與
  內嵌法條仍應遠低於 100 MB。**門檻的作用是「證明 496 MB 的 corpus 確定未被載入」**，
  不是精確預測——它比基線低 5 倍，給出無歧義訊號。
  證據：`evidence/rss-probe-20261010-193429.txt`。
- **SC-004**: 預計算 200 卷後，`judgment_statute` 筆數與**逐卷獨立重算**結果完全一致
  （差異 0）；明列分母為 **200**，並分別列出「已解析／not_applicable／失敗」卷數，
  **三者相加須等於 200**。
- **SC-005**: 對抽樣 **30 筆** statute，人工可核對判決原文 offset 確實指向該法條字樣；
  `is_inferred=true` 的抽樣另列。
- **SC-006**: 重跑預計算兩次，資料列數與內容 hash 不變（**冪等**）。
- **SC-007**: sha256 抽驗 **200 卷**全數與 parquet 一致，**不一致數為 0**。
- **SC-008**: **不使用 LLM** 的情況下，evidence 事件仍能完整送出（關閉 LLM 後端驗證）。
- **SC-009** *(2026-10-10 追加)*: **刑事卷法條抽取品質**——從 68 卷刑事判決抽取中，
  **抽樣 30 筆** statute，人工核對 `extract_statute_mentions` 的輸出**確實指向該
  判決原文中該條法的字樣**；`is_inferred=true` 的抽樣**另列**。
  - 分母：30 筆抽樣（母體＝68 卷刑事判決的法條抽取結果總數，須於量測時列明）。
  - **門檻（建議）**：失敗率 **> 10%（即 30 筆中 ≥ 3 筆）** → **回報並建議**
    將刑事卷改為 `degraded` 狀態。
  - ⚠ **本 spec 不自行決定**：超過門檻時的動作是「回報 ＋ 建議」，
    是否改為 `degraded` 由 maintainer 決定（constitution X：範圍變更須先停下問）。
  - ⚠ **抽樣侷限須於回報中明寫**：n=30 時單筆＝3.3%，故門檻對抽樣誤差敏感；
    3 筆失敗與 2 筆失敗的差異在此樣本量下不具統計顯著性。回報時 MUST 附
    母體總數與實際抽樣框選方式。

---

## Assumptions

- **分層樣本維持 200 卷**：沿用 spec 007 的分層樣本（137 個法院目錄全覆蓋），
  不重新抽樣；SC-004／SC-007 的分母即為此 200。
- **parquet 原文層不變**：spec 007 的 `judgements.parquet`（200 列，已驗證可還原）
  是本功能的原文事實來源；本功能只增加**查詢路徑**的讀取途徑，不改 parquet 產出。
- **重用既有 `article` 表**：FR-006 的法條批次查詢直接打既有的
  `(pcode, article_seq)` 主鍵，實測 Index Scan 0.018 ms（`evidence/` 有原始輸出）。
- **不動 `b1_serve` 取捨邏輯**：屬 maintainer 指定的範圍外。新的 evidence 路徑與既有
  B1 路徑並存，不取代。
- **`unsupported_court_type` 的判定沿用 parser 現有行為**：本功能不新增 parser 能力。
- **SSE 需前端配合**：本 spec 只定義後端事件契約；前端消費方式屬實作階段。
- **憑證不外流**：Server-Timing 只記耗時，不記問題內容或回應內容。
- **Q1=A 的容量未知**：全庫 108,409 卷存入 pg 的容量**尚未實測**。spec 階段刻意不填
  估算值；plan 階段必須以實際 parquet 大小重算並附指令與分母（FR-003 容量約束）。
  ⚠ 先前 spec 007 的「5.7 GB／180 倍」是**外推值**，依 constitution XII 不得直接沿用。
- **SC-009 門檻 10% 為建議值**：n=30 時單筆＝3.3%，門檻對抽樣誤差敏感。
  超門檻時的動作是「回報 ＋ 建議」，最終決策權在 maintainer（constitution X）。