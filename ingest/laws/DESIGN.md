# ingest/laws 模組架構書（M3）— 法規全量資料管線

> **擁有者：`ingest` agent。** 本檔骨架由 `ARCHITECTURE.md`〈架構表〉建立，
> 內容由 owner 逐題補齊 —— 補不完的題目代表該處有沒被記錄下來的知識，
> 應該在下次動到時補上，而不是先留空。
>
> ⚠️ **`files/` 兩份交接文件把「`M3 rerank() 實作（目前 stub）」列為高優先待辦，
> 那是過期資訊** —— `rerank()` 已實作，真正沒做的是接 cross-encoder 模型。
> 完整更正見 `evals/README.md` §7 末。

## 1. 目標

把官方法規（`law.moj.gov.tw`）變成**可被引用的檢索來源**：
蒐集 → 清洗 → 切分 → 落 PG（metadata）＋ Qdrant（向量檢索視圖），
並且**每日自動更新**、**可被三台備援機獨立使用**。
做到什麼算完成：`POST /eval` 的 `hit_rate ≥ 0.8`（`evals/README.md` §8 門檻）
＋ `data/laws/.law_version` 的 `update_date` 與 x570 一致。

## 2. 邊界（不負責）

- **不改檢索邏輯與 UI**（`ARCHITECTURE.md`〈架構表〉M3 邊界欄）。
  查詢端 hybrid／條號精準分支／rerank 全在 `backend/app/rag.py`（M2）。
  本模組只決定「資料長什麼樣、切多細、payload 有哪些欄位」，
  檢索端怎麼用是 M2 的事。
- 不動 `backend/app/`、`frontend/`
- 環境變數的分類與分發歸 M1；本模組只宣告需要哪些變數
  （`SRC_API_URL` 是 per-machine 必要設定，見 §4）
- 三機部署與排程安裝歸 M6 `ops`；本模組交付的是**能在 host 端跑的指令**，
  由 `scripts/law-update-worker.sh` 觸發（見 §4 陷阱）

## 3. 不變量

壞了會出事、但**測試不一定抓得到**的規則：

- **版本號必須取 `ChLaw.json` 內的 `UpdateDate`**，不是 zip 檔名
  —— 官方 zip 檔名**永遠固定**，用檔名當版本會讓每日同步永遠判定「無更新」。
- **切分塊大小必須與檢索配合**：預設「一條一向量」，只有 >3000 字的條才切塊
  （§9）。亂切會導致上下文丟失、引用不到完整的條。
- **容器內 `data/laws` 是唯讀掛載，寫入一律在 host 端由 cron 進行。**
  在容器裡寫 `data/laws` 會寫到 tmpfs 或被唯讀擋掉，且重啟即消失。
- **Ingest 容器跑不了**（Python subprocess 限制），只能在 host 端用
  `scripts/law-update-worker.sh` 跑。這是架構限制，不是待修的 bug。
- **冪等**：`point_id`／PK／`content_hash` 三道機制讓重跑安全
  （§9 身分、§8 PG）。每日同步任一步失敗即中止且**不回退**，
  靠下次 cron 冪等重跑。
- **上游刪除的點也必須被移除**：qdrant 走**整庫重建**而非 upsert，
  否則法規被刪條後會留下幽靈點。

## 4. 陷阱（實測踩過）

- **`SRC_API_URL` 是 per-machine 必要設定，但三台 8000 全綁 `127.0.0.1`** ——
  腳本無法從別台取版本。`sync-snapshot.sh` 要指定 `SRC_API_URL`，
  否則抓不到版本。
- **`VENV_PY` 千萬不要 `.resolve()`** —— 會追 symlink 到 uv 的 base python
  （沒套件），症狀是 `ModuleNotFoundError: duckdb/asyncpg`。
  一律用根 `.venv/bin/python`（`uv sync` 統一環境）。
- **點 ID 必須是 unsigned int**：Qdrant 只收 `u64`/`uuid`，不能直接用
  `f"{pcode}-{seq}"` 字串。用 `md5(...)[:8]` 轉 u64（§10）。
- **sparse indices 只收 u32**：token 要用 md5 前 4 bytes（Qdrant 限制）。
- **bge-m3 放不下超長／ASCII 膨脹文本**：`_embed_one` 執行時逐筆縮短重試，
  直到放得下。這是為什麼單條 3000 字以上要切塊。
- **抓遠端 `/status` 必須帶 `probe=0`**，否則請求數指數成長（與 M2 共享）。
- **官方法規已全面取代 3 筆 demo 點**；舊點備份在 `laws_demo_backup`，
  不要拿那 3 筆當驗收基準（`hit_rate` 會是假象）。
- ⚠️ **「精簡包」還沒完成**：目前離線可用資料量遠低於目標
  （500~1000 筆的目標見 `ARCHITECTURE.md` 待辦）。做 demo 前先確認
  快照裡實際有多少點，別假設有。

## 5. 驗收

```bash
# 純邏輯測試（不需要 qdrant／pg／網路）
pytest -q tests/test_normalize.py tests/test_law_version.py \
          tests/test_qdrant_chunk.py tests/test_sparse.py tests/test_sync_daily.py

# 版本號有沒有正確寫進 sidecar
cat data/laws/.law_version          # 應有 {"update_date": "...", ...}

# 端到端門檻（需服務在跑）
curl -s -X POST http://localhost:8000/eval | python3 -m json.tool
```

Qdrant `laws` collection 點數與 PG `article` 表行數屬於
**需要活服務的檢查**，列在下方〈跨機檢查〉；`pytest` 那 38 條不需要。

⚠️ **陷阱：PG 沒有 `laws_keywords` 這張表。** 驗證點數時若照抄別處出現過的
「PG `laws_keywords` 表行數」會直接報 relation does not exist。
`ingest/laws/pg_schema.sql` 實際只有三張表：`law`、`article`、`law_import`（§8）。
要查關鍵詞請用 `article` 表。（此錯誤源自 2026-09-27 已刪除的交接文件，
修正後的驗收指令以本檔 §8 為準。）

### 跨機／活服務檢查（需對得上 x570）

```bash
# 點數（容器內）
docker compose exec qdrant sh -c 'wget -qO- --post-data='{}' \
  http://localhost:6333/collections/laws'   # 會展開 result.points_count

# 版本一致性：各台 /health 的 law_version 應與 x570 相同
curl -s http://localhost:8000/health | python3 -m json.tool
```

⚠️ 本機（`msi`）2026-09-27 狀態：`data/laws/.law_version` 的
`update_date=2026-09-18`、`source=http://100.119.83.111:6333`
（= **快照來自 x570**，本機無 `.law_sync.json`，是備援角色）。
`SCOPE.md`〈待套用〉記錄本機 `docker compose ps` 為空，
所以 Qdrant 點數**本機無法驗**，要等容器起來。

---

# 管線設計（詳細）

資料源：`https://law.moj.gov.tw/api/ch/law/json`（官方、含 BOM 的 ZIP，內含 `ChLaw.json`）。
2026-09-11 版：**1347 部**法規、條文 **47,281** 則（A 型態；另 4,062 則 C=章節標題）。

## 6. 清洗規則（`normalize.py`）
- utf-8-sig 解 BOM；CRLF→LF；逐行去首尾空白。
- `pcode` 取自 `LawURL` 末段（A0000001…，唯一；檢查過無重複）。
- 章節標題（Type C）就地轉為後續條文的 `chapter`；張貼 29% 條文無章節，僅當輔助欄。
- 已廢止法規：`is_abandoned=true`（LawAbandonNote 非空）。
- 已刪除條文：「（刪除）」→ `is_repealed=true`（1,019 則），檢索端預設排除。
- 產出：`laws_flat.jsonl`（每條一列）、`laws_meta.jsonl`（每部一列）。

## 7. Parquet 備份（`eda.py`，duckdb 產出）
- `laws_flat.parquet`（47,281 列，~8MB，壓縮自 26MB JSON）、`laws_meta.parquet`。
- 保留原始 ZIP＋`ChLaw.json` 當上位審計與追蹤。
- duckdb 安裝：**uv 為主**——根 `pyproject.toml` 已含依賴，`cd ~/ragdemo && uv sync` 即可（.venv 統一；
  唯 duckdb 惰性 import、且 eda 還需要 numpy/pandas 才有 `.df()`）。

## 8. metadata 存 Postgres（`pg_load.py` ＋ `pg_schema.sql`）
目的：**去重／增量／不重複 embedding**，並為前端篩選、`/hosts` 職能。
- `law`: pcode PK、name、level、category、modified_date、effective_date、is_abandoned、
  has_eng、eng_name、attach_count、foreword、histories、article_count、updated_at。
- `article`: id (pcode+seq) PK、pcode FK、seq、no、chapter、content、char_len、
  is_repealed、is_abandoned（去正規化）、content_hash（內容指紋，增量判斷）。
- `law_import`: source_url、update_date、fetched_at、source_sha256（審計＋重新擷取比對）。
- 全量可用「pcode+seq 為主鍵 upsert」冪等落庫；`content_hash` 相同就跳過再 embedding。

## 9. Qdrant 儲存設計（`qdrant_load.py`）—「精準＋有效率」
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

## 10. ingest 流程（2026-09-23 已實作、x570 上線）
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

## 11. 每日同步（2026-09-24 上線，x570 crontab 06:30）
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
  ⚠️ 這個 14/14 是 n=14，統計上**不足以**宣稱穩定達標，見 `evals/README.md` §6。

### 兩個版本檔的分工（容易搞混）

| 檔案 | 誰寫 | 意義 |
|---|---|---|
| `data/laws/.law_sync.json` | `sync_daily.py`（**只在來源機 x570**） | 「本機曾下載過的版本」 |
| `data/laws/.law_version` | `scripts/sync-snapshot.sh`（快照同步成功後） | 「**實際服務的資料版本**」 |

`rag.py:590-607` 的 `_read_law_version()` **`.law_version` 優先於 `.law_sync.json`**
—— 備援機的 sidecar 代表真正在服務的資料，`.law_sync.json` 只是本機下載紀錄。

⚠️ `scripts/law-update-worker.sh:65-66` 目前**以「有沒有 `.law_sync.json`」隱式判定角色**
（有＝source，跑 `sync_daily.py --apply`；無＝replica，跑 `sync-snapshot.sh --force`）。
這是 scope G 要改成顯式 `HOST_ROLE=source｜replica` 的原因 ——
角色不該靠檔案存在與否推斷。

## 待補（owner）

- **精簡包完成**（`ARCHITECTURE.md`〈待辦清單〉高優先）：
  目標 500~1000 筆法規供 demo 離線使用，現況遠低於此。
  架構：x570 全量 → 精選 → `sync-snapshot.sh` 快照到 mbp/msi。
- **接上 cross-encoder reranker**：`rerank()` 目前是「本地重排」
  （條號精準分支 ＋ dense 相似度降序），不是模型重排。
  `RERANK_MODEL`（`rag.py:42`）已宣告但**沒有任何消費者**。
  這屬 M2／M3 交界，記在 `SCOPE.md` 佇列代號 `H`。**注意**：不要把它寫成
  「`rerank()` 是 stub」—— 那個說法已過期（`files/` 交接文件仍如此寫，見本檔開頭警語）。
- `sync-snapshot.sh` 多 collection 化：`.sync-state` 是單一檔會被覆蓋，
  `cases` 上線後要拆成 `.sync-state-laws`／`.sync-state-cases`（與
  `ingest/cases/DESIGN.md` §2.8 同一件事）。
- 精簡包要定義「精選」規則（哪些法留、依什麼）—— 目前沒有寫下來。