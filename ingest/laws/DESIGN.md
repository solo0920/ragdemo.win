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
- duckdb 安裝筆記：本機 pypi/github 極慢，用 `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple duckdb pandas`。
  建議各機在專案 `.venv`（uv）內建置；目前分析腳本跑在 `/tmp/opencode/venv312`。

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
  payload 同時存稀疏 index 自訂 token；查詢用 `fusion=RRF` 取 top_k。
- 效益：條號級查詢精確命中；法學術語不漏；仍是單次 Qdrant round-trip。

### 效率：預設過濾＋索引
- collection 名字沿用 `laws`；`dense` 維度與現有 1024 一致，不重灌。
- 索引：`payload_index` 建在 `is_repealed`、`is_abandoned`、`pcode`（PQ 前先小卡）。
- 預設查詢條件：`is_repealed=false AND is_abandoned=false`，避免雜訊拉低 rerank。
- HNSW：`m=32, ef_construction=256`，查詢 `ef=128`（量級 5 萬點，足夠快）。

### 身分（upsert 冪等）
`point_id = f"{pcode}-{article_seq}"`；重跑 ingest 只覆蓋內容有變的點（靠 content_hash）。

## 5. 建議 ingest 流程（下一步實作）
```
normalize.py ─→ laws_flat.jsonl ─→ eda.py（parquet 備份）
                                  └─→ PG upsert（law/article，content_hash 比對）
                                        └─→ 需要的新/變更條文 → bge-m3 dense+sparse
                                             └─→ Qdrant upsert（保持 collection 名 laws）
```
注意：現有 `laws` collection 只有 dense 1024；要加 sparse 需「重建 collection＋重灌」，
與 §4.4 評測一起排期（一次灌滿 5 萬點＋評測 hit_rate）。