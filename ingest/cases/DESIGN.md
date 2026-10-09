# ingest/cases 模組架構書（M3）— 判決案例資料管線

> **擁有者：`ingest` agent。** 本檔骨架由 `ARCHITECTURE.md`〈架構表〉建立。
>
> 🔴 **本模組尚未實作，是草案。** `ingest/cases/` 下**只有這份 DESIGN.md**，
> 沒有任何 `.py`、沒有 `cases` collection、沒有 `case`／`case_import` 資料表。
> 下面 §1–§5 的契約**已定案**（邊界與不變量現在就生效，避免日後走歪），
> §6 之後的資料管線細節則**尚未實作**，含多個未解問題（§11）。
> 不要把本檔的量級估算當成既有產出。

## 1. 目標

讓判決**案號**能被引用（laws 已能做到「引用到條」，這是下一層：引用到案）。
做到什麼算完成：`evals/questions.json` 裡**有** `expect_case` 題目，
且 `POST /eval` 把它們算進 `hit_rate`（目前 `expect_case` 是 **0 題**，
見 `evals/README.md` §6）。

## 2. 邊界（不負責）

- **不改檢索邏輯與 UI**（同 M3 laws，`ARCHITECTURE.md`〈架構表〉M3 邊界欄）
- **不動 `ingest/laws/`** —— 兩條管線共用骨架但**各自獨立**；
  laws 已上線多年，cases 實作時**不得**為了重用去改 laws 的行為
  （若發現要共用程式碼，先提出，不要直接讓 laws 依賴 cases）
- 查詢端的雙 collection 檢索與 rerank 接線歸 M2 `backend`（§10 里程碑）
- 題庫加 `expect_case` 題歸 M5 `eval`

## 3. 不變量

- **payload 不放全文。** 全文（數 KB~數十 KB）只進 parquet；
  qdrant payload 只放檢索＋回答所需的最小欄位（含主文／要旨**節錄**）。
  理由：全文會隨 150 字 overlap 重複存，量級差 10 倍以上（§9）。
- **備援機離線回答不需要 PG。** 這是「metadata 抽出來存進 payload」的核心目的
  —— mbp/msi 沒有 PG 也要能給出案號／法院／日期／案由／主文節錄。
- **切分單元＝chunk（800~1200 字＋150 字重疊），不是「一案一向量」** ——
  與 laws 最大的差異。判決太長，一案一向量回憶差且 embedding 放不下。
- **點 ID 必須是 u64/uuid**（Qdrant 限制），用 `md5(f"{case_no}-{chunk_idx}")[:8]`；
  冪等靠 `case_no` upsert ＋ `content_hash` 跳過 re-embed。
- **同 laws 的版本紀律**（`ingest/laws/DESIGN.md` §3）：版本取來源的更新日期欄位，
  不是檔名。

## 4. 陷阱（實測踩過）

本模組**還沒有實測教訓**（沒實作過就沒有）。以下是從 laws 管線**已踩過的坑
推導**的預警，實作時請當成檢查表，並在踩到後回來補：

- 點 ID 用字串 → Qdrant 直接拒收（laws 已踩，見 laws §4 陷阱）
- sparse index 用 u64 → 只收 u32，要 md5 前 4 bytes（laws 已踩）
- 超長文本塞給 bge-m3 → 放不下，要逐筆縮短重試（laws 已踩）
- 在容器裡寫 `data/cases` → 唯讀掛載寫不進去，寫入必須在 host 端
  （laws 已踩，見 laws §3 不變量）
- `VENV_PY` 不要 `.resolve()`（laws 已踩）
- **刪除的判決要能移除** —— qdrant 走整庫重建，不是 upsert（laws 已踩）

⚠️ 兩個**尚未解決**的結構性問題（實作前必須先想清楚）：

1. **`sync-snapshot.sh` 的 `.sync-state` 是單一檔案、會被覆蓋。**
   同時同步 `laws` 與 `cases` 會互相蓋掉狀態。要拆成 per-collection
   （`.sync-state-laws`／`.sync-state-cases`，§8.8）。
2. **全文離線需求未定**：備援機目前只能給主文／要旨節錄。
   若要求全文，得把 parquet 也同步一份到 msi/mbp（§11 問題 4）。

## 5. 驗收

**本模組尚無可跑的驗收指令**（沒有程式碼）。實作後第一步就是補上，
至少要對齊 laws 的形狀：

```bash
# 實作後應補（現在會失敗，不要當成通過）
pytest -q tests/test_cases_*.py                          # 目前沒有這些檔
docker compose exec qdrant sh -c 'wget -qO- --post-data='{}' \
  http://localhost:6333/collections/cases'
curl -s -X POST http://localhost:8000/eval | python3 -m json.tool   # 見 expect_case 題數
```

現在**唯一**能驗的是本文件契約本身：
§1 目標、§2 邊界、§3 不變量、§4 陷阱、§5 驗收 五節齊備。

---

# 資料管線設計（草案，2026-09-24 定案方向）

對齊 `ingest/laws/DESIGN.md` 的 laws 管線，判決案例走同一骨架，但有三處客製：
**清洗規則（抽有價值欄位）**、**cases 的 PG schema**、**切分單元（以案為單位 chunk，非「一條一向量」）**。

## 6. 三層資料架構（定案）

| 層 | 角色 | 存在位置 | 對照 laws 現況 |
|---|---|---|---|
| **全文長期保存** | 清洗後原文＋全 metadata | **Parquet**（按年 partition，append-only） | `eda.py` 已用 duckdb 產 parquet |
| **metadata 主庫** | source of truth：去重／增量／不重複 embedding／審計／faceting | **Postgres（x570）** | `pg_schema.sql`＋`pg_load.py` 已有同款 |
| **檢索視圖** | 檢索＋回答呈現所需的最小欄位 | **Qdrant `cases` collection**（每台本機） | `qdrant_load.py` dense+sparse 同款 |

**分工原則**：
- PG＋parquet 都在 **x570**；msi/mbp 是 qdrant 檢索視圖的備援。
- **備援離線回答不需要 PG**：qdrant payload 內已有檢索＋回答所需的 metadata（案號／法院／日期／案由／主文＋要旨節錄），這就是「把 metadata 抽出來存」的核心目的。

## 7. 為何 metadata 存 Postgres？（評估結論：是）

與 laws 同理由（laws DESIGN §8 已驗證過）：
- **去重**：`case_no`（裁判字號，如 `112年度訴字第1234號`）唯一鍵，upsert 冪等。
- **增量／不重複 embedding**：靠 `content_hash` 判斷內容是否變過，相同即跳過 re-embed。
- **審計**：`case_import`（source_url、source_sha256、fetched_at、件數）留來源變動軌跡、可重新擷取比對。
- **重建保險**：PG metadata＋parquet 全文 = 隨時可重建 qdrant，不必重抓來源。
- **未來 faceting**：前端篩選（法院層級／年份／案由）或統計直接吃 PG。
- 例外折衷：**主文＋要旨**（回答時必引的短欄位，幾百字級）**同時放 PG 與 qdrant payload**；
  全文（數 KB~數十 KB）只進 parquet，不進 PG、不進 qdrant payload。

## 8. 判決專屬設計點

### 8.1 資料源與選樣（**未解，見 §11 問題 1、2**）
- 資料源候選：司法院裁判書查詢系統（`judgment.judicial.gov.tw`，JSON/HTML/txt）／
  法學資料檢索（`law.judicial.gov.tw`）下載。
- 選樣範圍建議（對應題庫）：近 3~5 年、民事＋刑事地院/高院、案由子集（詐欺／損害賠償／借貸類…），
  第一階段 **500~1,000 件**（ROADMAP §4.1/§4.4 目標量級，eval 題庫 50 題夠用）。

### 8.2 清洗規則（normalize 階段）

> **[OBSOLETE — FR-002]** 下列規則已被 `specs/004-judicial-source-fidelity/spec.md` 取代：
> 刪除內容是對司法原文的語意修改；一份少了「主文」的判決比完整但冗長的判決更糟。

- ~~去 boilerplate：當事人資訊、訴訟費用、據上論結尾巴等（往往佔判決一半篇幅）。~~
- 章節標記：`主文`／`事實`／`理由`／`據上論結` 段落結構化（類似 laws 的 chapter 就地帶入）。

> **[DEFERRED — FR-032 / spec §Security and privacy]** 個資去識別化（判決公開本就已遮，但要再檢查）；回答僅供參考非法律意見。
> 這不是「禁止」的動作，而是尚未決定的展示層決策。FR-002 要求 ingestion 階段**不得**遮罩、刪減或改寫原文；
> 任何 access control / redaction 必須發生在獨立的 presentation boundary，不得在管線上游進行。

### 8.3 metadata 抽取

> **[OBSOLETE — FR-005 / FR-016]** 下列規則已被 `specs/004-judicial-source-fidelity/spec.md` 取代：
> LLM 輸出不得被存成權威司法內容；source JSON 沒有 `court` 欄位，從 `JFULL` line 0 推論法院會製造偽造 metadata。

- ~~**第一階段規則式**：案號／法院／日期／案由／案件類型／法院層級——裁判書標題本身結構化
  （「臺灣臺北地方法院民事判決」「112年度訴字第1234號」），regex 即可。~~
- ~~**進階（可選）**：要旨／爭點摘要用 ollama（qwen3:8b，三台都有）批次補抽；500~1,000 件規模可接受。~~
> LLM 生成的 要旨／爭點摘要 若要做為**輔助閱讀**，必須存放在與司法原文完全分離的欄位，並明確標示為模型生成；不得混入權威內容。

### 8.4 切分單元（與 laws 最大差異）
- 判決長文本（數 KB~數十 KB），不能一案一向量（回憶差且 embedding 放不下）。
- 去 boilerplate 後 **800~1200 字切塊＋150 字重疊**；chunk 內文保留「主文/理由」段落名。
- 點 ID：`md5(f"{case_no}-{chunk_idx}")[:8]` → u64（Qdrant 只收 u64/uuid，冪等）。
- 回答時同 `case_no` 的 chunks 可組回整案出處（同 laws 的 article_seq＋chunk_idx 概念）。

### 8.5 Qdrant `cases` collection
- 同 laws：**bge-m3 dense(1024, cosine)＋sparse（BM25 化）**、查詢 DBSF fusion。
- **新增：dense 開 int8 scalar quantization**（laws 沒有，判決點數更多值得做；qdrant 1.19.1 三台支援；
  snapshot 內建 config，備援還原自動一致）。
- payload（每點）：`case_no, court, court_level(地院/高院/最高/行政), case_type(民/刑/行), cause(案由),
  judge_date, 主文節錄, 要旨節錄, chunk_text, chunk_idx, char_len, source, content_hash`。
  **不放全文。**
- payload index：`court_level`、`judge_date`（或 `judge_year`）、`cause`。
- HNSW：`m=32, ef_construction=256`、查詢 `ef=128`（同 laws 量級經驗）。

### 8.6 PG schema 草案（欄位草稿，實作時定稿）
- `case`：`case_no` PK、`court`、`court_level`、`case_type`、`cause`、`judge_date`、
  `verdict_summary`（主文）、`holding`（要旨）、`char_len`、`content_hash`、`updated_at`。
- `case_import`：`source_url`、`source_sha256`、`fetched_at`、`cases_count`、`started_at`、`finished_at`。
- 冪等：`case_no` upsert；`content_hash` 相同就跳過 re-embed。

### 8.7 Parquet 歸檔（eda 階段，duckdb）
- `data/cases/`：`cases_flat.parquet`（一案一列：全 metadata＋清洗後全文）、
  `cases_meta.parquet`；**按年 partition**（抓新年度只加檔）。
- 重建 chunk 時從 parquet 讀原文；原始來源檔保留當上位審計。

### 8.8 同步（sync-snapshot 多 collection 化）
- `sync-snapshot.sh` 已支援 collection 參數，但 `.sync-state` 是**單一檔案會被覆寫**。
- 改 per-collection state：`.sync-state-laws`／`.sync-state-cases`；crontab/launchd 各加一行。

## 9. 量級估算（50G 內極寬裕）

基準：laws 39,879 點 ≈ **288MB**（平均 7.4KB/點，含 HNSW index＋payload）。
判決每件約 15~30 chunk/點：

| 判決件數 | 約點數 | qdrant `cases` 估算 | parquet 全文 |
|---|---|---|---|
| 1,000 件 | 15k~30k | ~150~450MB | ~30MB |
| 5,000 件 | 75k~150k | ~0.7~2GB | ~150MB |
| 10,000 件 | 150k~300k | ~1.5~4GB | ~300MB |
| 50,000 件 | 750k~1.5M | ~7~20GB | ~1.5GB |

- payload 不放全文是最大杠杆（全文在 qdrant 會隨 overlap 重複存）；int8 quantization 再省 2~3 成。
- 快照網路量：只有點數變化才傳（sync 已是增量判斷），且快照隨 payload 瘦身而小。
- RAM 面：10GB 級資料若要再大，可開 mmap 或控制規模；demo 規模用不到。

## 10. 里程碑

> ⚠️ 這裡用「里程碑 1/2/3」編號，**不要寫成 M1/M2/M3** —— 那組代號是**模組編號**
> （M1 env／M2 backend／M3 ingest），混用會讓人以為里程碑 1 是 env 模組。

- **里程碑 1（`ingest` 模組）**：`ingest/cases/` 管線——normalize（清洗＋規則式 metadata）→ PG（case/case_import）→
  parquet（按年）→ qdrant_load（`cases` collection、int8 quantization、500~1,000 件選樣）＋
  sync-snapshot per-collection state。
- **里程碑 2（`backend` 模組）**：`rag.py` 查詢擴充——`cases`＋`laws` 雙 collection 檢索（各自 DBSF top_k 再融合）、
  rerank 接真正 reranker（三台已有 bge-reranker）、前端引用渲染判決出處。
- **里程碑 3（`eval` 模組）**：題庫擴到 50 題（含判決題 `expect_case`）、
  `hit_rate ≥ 0.8`。⚠️ 現況是 14 題計分題（19 題含 5 題 `expect_none`），
  `expect_case` **0 題** —— 見 `evals/README.md` §6。

## 11. 開放問題（**尚未解決**，2026-09-24 記錄至今無結論）

1. 判決資料源格式與抓取方式（`judgment.judicial.gov.tw` 哪種格式？有範例檔？）
2. 選樣範圍定案（年份＋法院層級＋案由子集）
3. 要旨是否第一階段就上 LLM 抽取（可選）
4. 全文離線需求：目前備援離線只能給主文/要旨節錄；若要求全文，再評估 parquet 同步一份到 msi/mbp