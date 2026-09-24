# 法規全量数据管線設計（law.moj.gov.tw）

資料源：`https://law.moj.gov.tw/api/ch/law/json`（官方、含 BOM 的 ZIP，內含 `ChLaw.json`）。
2026-09-11 版：**1347 部**法規、條文 **47,281** 則（A 型態；另 4,062 則 C=章節標題）。

## 1. 清洗規則（`normalize.py`）
- utf-8-sig 解 BOM；CRLF→LF；逐行去首尾空白。
- `pcode` 取自 `LawURL` 末段（A0000001…，唯一；檢查過無重複）。
- 章節標題（Type C）就地轉為後續條文的 `chapter`；張貼 29% 條文無章節，僅當輔助欄。
- 已廢止法規：`is_abandoned=true`（LawAbandonNote 非空）。
- 已刪除條文：「（刪除）」→ `is_repealed=true`（1,019 則），檢索端預設排除。
- 產出：`laws_flat.jsonl`（每條一列）、`laws_meta.jsonl`（每部一列）。

## 2. Parquet 備份（`eda.py`，duckdb 產出）
- `laws_flat.parquet`（47,281 列，~8MB，壓縮自 26MB JSON）、`laws_meta.parquet`。
- 保留原始 ZIP＋`ChLaw.json` 當上位審計與追蹤。
- duckdb 安裝：**uv 為主**——根 `pyproject.toml` 已含依賴，`cd ~/ragdemo && uv sync` 即可（.venv 統一；
  唯 duckdb 惰性 import、且 eda 還需要 numpy/pandas 才有 `.df()`）。

## 3. 是否把 metadata 存 Postgres？→ 是
目的：**去重／增量／不重複 embedding**，並為前端篩選、`/hosts` 職能。
- `law`: pcode PK、name、level、category、modified_date、effective_date、is_abandoned、
  has_eng、eng_name、attach_count、foreword、histories、article_count、updated_at。
- `article`: id (pcode+seq) PK、pcode FK、seq、no、chapter、content、char_len、
  is_repealed、is_abandoned（去正規化）、content_hash（內容指紋，增量判斷）。
- `law_import`: source_url、update_date、fetched_at、source_sha256（審計＋重新擷取比對）。
- 全量可用「pcode+seq 為主鍵 upsert」冪等落庫；`content_hash` 相同就跳過再 embedding。

## 4. 如何存 Qdrant 才能「精準＋有效率」
### 切分單元＝條（article）
- 47,281 條中位數 79 字、p90 271、p99 638；只有 **4 則**超過 3000 字。
- 預設「一條一向量」：精準（引用到條）且省空間。
- 超長條（>3000 字）才切塊：以「一、二、三／(一)(二)」為界＋100 字重疊，
  共點共 `article_seq`＋`chunk_idx`，回答可組回整條出處。

### payload（每點）
`pcode, law_name, law_category, article_seq, article_no, chapter, is_repealed,
is_abandoned, char_len, source=moj, updated_at, content_hash`

### 精準：dense＋sparse hybrid
- 法規特性是「條號／專有名詞／精確用語」密集（如「第259條」「違約金」）——
  純 dense 的 bge-m3 對「只知道條號」的查詢召回差。
- **bge-m3 是 hybrid 模型**：dense(1024)＋sparse(lexical)。
- Qdrant 一個 collection 建兩個 vector：`dense`(1024, cosine)＋`sparse`(BM25 化)，
  payload 同時存稀疏 index 自訂 token；查詢用 `fusion=DBSF` 取 top_k
  （不用 RRF：熱門條號兩腿被灌滿時 RRF 只看排名會漏真身，DBSF 分數正規化加總較準）。
- 效益：條號級查詢精確命中；法學術語不漏；仍是單次 Qdrant round-trip。

### 效率：預設過濾＋索引
- collection 名字沿用 `laws`；`dense` 維度與現有 1024 一致，不重灌。
- 索引：`payload_index` 建在 `is_repealed`、`is_abandoned`、`pcode`（PQ 前先小卡）。
- 預設查詢條件：`is_repealed=false AND is_abandoned=false`，避免雜訊拉低 rerank。
- HNSW：`m=32, ef_construction=256`，查詢 `ef=128`（量級 5 萬點，足夠快）。

### 身分（upsert 冪等）
`point_id = f"{pcode}-{article_seq}"`；重跑 ingest 只覆蓋內容有變的點（靠 content_hash）。

## 5. ingest 流程（2026-09-23 已實作、x570 上線）
```
normalize.py ─→ laws_flat.jsonl ─→ eda.py（parquet 備份）
                                  └─→ pg_load.py（asyncpg upsert：law/article/law_import，
                                      source_sha256+UpdateDate 審計；2.4s）
                                  └─→ qdrant_load.py（dense(bge-m3)＋sparse(TF, modifier=idf)，
                                      39,879 條，含條號/章節前綴進向量）
```
- **點 ID 用 unsigned int**（Qdrant 只收 u64/uuid）：`md5(f"{pcode}-{seq}[-c{i}]")[:8]`，仍是冪等。
- **sparse indices 只收 u32**：token 用 md5 前 4 bytes（Qdrant 限制發現後修正）。
- 超長/ASCII 膨脹文本：執行時逐筆縮短重試（`_embed_one`），直到 bge-m3 放得下。
- **查詢端（backend/app/rag.py）已改 hybrid**：`/points/query` DBSF fusion（每個腿 prefetch 500），
  頂層 filter `is_repealed=false AND is_abandoned=false`；old/命名-only collection 自動退回舊 search。
- **條號精準分支**：query 偵測到「第N條」（阿拉伯+中文數字+條之X）時，scroll 拉同條號跨法候選，
  本地「sparse dot＋法名 bigram 重疊×3」計分，取 top3 prepend——破解熱門條號（第11/20條）crowding、
  以及「內容不含法名詞彙引致的同分」與「sparse 腿排到數百名外」的召回問題。
- 收斂結果：`/eval` **hit_rate=1.0**（14/14，expect_law 比對）；公網 ragdemo.win → api-x570 全通。
- 舊 3 筆 demo 點備份在 `laws_demo_backup`（法令資料正式取代 demo 佔位）。

## 6. 每日同步（2026-09-24 上線，x570 crontab 06:30）
- `sync_daily.py`（用根 `.venv`，uv sync 統一環境）：
  1. 每日下載官方 ZIP（~6MB）+ sha256 比對「版本記錄」`data/laws/.law_sync.json`
  2. 無更新 → log 一行心跳，不動任何資料
  3. 有新版 → 完整性優先：舊版工件先備份 `versions/<舊sha8>/` → 驗證(Laws/UpdateDate/條文>0)
     → 縮水 20%+ 中止 → 置換 → normalize → eda(parquet) → 新版工件+ZIP 備份 `versions/<新stamp>/`
     → pg_load → qdrant_load（整庫重建，上游刪除的點也正確移除）
  4. 只要任一步失敗即中止並留 log，不回退（下次 cron 冪等重跑）
- crontab：`30 6 * * * ... --apply`；log `data/laws/sync.log`＋`cron.out`；lock 用 `.sync.lock`(flock) 防重疊。
- **uv 為主**：根 `pyproject.toml`＋`uv sync` → `.venv`（內含 duckdb/asyncpg/httpx/numpy/pandas）；`VENV_PY=根/.venv/bin/python`。
- 坑：`VENV_PY` 勿 `.resolve()`——會追 symlink 到 uv base python（沒套件）。
- 2026-09-24 首跑套用 2026/9/18 版（1347 法規、47,281 A 條；可用 39,879 條），/eval hit_rate=1.0（14/14）。