# 判決案例數據管線設計（草案，2026-09-24 定案方向）

對齊 `ingest/laws/DESIGN.md` 的 laws 管線，判決案例走同一骨架，但有三處客製：
**清洗規則（抽有價值欄位）**、**cases 的 PG schema**、**切分單元（以案為單位 chunk，非「一條一向量」）**。

## 0. 三層資料架構（定案）

| 層 | 角色 | 存在位置 | 對照 laws 現況 |
|---|---|---|---|
| **全文長期保存** | 清洗後原文＋全 metadata | **Parquet**（按年 partition，append-only） | `eda.py` 已用 duckdb 產 parquet |
| **metadata 主庫** | source of truth：去重／增量／不重複 embedding／審計／faceting | **Postgres（x570）** | `pg_schema.sql`＋`pg_load.py` 已有同款 |
| **檢索視圖** | 檢索＋回答呈現所需的最小欄位 | **Qdrant `cases` collection**（每台本機） | `qdrant_load.py` dense+sparse 同款 |

**分工原則**：
- PG＋parquet 都在 **x570**；msi/mbp 是 qdrant 檢索視圖的備援。
- **備援離線回答不需要 PG**：qdrant payload 內已有檢索＋回答所需的 metadata（案號／法院／日期／案由／主文＋要旨節錄），這就是「把 metadata 抽出來存」的核心目的。

## 1. 為何 metadata 存 Postgres？（評估結論：是）

與 laws 同理由（laws DESIGN §3 已驗證過）：
- **去重**：`case_no`（裁判字號，如 `112年度訴字第1234號`）唯一鍵，upsert 冪等。
- **增量／不重複 embedding**：靠 `content_hash` 判斷內容是否變過，相同即跳過 re-embed。
- **審計**：`case_import`（source_url、source_sha256、fetched_at、件數）留來源變動軌跡、可重新擷取比對。
- **重建保險**：PG metadata＋parquet 全文 = 隨時可重建 qdrant，不必重抓來源。
- **未來 faceting**：前端篩選（法院層級／年份／案由）或統計直接吃 PG。
- 例外折衷：**主文＋要旨**（回答時必引的短欄位，幾百字級）**同時放 PG 與 qdrant payload**；
  全文（數 KB~數十 KB）只進 parquet，不進 PG、不進 qdrant payload。

## 2. 判決專屬設計點

### 2.1 資料源與選樣（**開放問題，下午研究**）
- 資料源候選：司法院裁判書查詢系統（`judgment.judicial.gov.tw`，JSON/HTML/txt）／
  法學資料檢索（`law.judicial.gov.tw`）下載。
- 選樣範圍建議（對應題庫）：近 3~5 年、民事＋刑事地院/高院、案由子集（詐欺／損害賠償／借貸類…），
  第一階段 **500~1,000 件**（ROADMAP §4.1/§4.4 目標量級，eval 題庫 50 題夠用）。

### 2.2 清洗規則（normalize 階段）
- 去 boilerplate：當事人資訊、訴訟費用、據上論結尾巴等（往往佔判決一半篇幅）。
- 章節標記：`主文`／`事實`／`理由`／`據上論結` 段落結構化（類似 laws 的 chapter 就地帶入）。
- 個資去識別化（判決公開本就已遮，但要再檢查）；回答僅供參考非法律意見。

### 2.3 metadata 抽取
- **第一階段規則式**：案號／法院／日期／案由／案件類型／法院層級——裁判書標題本身結構化
  （「臺灣臺北地方法院民事判決」「112年度訴字第1234號」），regex 即可。
- **進階（可選）**：要旨／爭點摘要用 ollama（qwen3:8b，三台都有）批次補抽；500~1,000 件規模可接受。

### 2.4 切分單元（與 laws 最大差異）
- 判決長文本（數 KB~數十 KB），不能一案一向量（回憶差且 embedding 放不下）。
- 去 boilerplate 後 **800~1200 字切塊＋150 字重疊**；chunk 內文保留「主文/理由」段落名。
- 點 ID：`md5(f"{case_no}-{chunk_idx}")[:8]` → u64（Qdrant 只收 u64/uuid，冪等）。
- 回答時同 `case_no` 的 chunks 可組回整案出處（同 laws 的 article_seq＋chunk_idx 概念）。

### 2.5 Qdrant `cases` collection
- 同 laws：**bge-m3 dense(1024, cosine)＋sparse（BM25 化）**、查詢 DBSF fusion。
- **新增：dense 開 int8 scalar quantization**（laws 沒有，判決點數更多值得做；qdrant 1.19.1 三台支援；
  snapshot 內建 config，備援還原自動一致）。
- payload（每點）：`case_no, court, court_level(地院/高院/最高/行政), case_type(民/刑/行), cause(案由),
  judge_date, 主文節錄, 要旨節錄, chunk_text, chunk_idx, char_len, source, content_hash`。
  **不放全文。**
- payload index：`court_level`、`judge_date`（或 `judge_year`）、`cause`。
- HNSW：`m=32, ef_construction=256`、查詢 `ef=128`（同 laws 量級經驗）。

### 2.6 PG schema 草案（欄位草稿，實作時定稿）
- `case`：`case_no` PK、`court`、`court_level`、`case_type`、`cause`、`judge_date`、
  `verdict_summary`（主文）、`holding`（要旨）、`char_len`、`content_hash`、`updated_at`。
- `case_import`：`source_url`、`source_sha256`、`fetched_at`、`cases_count`、`started_at`、`finished_at`。
- 冪等：`case_no` upsert；`content_hash` 相同就跳過 re-embed。

### 2.7 Parquet 歸檔（eda 階段，duckdb）
- `data/cases/`：`cases_flat.parquet`（一案一列：全 metadata＋清洗後全文）、
  `cases_meta.parquet`；**按年 partition**（抓新年度只加檔）。
- 重建 chunk 時從 parquet 讀原文；原始來源檔保留當上位審計。

### 2.8 同步（sync-snapshot 多 collection 化）
- `sync-snapshot.sh` 已支援 collection 參數，但 `.sync-state` 是**單一檔案會被覆寫**。
- 改 per-collection state：`.sync-state-laws`／`.sync-state-cases`；crontab/launchd 各加一行。

## 3. 量級估算（50G 內極寬裕）

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

## 4. 里程碑

- **M1（ingest 分工）**：`ingest/cases/` 管線——normalize（清洗＋規則式 metadata）→ PG（case/case_import）→
  parquet（按年）→ qdrant_load（`cases` collection、int8 quantization、500~1,000 件選樣）＋
  sync-snapshot per-collection state。
- **M2（backend 分工）**：`rag.py` 查詢擴充——`cases`＋`laws` 雙 collection 檢索（各自 DBSF top_k 再融合）、
  rerank 接真正 reranker（三台已有 bge-reranker）、前端引用渲染判決出處。
- **M3（eval 分工）**：題庫 3→50（含判決題 expect_case）、hit_rate≥0.8。

## 5. 開放問題（下午研究）

1. 判決資料源格式與抓取方式（`judgment.judicial.gov.tw` 哪種格式？有範例檔？）
2. 選樣範圍定案（年份＋法院層級＋案由子集）
3. 要旨是否第一階段就上 LLM 抽取（可選）
4. 全文離線需求：目前備援離線只能給主文/要旨節錄；若要求全文，再評估 parquet 同步一份到 msi/mbp