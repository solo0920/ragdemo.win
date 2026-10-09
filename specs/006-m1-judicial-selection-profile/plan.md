# Implementation Plan: M1 司法選樣解壓與 profile 建立

**Branch**: `main`（本 repo 慣例：不開 feature 分支，見 006 spec 記錄） | **Date**: 2026-10-09 | **Spec**: [spec.md](spec.md)

**Input**: `specs/006-m1-judicial-selection-profile/spec.md`（clarify 6/6 已答）

## Summary

把 T007 已定案的 100 條 entry path（`specs/004-judicial-source-fidelity/allowlist-m1.json`）
解壓成逐字保真的判決文件，跑既有的 schema／lossless 驗證與**凍結的** chunker，
落到 `data/judgements/profile-m1/`（整個目錄 gitignore），並產出兩份可稽核的東西：

1. **進版控**：`profile-manifest.json`（artifact／decoder／selection／chunker 指紋＋逐卷結果）
   與 `review-verdicts.md`（審閱結論，小、無個資、可 diff）。
2. **不進版控**：解壓 bytes、chunk 產物、**人可讀 Markdown 審閱包**（含姓名全文）。

技術路線是**組合既有零件，不造新機制**：T005 `extract.py`（size+CRC32＋artifact
digest 不變）→ T008 `schema.py`／T009 `document.py`／T010 `text.py` → T015 `chunk.py`
（不改參數）。新的只有一個 pipeline 模組 ＋ 一支 CLI 薄殼，沿用 FR-007 剛建立的
`ingest/laws/layer_check.py` ＋ `scripts/check-law-layers.py` 慣例。

**不做**：Qdrant 索引、embedding、live 接線、gold 標註、P2 審閱本體。

## Technical Context

**Language/Version**: Python 3.12（repo `.venv`）

**Primary Dependencies**: 零新增。既有 stdlib ＋ `inventory/extract/schema/document/text/chunk`
六個模組；外部程式的唯一依賴是 UnRAR 7.13（`~/.local/bin/unrar`，不在 PATH 時用絕對路徑）。

**Storage**: 檔案系統。`data/judgements/profile-m1/{raw/,chunks/,review-bundle.md}`
（gitignored）＋ `specs/006-…/profile-manifest.json`（版控）。

**Testing**: pytest（`.venv/bin/python -m pytest -q -p no:cacheprovider`）；
單元測試用 `tests/fixtures/judgements/` 既有 fixture，**不碰 284M RAR**。

**Target Platform**: Linux（x570 實跑；mbp/wsl 見 VIII 條）。

**Project Type**: CLI ＋ 資料管線（無 API、無前端）。

**Performance Goals**: 無硬性目標。實測基準：header 掃描 108,547 entries 數十秒；
100 筆解壓約 15MB，量級在人工可等待內。

**Constraints**:
- 解壓目標**永不**位於 `data/judgements/raw/` 之下（T005 既有約束）。
- 對 `data/judgements/{raw,seed}` 與 `artifacts/` **零寫入**（凍結語料與 T006 產物）。
- 任何驗證失敗 → 該卷跳過並記錄，**不中斷整批、不補位**（FR-001／SC-001）。
- fail-closed：環境或輸入不符 → exit 非零，**不得靜默跳過**。

**Scale/Scope**: 100 entries／1 court-family 4 courts／1 period（202607）／約 15MB。

## Constitution Check

*GATE：Phase 0 前必須通過；Phase 1 設計後複查一次。複查點見下方〈複查〉。*

| 原則 | 判定 | 本 feature 的落實／依據 |
|---|---|---|
| **I** 垂直切片 | ⚠ **PARTIAL** | 見〈Complexity Tracking〉第 1 列——本 slice 的終端消費者是**人（P2 審閱）**不是 API 使用者，鏈路止於「可解、可驗、可切、可交付人判」。這是 maintainer 已裁定的範圍縮短（spec FR-009／T007 決策），非本 plan 自行決定。 |
| **II** 穩定核心／可換零件 | ✅ | UnRAR 只經既有 `decoder.py` 這個 adapter 進出（該檔已釘 source url＋sha256＋版本）；pipeline 不直接 spawn `unrar`。無 provider 細節滲入核心。 |
| **III** 簡單優先 | ✅ | 不新增抽象層、不新增設定系統、不做 plugin。唯一新模組是**管線本體**（無它則需求無法達成）。沿用 FR-007 的 `layer_check.py` ＋ CLI 兩段式，不自創樣式。 |
| **IV** 契約先於實作 | ✅ | 三份契約先定：①`allowlist-m1.json`（schema `m1-allowlist/1`，已存在且自證 `entries_sha256`）②`profile-manifest.json`（新 schema，欄位由本 plan 列出）③`review-verdicts.md`（每卷一行的固定欄位）。FR-001 明令不得由程式解析散文當契約。 |
| **V** 測行為不測實作 | ✅ | 測試對**檔案內容與退出碼**斷言（可觀察行為），不對內部函式呼叫序斷言。SC-008 的 5 個注入錯誤是「行為測試」的具體化。 |
| **VI** RAG 正確與可追溯 | ✅（不適用邊界內） | 本 feature **不產生、不檢索、不生成**任何答案，故不可能偽造引用。可追溯性落在：每 chunk 帶 `start_offset/end_offset/boundary_kind`，且審閱包讓人能**重新切片對照**原文（=XI 要求的 re-slice 證明）。 |
| **VII** 安全與資料邊界 | ✅ | 本批含**當事人姓名**：審閱包與 bytes 一律 gitignore（FR-005／007），版控內只留無個資的結論（FR-008／SC-007 以 grep 驗）。不碰任何憑證、不新增網路服務。 |
| **VIII** 可重現環境 | ⚠ **有前置缺口** | 零新依賴、零新服務 ✅。但**284M RAR 本身不在 repo 且不可重建**（`data/judgements/raw/*` gitignored，無 sync 機制；T006 的 494 筆 manifest 也在 repo 外）。本 feature 因此只在**持有該 artifact 且 sha256 相符**的機器可跑（`artifact.py` 已釘 `ef35ce44…`）。此缺口是既有的，plan 只如實標記，不假裝已解決。 |
| **IX** 可觀測可診斷 | ✅ | manifest 逐階段計數：`allowlisted → extracted → size_crc_ok → schema_valid → chunked`，失敗卷附 `stage`＋`reason`。CLI 逐階段印行，失敗可定位到階段（呼應 IX「failures SHOULD identify the failed stage」）。 |
| **X** AI agent 紀律 | ✅ | 檔案範圍鎖定於 `ingest/judgements/`、`scripts/`、`tests/`、`.gitignore`、`specs/006-…/`。**凍結 430 語料、B2/B3/B4 判定、Qdrant、frontend 一律不碰**。發現必要的外溢 → 停下報告，不自行擴張。 |
| **XI** 司法來源不可變（NON-NEGO） | ✅ | ①`JFULL` 經 T010 `LosslessText`，**無 normalize/trim/collapse 方法**（該檔既有斷言 CRLF／U+3000 計數不變）；②每 chunk 滿足 `text == jfull[start:end]`（既有重組斷言）；③解壓以 size＋CRC32 `%08X` 逐筆比對，artifact digest 前後不變；④審閱包**逐字輸出 `JFULL`**，不得摻格式化或清洗。 |

**複查（Phase 1 設計後）**：I（是否仍為 PARTIAL、依據是否變）、VIII（缺口是否有變化）、
IV（三份契約的欄位是否定稿）——其餘七條設計不會改變判定。

## 契約（Phase 1 定稿欄位）

**① `allowlist-m1.json`**（已存在，本 feature **唯讀**）：`schema`／`generated_at`／
`artifact{path,sha256}`／`selection{courts,period,sort,pool_total_4courts_civil,
excluded_frozen_in_pool,pool_after_exclusion}`／`excluded_frozen_document_ids`（74）／
`count`／`entries_sha256`／`entries[{path,path_posix,unpacked_size,crc32}]`。

**② `profile-manifest.json`（新）**：
`schema:"m1-profile-manifest/1"`／`generated_at`／`allowlist{sha256,count}`／
`artifact{sha256}`／`decoder{version,source_url,source_sha256}`／
`chunker{module_sha256,params}`／`counts{allowlisted,extracted,size_crc_ok,schema_valid,chunked}`／
`entries[]`（每筆：`path`／`sha256`／`size`／`crc32_expected`／`crc32_actual`／
`stages{extracted,size_crc_ok,schema_valid,chunked}`／`chunk_count`／
`boundary_kind_histogram`／`forced_break_chunk_indexes`／`fail{stage,reason}`）／
`frozen_corpus{seed_snapshot_sha256,unchanged:bool}`。

**③ `review-verdicts.md`（新，進版控）**：表頭固定為
`| jid | 判定（正例／負例） | 理由 | 審閱者 | 日期 |`，**不含全文與姓名**；
未審＝空值而非省略（，讓「沒審」與「審了說不是」可區分）。

## Project Structure

### Documentation（本 feature）

```text
specs/006-m1-judicial-selection-profile/
├── spec.md                  # 已完成 clarify（6/6）
├── plan.md                  # 本檔
├── profile-manifest.json    # 產出物，進版控（執行後）
├── review-verdicts.md       # 產出物，進版控（P2 填，初始全空）
└── tasks.md                 # /speckit.tasks 產出（不在本檔）

specs/004-judicial-source-fidelity/
├── scope.md                 # T007 決策記錄（本 feature 的上游）
└── allowlist-m1.json        # 輸入，唯讀
```

### Source Code（repo 根）

```text
ingest/judgements/
├── profile_build.py         # 新增：pipeline 本體（載入／驗證／解壓／切塊／產出）
└── （inventory, extract, schema, document, text, chunk, selection, decoder, artifact 皆不動）

scripts/
└── build-m1-profile.py      # 新增：CLI 薄殼（沿用 check-law-layers.py 慣例）

tests/
└── test_judgement_profile_build.py   # 新增：離線層自檢＋注入錯誤＋假 fixture 流程

.gitignore                   # 修改：加 data/judgements/profile-*/

data/judgements/profile-m1/  # 新增目錄（整個 gitignore）
├── raw/                     # 解壓 bytes（逐字，不改寫）
├── chunks/                  # chunk 產物（含 offsets／boundary_kind）
└── review-bundle.md         # 人可讀審閱包（含姓名全文，不進版控）
```

**Structure Decision**：沿用 repo 既有的「模組在 `ingest/<domain>/`、可執行入口在
`scripts/`、測試在 `tests/` 頂層」三段式（FR-007 的 `layer_check.py` ＋
`check-law-layers.py` ＋ `test_law_layer_check.py` 即為最近先例）。
**不加** `src/`、`backend/`、任何新目錄層級。

## Complexity Tracking

> 僅記錄 Constitution Check 中**非全綠且需justify** 的項目。

| 項目 | 為什麼需要 | 更簡單的替代方案為何被拒 |
|---|---|---|
| **I 垂直切片 PARTIAL** | 本 slice 的消費者是 P2 審閱者（人），鏈路止於「判決 bytes → 逐字保真 → 切塊 → 可交付人判的審閱包」。做到 API 端到端需要 Qdrant 索引＋檢索＋生成，那是 P2／S2 的範圍，且 maintainer 已明確裁定 P2 審閱不在本 spec（FR-009）。 | 「為了 I 而硬接 live」會把 M1 變成第二個 M0：同時動索引與服務面、擴大檔位、且在**正例都還沒找到**之前就接上線——那是在沒有證據的位置先蓋房子。 |
| **VIII artifact 不可重建** | 100 卷的真實來源是 284M RAR，`data/judgements/raw/*` gitignored 且**無 sync 機制**（`data/laws/` 有 `sync-snapshot.sh`，`data/judgements/` 沒有）。因此新 clone／其他機器無法重跑本 feature。 | 唯一真正的解法是把 RAR 搬進可分發的儲存（物件儲存／scrub 機制）或補一條取檔路徑——兩者都是架構層決定，遠超本 scope，且涉及憑證與三機部署。**已記入待辦，不在本 feature 假裝解決。** |

## 待確認（不阻擋 plan，tasks 前需一句話）

1. **對庫層自檢掛哪**：FR-010(b) 的「由 archive 重算比對」**保留在預設測試套件**
   （`@pytest.mark.judgement_corpus` ＋ 每條測試自己的 `skipif`，marker **不**加進
   `addopts`），同時是 pipeline 的前置閘門（FR-001）。這是 maintainer 於 plan 後
   追認的預設，並**駁回**了本 plan 初稿「不放進預設 pytest」的寫法——後者與
   `pyproject.toml` 已記載的決策相反（理由見 FR-010(b)）。
   **不**掛 `.githooks/pre-push`（每次推銷都要讀 284M，數十秒，且多數 commit 不碰
   allowlist）；**不**塞 `host-doctor.sh`（那是 M6 的檔位，屬跨 scope，需另開 scope
   並由 ops agent 處理）。
2. **P2 審閱包的粒度**：100 卷全部渲染成單一 `review-bundle.md`（可能數 MB），
   或是每卷一檔＋索引。預設單檔（排序＝size 降冪，便於從最長的看起）。

## Definition of Done（對應 spec）

- SC-001～SC-008 全部有**實跑輸出或注入錯誤的紅燈證據**（非推論）。
- `pytest -q` 全綠（基準 1723 passed 不回歸，且新增測試通過）。
- manifest 的 `frozen_corpus.unchanged` 為 `true`，且 `data/judgements/seed/` sha 前後一致。
- `git status` 乾淨；**不 push**。
- 誠實報告：掉數、Drift、FORCED 斷點為 0 等結果一律照實寫入 commit 訊息與 manifest。