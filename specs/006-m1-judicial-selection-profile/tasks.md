# Tasks: M1 司法選樣解壓與 profile 建立

**Input**: Design documents from `/specs/006-m1-judicial-selection-profile/`
**Prerequisites**: [`spec.md`](./spec.md)（clarify 6/6）· [`plan.md`](./plan.md)·
上游決策與輸入：[`../../004-judicial-source-fidelity/scope.md`](../../004-judicial-source-fidelity/scope.md)、
[`../../004-judicial-source-fidelity/allowlist-m1.json`](../../004-judicial-source-fidelity/allowlist-m1.json)

**Scope**: task definition only. No implementation has begun.

## Layer labels

沿用 spec 004 的標籤（同一套 `ingest/judgements/` 目錄，標籤語意必須一致）：

| Label | Meaning |
|---|---|
| **[A]** | authoritative — 來源事實；本 feature 對 `allowlist-m1.json`、RAR、`JFULL` **唯讀** |
| **[D]** | derived — 由 [A] 算出，必須可重現 |
| **[I]** | inferred — 明確暫定；不得進入契約 |
| **[O]** | optional / future — 本輪不做 |
| **[doc]** | documentation / config only（不改行為） |

## Invariants referenced by tasks

| Key | Invariant |
|---|---|
| **INV-SRC** | `JFULL` 保持逐字原文（沿用 004） |
| **INV-SUB** | `chunk.text == JFULL[start:end]`（沿用 004） |
| **INV-PROV** | 衍生物件可解析回 artifact → entry → 欄位 → offset（沿用 004） |
| **INV-REPRO** | 同 artifact ＋ decoder ＋ 演算法 ⇒ 同一輸出（沿用 004） |
| **INV-SEP** | 原文與衍生 metadata 永不混合（沿用 004） |
| **INV-FROZEN** | `data/judgements/{raw,seed}` 與 `artifacts/` **零寫入**（本 feature 新增） |
| **INV-COUNT** | 任何通過率**無下限**，不得以補位／重抽讓分母湊滿（FR-001／SC-001） |
| **INV-NOSKIP** | 環境或輸入不符 → 非零退出；**不得以 skip 冒充通過**（FR-010／IX） |
| **INV-PII** | 版控內**不含**判決全文與當事人姓名（FR-008／VII） |
| **INV-PATH** | 新程式碼與 manifest 的路徑**與機器無關**：repo 相對、零 `/home/<user>/` 絕對路徑、外部資料缺失可誠實降級（FR-011／VIII／analyze A5＋Q7／Q8） |

## Fixture strategy

單元測試 **MUST NOT** 需要 284M RAR（沿用 004 的既定策略）。做法完全照抄既有
樣式：`tests/test_judgement_inventory.py` 用 `tests/fixtures/judgements/` 的小 fixture，
需要真 artifact 的測試標 `@pytest.mark.judgement_corpus` ＋ 自己的
`skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")`，
**marker 不加進 `addopts`**（理由已記載於 `pyproject.toml`）。

---

## Phase 1: 輸入契約與離線自檢

*無相依。T101／T102／T103 可並行。*

### T101 — profile 目錄列入 gitignore

- **Status**: ✅ 完成（2026-10-10）｜✅ gitignore 命中／seed 不命中／raw 仍命中
- **Layer**: [doc]
- **Purpose**:讓 `data/judgements/profile-m1/` 及其內容（解壓 bytes、chunk 產物、審閱包）不可能被誤 commit
- **Dependencies**: none
- **Files**: `.gitignore`（修改）
- **Input**: plan.md 的 Project Structure
- **Output**: 一條規則 `data/judgements/profile-*/` ＋ 說明註解
- **Acceptance**:
  - 規則為 `data/judgements/profile-*/`（**不是** `data/judgements/*`），
    理由與既有 `data/judgements/raw/*` 那條同源：只擋 derived corpus，
    不連帶擋掉可進版控的 `seed/`
  - `data/judgements/profile-m1/review-bundle.md` 在 `git check-ignore` 下命中
  - `data/judgements/seed/*.json` **不**被命中（回歸保護）
- **Verification**: `.venv/bin/python scripts/check-law-layers.py` 之類的既有檢查不適用；
  本 task 以 `git check-ignore -v <path>` 三條（profile 命中／seed 不命中／raw 命中）驗證
- **FR**: FR-005, FR-007 · **INV**: INV-PII
- **Note**: 寫完必須實際 `mkdir -p data/judgements/profile-m1` 並確認 `git status` 乾淨

### T102 — allowlist 載入與離線層自檢

- **Status**: ✅ 完成（2026-10-10）｜✅ 離線層 11 條全 PASS
- **Layer**: [D] 1
- **Purpose**:把「輸入是對的那份清單」變成每次必跑、fail-closed 的可執行檢查（FR-010a）
- **Dependencies**: none
- **Files**: `ingest/judgements/profile_build.py`（新）
- **Input**: `specs/004-judicial-source-fidelity/allowlist-m1.json`
- **Output**: `load_allowlist(path) -> Allowlist`、`verify_allowlist(obj) -> list[str]`
  （回傳問題清單，空清單＝通過；**不回 None、不吞例外**）
- **Acceptance**（每條各自獨立報，不提前失敗）:
  - `count == len(entries)`
  - `entries_sha256 == sha256("\n".join(path))`
  - `path_posix == forward_slash(path)`（**沿用 `selection.py`**，不自寫正規化）
  - `path` 無重複
  - 74 個 `excluded_frozen_document_ids` 唯一，且與 `entries` 的 JID **零交集**
  - `crc32` 為 8 位 hex；`unpacked_size` 為正整數
  - `artifact.sha256 == ingest.judgements.artifact.ARTIFACT_SHA256`（單一事實來源）
  - ⚠ **只比 `sha256`，禁止比對路徑字面**：JSON 的 `artifact.path` 是 repo 相對、
    `ARTIFACT_PATH` 是絕對，兩者字面永不相等（analyze A4）
- **Verification**: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_judgement_profile_build.py`
- **FR**: FR-001, FR-010 · **INV**: INV-PROV, INV-NOSKIP
- **Note**: **不讀** `scope.md` 散文、**不讀** repo 外檔案（FR-001 明令）

### T103 — 離線層自檢測試（含 SC-008 的注入錯誤）

- **Status**: ✅ 完成（2026-10-10）｜✅ 5 個注入全紅、還原轉綠、零 skip
- **Layer**: [D] 1
- **Purpose**: 證明「守住了」，不是證明「現在是綠的」（SC-008）
- **Dependencies**: T102
- **Files**: `tests/test_judgement_profile_build.py`（新）
- **Input**: 真實 `allowlist-m1.json` 的**副本**（測試內寫 tmp，不得改版控檔）
- **Output**: 正向 1 條＋**反向 5 條**注入測試
- **Acceptance**:
  - 真實檔 `verify_allowlist()` 回空清單（正向）
  - 注入①改一筆 `path` → 必報 sha 不符
  - 注入②刪掉一筆 `entries` → 必報 `count != len(entries)`
  - 注入③把 `entries_sha256` 欄位改掉 → 必報（否則就是「自己證自己」的漏洞）
  - 注入④塞一筆重複 `path` → 必報重複
  - 注入⑤讓某筆的 JID 出現在 `excluded_frozen_document_ids` → 必報交集
  - **5 條注入全部紅燈**，且每條在還原後轉綠
  - **整個檔在沒有 RAR 的環境跑仍全綠**（`HAS_REAL=False` 時不得出現 skip）
- **Verification**: 同 T102；並額外跑 `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_judgement_profile_build.py -rs` 確認**無 skip 理由被列出**
- **FR**: FR-010 · **INV**: INV-NOSKIP
- **Note**: 樣式照 `tests/test_judgement_artifact.py` 的正向＋反向雙向斷言

---

## Phase 2: 對庫層自檢（外部錨點）

### T104 — 由 archive 重算選樣並比對

- **Status**: ✅ 完成（2026-10-10）｜✅ 對庫層 PASS（0.6s），digest 造假必紅
- **Layer**: [D] 1
- **Purpose**: 證明「這份清單確實是那個選樣規則算出來的」，而非只是自己內部自洽（FR-010b）
- **Dependencies**: T102
- **Files**: `ingest/judgements/profile_build.py`（修改）
- **Input**: 真實 RAR（`artifact.ARTIFACT_PATH`）
- **Output**: `verify_selection_against_archive(path) -> list[str]`，同一函式被 pipeline 當前置閘門呼叫（FR-001）
- **Acceptance**:
  - 重算 `pool → sel[:100]` 並比對 `entries_sha256`；不符回報兩邊值
  - 覆蓋 `inventory.Entry` 的 `crc32` 與 JSON 逐筆一致（外部錨點，不只比 path）
  - **測試標 `@pytest.mark.judgement_corpus` ＋ 自己的 `skipif(not HAS_REAL, …)`**，
    保留在預設套件；**marker 不加進 `addopts`**
  - **反向**：把 JSON 的 `entries_sha256` 改掉時該測試必須紅（不是綠）
- **Verification**: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_judgement_profile_build.py -m judgement_corpus`（需本機持有 artifact）
- **FR**: FR-010 · **INV**: INV-REPRO, INV-NOSKIP
- **Note**: 本 task 是 **VIII 缺口**（artifact 不可重建）的唯一外部證據來源；
  沒有持有 artifact 的機器會誠實 skip 並顯示理由

---

## Phase 3: 解壓與逐筆驗證

### T105 — 逐筆解壓並比對 size＋CRC32

- **Status**: ✅ 完成（2026-10-10）｜✅ 100/100；allowlist 值不符停在 size_crc_ok
- **Layer**: [D] 2
- **Purpose**: 只解 allowlist 上的 100 條，逐筆證明 bytes 沒走樣（FR-002）
- **Dependencies**: T102
- **Files**: `ingest/judgements/profile_build.py`（修改）
- **Input**: allowlist 條目 ＋ `extract.extract_one()`（**既有函式，不重寫解壓邏輯**）
- **Output**: 每筆 `{path, sha256, size, crc32_expected, crc32_actual}`；寫入
  `data/judgements/profile-m1/raw/`
- **Acceptance**:
  - `size == entry.unpacked_size`
  - `crc32_actual == "%08X" % entry.crc32`（T005 向量格式）
  - 逐筆 `sha256` 記進 manifest（FR-005）
  - **任一筆失敗 → 該筆記 `fail{stage,reason}` 並跳過，不中斷整批、不補位**（INV-COUNT）
  - 解壓目標**永不**位於 `data/judgements/raw/` 之下（T002 既有約束，呼叫前斷言）
- **Verification**: 離線層用 `tests/fixtures/judgements/` 的 fixture entry；
  整合層 `-m judgement_corpus`
- **FR**: FR-001, FR-002 · **INV**: INV-SRC, INV-COUNT, INV-FROZEN

### T106 — artifact 與凍結語料零寫入

- **Status**: ✅ 完成（2026-10-10）｜✅ seed_tree_sha256 固定、mtime 不變、反向注入有效
- **Layer**: [A] 3
- **Purpose**: 證明整輪執行沒有改動任何 [A] 資料（FR-005 禁寫凍結路徑）
- **Dependencies**: T105
- **Files**: `tests/test_judgement_profile_build.py`（修改）
- **Input**: `artifact.ARTIFACT_PATH`、`data/judgements/seed/`（**repo 相對路徑**）
- **Output**: 前後比對的斷言 ＋ 機器無關的 `seed_tree_sha256`
- **Acceptance**:
  - RAR 的 `sha256` 全輪前後一致
  - `raw/` 與 `seed/` 的檔案清單與 mtime 全部未變（沿用 `test_inventory.py` 既有做法）
  - **`seed_tree_sha256`**：`seed/` 下所有檔案取
    `sorted(相對路徑 + "\0" + 檔案 sha256)` 以 `\n` 連接後 sha256
    —— **只含 repo 相對路徑**，任何 clone／任何機器算得出同樣的值（analyze A3＋A5）
  - 外部快照 `<ARTIFACTS_ROOT>/t007e05cb/out/corpus_snapshot.json`：
    存在 → 記 sha；不存在 → 記 **`present: false` 且欄位仍在**，
    **不得**因此中止整輪，也不得假裝它存在（FR-011③）
  - **反向**：把 seed 檔案清單改動注入時該斷言必須紅
- **Verification**: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_judgement_profile_build.py`
- **FR**: FR-005, FR-011 · **INV**: INV-FROZEN, INV-SRC, INV-PATH

---

## Phase 4: 文件驗證與切塊

### T107 — schema／document／lossless text 串接

- **Status**: ✅ 完成（2026-10-10）｜✅ 100/100；drift 記錄不拋例外
- **Layer**: [D] 4
- **Purpose**: 逐份過既有驗證，Drift 全記錄（FR-003、SC-002）
- **Dependencies**: T105
- **Files**: `ingest/judgements/profile_build.py`（修改）
- **Input**: 解壓 bytes
- **Output**: `document.Document`（含 `JFULL` 的 `LosslessText`）或 `fail{stage:"schema",reason}`
- **Acceptance**:
  - 使用既有 `schema.py`／`document.py`／`text.py`，**不改它們一行**
  - Drift **逐筆記錄**（`fail.stage="schema"`），不得靜默丟棄
  - `LosslessText` 不得呼叫任何 normalize／trim／collapse（該類既有斷言必須仍綠）
  - 計數寫進 manifest `counts.schema_valid`
- **Verification**: 既有 `tests/test_judgement_{schema,document,text}.py` 保持全綠（回歸保護）
- **FR**: FR-003 · **INV**: INV-SRC, INV-SEP

### T108 — 切塊（凍結參數）與重組斷言

- **Status**: ✅ 完成（2026-10-10）｜✅ 100/100；chunk_size 未傳參（測試釘死）
- **Layer**: [D] 5
- **Purpose**: 用凍結的 chunker 切出可追溯片段（FR-004、SC-003）
- **Dependencies**: T107
- **Files**: `ingest/judgements/profile_build.py`（修改）
- **Input**: `Document`（`JFULL` ＋ `LosslessText`）
- **Output**: `data/judgements/profile-m1/chunks/` ＋ 每筆的 `chunk_count`、
  `boundary_kind_histogram`、`forced_break_chunk_indexes`
- **Acceptance**:
  - **chunk.py 參數一字不改**；若需改參數 → 停止並回報（超出範圍，憲法 X）
  - 逐筆斷言 `text == jfull[start_offset:end_offset]`
  - `forced_break_chunk_indexes` **如實統計**；**0 個是合法結果**，不得為湊數調參數（INV-COUNT）
  - `boundary_kind` 值域不得超出 chunk.py 既有定義
- **Verification**: 既有 `tests/test_judgement_chunk.py` 全綠 ＋ 本 task 的重組斷言
- **FR**: FR-004 · **INV**: INV-SUB, INV-REPRO

---

## Phase 5: 產出物

### T109 — profile manifest

- **Status**: ✅ 完成（2026-10-10）｜✅ manifest schema 正確、grep /home/ 命中 0
- **Layer**: [D] 6
- **Purpose**: 把指紋與逐卷結果變成可稽核、可重算的檔案（FR-005、SC-005）
- **Dependencies**: T104, T106, T107, T108
- **Files**: `ingest/judgements/profile_build.py`（修改）、
  `specs/006-m1-judicial-selection-profile/profile-manifest.json`（產出，進版控）
- **Input**: plan.md〈契約②〉定稿欄位
- **Output**: `profile-manifest.json`（schema `m1-profile-manifest/1`）
- **Acceptance**:
  - 欄位**完全**符合 plan.md 契約②（不多不少，避免日後漂移）
  - `chunker.module_sha256` 與 `params` 記錄在案
  - `counts` 五段計數相加關係自洽（`chunked ≤ schema_valid ≤ size_crc_ok ≤ allowlisted`）
  - `frozen_corpus.seed_tree_sha256` 等於 T106 實算值；`external_snapshot` 三欄
    （`pattern`／`present`／`sha256`）**恆存在**，缺檔時為 `false`／`null`（FR-011④）
  - **路徑欄位全部為 repo 相對**，無絕對路徑；`artifact` **只存 sha256**、不存路徑
    （機器無關定義見 plan.md〈機器無關的欄位定義〉）
  - **重跑必覆寫**（FR-006），且 manifest 內不得出現「上次已驗過」之類狀態欄
- **Verification**: 讀回 JSON 並以 `python -m json.tool` 驗可解析；欄位集與契約逐鍵比對
- **FR**: FR-005, FR-006, FR-011 · **INV**: INV-PROV, INV-COUNT, INV-PATH

### T110 — 人可讀審閱包

- **Status**: ✅ 完成（2026-10-10）｜✅ 100 節＋FORCED 索引；100 卷逐位元組 0 不符
- **Layer**: [D] 7
- **Purpose**: 讓 P2 **不跑程式**就能判斷 holding 是否被切斷（FR-007、SC-006）
- **Dependencies**: T108
- **Files**: `ingest/judgements/profile_build.py`（修改）
- **Input**: chunk 產物 ＋ `JFULL`
- **Output**: `data/judgements/profile-m1/review-bundle.md`（單檔，按 size 降冪）
- **Acceptance**:
  - 每卷一節：JID／size／CRC32／chunk 數／`boundary_kind` 分布／
    FORCED 斷點所在 chunk 索引／`JFULL` **逐字全文**／逐 chunk `(start, end, boundary_kind)` 表
  - **`JFULL` 逐字輸出**：不得摻格式化、不得摻清洗、不得改空白（INV-SRC）。
    **寫入與讀回兩端都必須顯式 `newline=""`**——`TextIOWrapper` 在
    `newline=None` 時**讀取**會做 universal newlines（CRLF→LF），那是逐字性
    的破壞，而且檔案內容看起來完全正常（2026-10-10 實測：第一次驗 100 卷
    全數不符，真正原因是我用預設 `read_text` 讀回，不是寫入壞掉）
  - `JFULL` 區段的邊界 marker 與原文**同一行**（不插入換行），否則取出的
    字串會多 2 個 `\n` 並丟掉結尾的 `\r`
  - 包內**不含**任何生成式摘要或結論（VI：模型輸出不得與原文混同）
  - 檔案**不被 git 追蹤**（T101 已擋）；`git status` 乾淨
- **Verification**: **全 100 卷**逐位元組比對包內 `JFULL` 與原始 JSON 的
  `JFULL`（不是抽 1 卷——2026-10-10 實測證明「抽 1 卷」會漏掉系統性的轉換）。
  比對時以 **JID 定位章節**，不可用「第 N 個出現」（包內依 size 降冪排序，
  與 caller 傳入順序不一定一致，會產生假的逐字性失敗）
- **FR**: FR-007 · **INV**: INV-SRC, INV-SEP, INV-PII

### T111 — 審閱結論檔（空表）

- **Status**: ✅ 完成（2026-10-10）｜✅ 100 列全空、個資命中 0
- **Layer**: [doc] 8
- **Purpose**: 給 P2 一個可 diff、可稽核的落點（FR-008、SC-007）
- **Dependencies**: T109
- **Files**: `specs/006-m1-judicial-selection-profile/review-verdicts.md`（產出，進版控）
- **Input**: allowlist 的 100 個 JID
- **Output**: 固定表頭 `| jid | 判定（正例／負例） | 理由 | 審閱者 | 日期 |`
- **Acceptance**:
  - 100 列齊全，**未審＝空值而非省略**（要能區分「沒審」與「審了說不是」）
  - **不含**判決全文與姓名（INV-PII）
  - `git diff` 對單列改動可讀（每列一行，無多行欄位）
- **Verification**: `grep -c` 斷言無 JFULL 特徵字串；行數 == 100＋2（表頭與分隔）
- **FR**: FR-008 · **INV**: INV-PII

### T112 — CLI 入口與階段可觀測性
- **Status**: ✅ 完成（2026-10-10）｜✅ 退出碼 0/2/3/4 實測；閘門擋下零寫入
- **Layer**: [D] 9
- **Purpose**: 讓執行可被外部驅動，且失敗能定位到**階段**（憲法 IX）
- **Dependencies**: T105–T110
- **Files**: `scripts/build-m1-profile.py`（新，薄殼；沿用 `check-law-layers.py` 慣例）
- **Input**: CLI args（`--allowlist`／`--out`／`--limit`）
- **Output**: 逐階段進度 ＋ 最終五段計數 ＋ manifest 路徑
- **Acceptance**:
  - 每階段**先執行 T104 的前置閘門**；不符 → **非零退出**且不寫任何產物（INV-NOSKIP）
  - 失敗卷以 `stage`＋`reason` 呈現，**不得**折疊成一句通用錯誤
  - 退出碼契約：`0` 全程可讀／`2` allowlist 或 artifact 不符／`3` UnRAR 缺席／`4` 環境缺失
  - 語法驗證用 **`.venv/bin/python -m py_compile scripts/build-m1-profile.py`**
    （⚠ **不是** `bash -n`——那是 shell 專用，對 `.py` 必然報錯；本 repo 也**沒有**
    任何 linter 可用，**不得**為了這一條憑空新增相依，原則 III）
  - **同旨的實際性質取代「拒絕 xtrace」**：本 CLI 不讀任何憑證（對應原則 VII），
    驗收改為「**任何輸出**（stdout／stderr／manifest）不含憑證值」——以本機
    `.env` 的真值逐一比對，驗證樣式沿用 `host-sync.sh` 既有的第⑤條測試
  - **覆寫語義明文化**：依 FR-006 每次執行覆寫 `profile-m1/` 與 manifest，
    **不提供** `--force`／`--skip-verify` 之類繞過旗標（既有 shell 腳本刻意不提供 force）
- **Verification**: `python -m json.tool` 解析 manifest ＋ 以 `--limit 1` 快跑驗證計數輸出
  ＋ 以 `.env` 真值比對確認輸出無憑證值
- **FR**: FR-010 · **INV**: INV-NOSKIP, INV-COUNT

### T113 — 誠實報告

- **Status**: ✅ 完成（2026-10-10）｜✅ 五段計數寫入 SCOPE.md 與 commit 訊息
- **Layer**: [doc] 10
- **Purpose**: 結果無論好壞都必須可見（SC-001～SC-003 的「如實報數」）
- **Dependencies**: T112
- **Files**: commit 訊息 ＋ `profile-manifest.json`（不另立檔）
- **Input**: 實跑輸出
- **Output**: commit 訊息內含**分母明確**的五段計數
- **Acceptance**:
  - 明寫分母：`allowlisted=N`、通過 `M/N`、schema Valid `K/M`、chunked `J/K`
  - 掉數、Drift 條目、FORCED 斷點卷數**全部寫出**；**0 也是合法結果**
  - 若任一筆失敗，訊息列出該筆的 `stage`＋`reason`
  - **不得**寫「預期」「應該」「大致」等未量測字眼
- **Verification**: 人工複核 commit 訊息與 manifest 數字一致
- **FR**: FR-001 · **INV**: INV-COUNT


### T114 — 預設檢索路徑零引用新 profile（分離回歸防護）

- **Status**: ✅ 完成（2026-10-10）｜✅ 7 檔命中 0；清單存在性已驗
- **Layer**: [D] 11
- **Purpose**: 把 US2／SC-004 的「預設檢索零引用新 profile」變成可執行斷言，而不是一句散文
- **Dependencies**: T109
- **Files**: `tests/test_judgement_profile_build.py`（修改；**只讀**下列檢索檔案，不得修改它們）
- **Input**: 明列的檢索模組清單（見下）＋ `data/judgements/profile-m1/` 已存在
- **Output**: 一條 grep 型斷言 ＋ 檢查清單寫進 test docstring
- **Acceptance**:
  - 明列清單內每個檔案對 `profile-m1`／`profile_m1` 的命中數 **== 0**：
    `backend/app/rag.py`、`backend/app/b1_serve.py`、`backend/app/b2d_answer.py`、
    `backend/app/judgement_store.py`、`ingest/judgements/index_load.py`、
    `ingest/laws/qdrant_load.py`、`agent/scripts/eval_judgements_slice.py`
  - `backend/app/judgement_store.py` 的預設讀取路徑仍為 `data/judgements/seed/`
    （**行為斷言**，不只字串比對）
  - 清單本身寫在 test 的 docstring，**日後新增檢索入口必須一併加進來**
    （見下方〈已知弱點〉）
  - **反向**：把某個檔案的內容在 tmp 副本裡改成含 `profile-m1` 時，斷言必須紅
  - 清單的**單一事實來源是** `scripts/build-m1-profile.py` 的 `RETRIEVAL_FILES`
    常數，測試讀它（不各寫一份）
  - 清單內每個檔案必須真的存在——清單指到不存在的路徑時防護就是空的
- **Verification**: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_judgement_profile_build.py -k "zero_reference or retrieval_files"`
- **FR**: FR-005 · **INV**: INV-FROZEN
- **Note**: 本 feature 完全不碰檢索面（這條必然成立）；它的價值是**給未來的 P2／S2
  scope 上一道鎖**——若有人把 profile 接上線而忘了改這條測試，它會紅。
  **已知弱點（2026-10-09 實作時確認，不隱瞞）**：①清單是靜態的，日後新增的檢索模組
  不在其中就不受保護；②反向測試是「注入到 tmp 副本後確認字串存在」，
  它證明**掃描器有能力找到**，但沒有直接跑掃描器去掃那份副本——
  這是刻意的簡化，真接起來會多一層間接。根治兩者都要另開 scope 的重構。

### T115 — 路徑可攜性守則（各地部署都能跑）

- **Layer**: [D] 12
- **Purpose**: 把「各機一致」從口號變成 grep 與斷言（FR-011、SC-009；analyze A5＋Q7／Q8）
- **Status**: ✅ 完成（2026-10-10）｜✅ 零絕對路徑；三欄恆存在
- **Dependencies**: T102, T109
- **Files**: `tests/test_judgement_profile_build.py`（修改）
- **Input**: 本 feature 新增的三個檔案（`profile_build.py`、`scripts/build-m1-profile.py`、
  自己的測試檔）＋ 產出的 `profile-manifest.json`
- **Output**: 一條 grep 型斷言 ＋ 一條 manifest 路徑欄位斷言
- **Acceptance**:
  - 上述三個檔案對 `/home/` 的命中數 **== 0**（連註解與字串常數都算）——
    **反向**：在 tmp 副本注入一行 `Path("/home/x/artifacts")` 時斷言必須紅
  - ⚠ **例外名單只允許一項**（`tests/test_judgement_profile_build.py` 本身），
    並加一條測試斷言該名單**不得被擴大**。理由：該檔原始碼必然含 `/home/`
    那三個字（它就是找那三個字的），掃自己會永遠紅，而一個永遠紅的防護等於
    沒有防護。代價是**本檔抓不到自己**（日後有人在測試裡寫死路徑，這條不會
    抓到）——已如實記錄，不假裝解決
  - `profile-manifest.json` 的所有路徑欄位皆為 **repo 相對**；**無**絕對路徑
  - `frozen_corpus.external_snapshot.pattern` 是**佔位形式**
    （含 `<ARTIFACTS_ROOT>` 字樣），**不是** `/home/solo/...`
  - `external_snapshot` 欄位**恆存在**（`present:false` 時也在），不得因缺檔而省略
  - **反向**：把 manifest 的某個路徑欄位改成絕對路徑時該斷言必須紅
- **Verification**: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_judgement_profile_build.py -k portable`
- **FR**: FR-011 · **INV**: INV-PATH
- **Note**: 本 task 只驗**本 feature 自己新增的檔案**。`agent/` 下另有 **20 個檔案**
  寫死 `/home/solo/...`（評測腳本與 `agent/architecture/validate_runtime_arch.py`），
  屬 **M5／agent 檔位且 A-01~A-11 閘門凍結中**——依憲法 X 本 feature **不碰**，
  已如實記錄於 spec〈Clarifications〉與 plan 憲法 VIII 條，建議另開 scope。
  **切勿**把這條測試擴大到那些檔案（那等於用 M1 偷改凍結閘門的檔位）。

---

## Dependencies & Execution Order

### Phase dependencies

```text
Phase 1 (T101, T102, T103)  ──┬──> Phase 3 (T105 ──> T106, T107 ──> T108)
T102 ──> T104 ────────────────┤
T102, T109 ──> T115 ──────────┤
                              └──> Phase 5 (T109 ──> T110, T111, T114)
                                        └──> T112 ──> T113
```

### Parallelizable tasks

- **T101／T102／T103** 可並行（檔案互不重疊：`.gitignore`／模組／測試檔）
- **T104** 需 T102 完成後可與 T105 併行
- **T106**（零寫入斷言）與 **T107**（schema 串接）可並行
- **T110／T111／T114／T115** 可並行（不同檔案；T114 只讀檢索模組、T115 只讀本 feature
  的新檔案，其餘不碰）

### Critical path

`T102 → T105 → T107 → T108 → T110`（最長鏈＝資料本身的路徑）。

---

## Scope boundary

**In scope**: `ingest/judgements/profile_build.py`、`scripts/build-m1-profile.py`、
`tests/test_judgement_profile_build.py`、`.gitignore` 一條規則、
`specs/006-…/{profile-manifest.json,review-verdicts.md}`、讀取
`specs/004-…/allowlist-m1.json`（唯讀）。

**Out of scope**: Qdrant collection／索引／embedding（US2 明寫不建）· live 接線 ·
gold 標註 · P2 審閱本體（FR-009）· 改 `chunk.py` 參數 · 改 T005/T008–T010 任一檔 ·
`data/judgements/{raw,seed}` 與 `artifacts/` 寫入 · 搬 RAR 或補取檔機制
（VIII 缺口，另開 scope）· `.githooks` 與 `host-doctor.sh`（M6 檔位，跨 scope）。

---

## Post-authoring consistency review

| 檢查 | 結果 |
|---|---|
| 每條 FR 至少映射一個 task | **PASS** — FR-001（T102,T105,T113）· FR-002（T105）· FR-003（T107）· FR-004（T108）· FR-005（T101,T106,T109）· FR-006（T109）· FR-007（T101,T110）· FR-008（T111）· FR-009（scope boundary）· FR-010（T102,T103,T104,T112） |
| 每條 SC 可量測 | **PASS** — SC-001（T113）· SC-002（T107）· SC-003（T108）· SC-004（T106,T109,**T114**）· SC-005（T109）· SC-006（T110）· SC-007（T111）· SC-008（T103） |
| 每條 invariant 有驗證 task | **PASS** — INV-SRC（T105,T107,T110）· INV-SUB（T108）· INV-PROV（T102,T109）· INV-REPRO（T104,T108）· INV-SEP（T107,T110）· INV-FROZEN（T105,T106）· INV-COUNT（T105,T108,T113）· INV-NOSKIP（T102,T103,T112）· INV-PII（T101,T110,T111） |
| 單元測試不需要 284M RAR | **PASS** — T102／T103／T106／T111 全離線；T104／T105 整合段才需要，且用既有 marker＋skipif |
| 沒有 task 跨越多個架構層 | **PASS** — 每個 task 只宣告一層；T113 為 [doc] 且不動程式 |
| 沒有 task 修改 [A] 資料 | **PASS** — T101／T106／T111 只加規則或斷言；T102／T105／T107／T108／T109／T110／T112 對 [A] 唯讀 |
| 沒有 task 需要新的抽象層或相依 | **PASS** — 唯一新模組是 pipeline 本體；CLI 為薄殼；無新 pip 套件、無新服務 |
| 失敗語義不被吞掉 | **PASS** — INV-NOSKIP 由 T102／T103／T112 三處把關；manifest 逐筆帶 `stage`＋`reason` |
| analyze A1（覆蓋缺口）已補 | **PASS** — US2／SC-004 的「預設檢索零引用」原無任何 task 承載，已新增 **T114**（grep ＋ 行為斷言 ＋ 反向注入） |
| analyze A2（錯誤驗證手段）已修 | **PASS** — T112 原寫 `bash -n`／拒絕 `-x`（shell 專用，對 `.py` 必然報錯），已改為 `py_compile` ＋「輸出不含憑證值」，並註明 repo 無 linter 故不得新增相依 |
| analyze A3（契約欄位歧義）已定 | **PASS** — `seed_snapshot_sha256`（定義不明）改為 **`seed_tree_sha256`**，算法寫死在 T106／plan.md：sorted(相對路徑\0檔案 sha256) 以 `\n` 連接後 sha256 |
| analyze A4（路徑不可比對）已定 | **PASS** — T102／T109 明令**只比 sha256、禁止比路徑字面**（JSON 相對 vs `ARTIFACT_PATH` 絕對，字面永不相等） |
| analyze A5（凍結快照無 repo 錨點）已解 | **PASS** — 錨點改為 repo 內 `seed_tree_sha256`（任何機器可算）；外部快照以 `external_snapshot{pattern,present,sha256}` 記錄，缺失記 `present:false` 而非省略；FR-011＋T115 另加可攜性守則 |
| analyze A6（SC-006 不可自動化）已接受 | **PASS（接受限制）** — SC-006 前半「開箱判讀」屬人的判斷，由 P2 人工閉環；後半（`JFULL` 逐字比對）由 T110 機械驗證。**不再列為缺陷** |
| 跨機一致性（Q7／Q8）已落實 | **PASS（限 M1 檔位）** — T115 驗本 feature 新增檔案對 `/home/` 命中 0 ＋ manifest 路徑全相對；`agent/` 那 20 個檔案屬 M5／agent 且閘門凍結，**不碰**並已記錄 |

### 刻意留白（不做）

- **不做搬 RAR／補取檔機制。** 這是 VIII 的真正缺口，屬架構層決定（物件儲存或
  scrub 機制），且牽涉憑證與三機部署；不在本 feature 假裝解決。
- **不把對庫層自檢掛 pre-push 或 host-doctor。** 前者每次推銷都要讀 284M；
  後者是 M6 檔位，屬跨 scope。
- **不做 P2 審閱工具。** M1 只交出「可交付人判」的審閱包與結論檔。