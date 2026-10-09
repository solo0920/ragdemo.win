# Feature Specification: M1 司法選樣解壓與 profile 建立（DRAFT）

- Status: Draft（specify 階段；未 clarify／plan／tasks／analyze／implement）
- Scope: SCOPE.md 待開 M1（北部民事 100 卷跨 chunk 正例候選池）。
  前置已定：(1) UnRAR 7.13 安裝完成（`~/.local/bin/unrar`，已驗收）；
  (2) T007 `specs/004-judicial-source-fidelity/scope.md`（100 paths 可重現）。
- Constitution Check（specify 階段先行自查）：I 小切片（100 卷封頂、可獨立驗收）、
  III 不造新抽象（沿用 ingest 既有零件＋新 profile 目錄慣例）、VI＋XI
 （逐字保真、hash 驗收、拒絕偽造；候選≠正例，審閱另行授權）。無已知違規；
  plan 階段再逐條 I–XI 複查。

## User Scenarios & Testing

### User Story 1 — 100 卷解壓驗證入庫待審（Priority: P1）

操作者按 scope.md allowlist 解壓 100 個 RAR entries，每份過 size＋CRC32＋
schema 驗證，chunk 後寫入新版 profile（含 manifest＋fingerprint），供 P2 人審。
任一失敗即停該卷、不污染已入庫。

驗收：通過 size＋CRC32 者 `N/allowlist`（N 如實報，無下限）；其中 schema Valid 者
`M/N` 且 **Drift 全記錄無遺漏**；chunk 重組斷言全過（分母＝實際入庫者）；
profile manifest fingerprint 已記錄；凍結 430 語料零寫入。

### User Story 2 — 新舊 profile 並存可辨（Priority: P2）

凍結 corpus 與新 profile 在**磁碟路徑與 manifest** 上完全分離；任一查詢預設仍只讀
凍結 corpus。**本 spec 不建、不寫任何 Qdrant collection**（索引屬後續 scope），
因此「collection 名分離」不在此列。

驗收：凍結快照 sha 不變；新 profile 有獨立 fingerprint；預設檢索路徑
零引用新 profile（grep＋測試斷言）。

### Edge Cases

- 條目損壞／schema Drift／重複 JID：該卷跳過＋記錄，不中斷整批，不補位湊數。
- 解壓後發現 allowlist 選錯（無 FORCED 斷點）：照實記錄為負例，不換選樣規則重抽。
- UnRAR 缺席的機器：腳本開頭即報缺失並 exit 非零，不靜默跳過。

## Requirements

### Functional Requirements

- FR-001：僅解 allowlist 上的 entry paths（**ceiling，非目標下限**；少於 allowlist
  長度就如實報數，不補位、不換選樣規則重抽）。allowlist 必須是**版控內的機器可讀
  檔案**（由 `specs/004-…/scope.md` 的 header-only 指令重生成，附完整 sha256）；
  不得依賴 `/tmp` 等 repo 外的暫存，也不得由程式去解析 `scope.md` 散文。
- FR-002：沿用 T005 向量（size＋CRC32 `%08X`＋artifact digest 前後不變）。
- FR-003：沿用 T008–T010（schema／frozen document／lossless text），Drift 即跳過。
- FR-004：沿用凍結 `chunk.py`（不改參數），chunk 產物帶 offsets＋boundary_kind。
- FR-005：新 profile 目錄＋manifest（artifact／decoder／selection／chunker
  fingerprint＋時間戳），禁寫凍結路徑。
- FR-006：P2 審閱在本 spec 之外；本 spec 不產 gold、不升級 APPLIED。

### Key Entities

- allowlist（100 entry paths＋manifest sha）、profile（含 manifest＋fingerprint）、
  UnRAR 7.13（`~/.local/bin/unrar`，已驗）。

## Success Criteria

### Measurable Outcomes（技術無關、可量測）

- SC-001：allowlist 中通過 size＋CRC32 的筆數**如實報出**（分母＝allowlist 長度，
  目前 100）。**通過率無下限**——掉數是必須被看見的結果，不是失敗；
  任何時候都不得以補位或重抽讓分母湊滿。
- SC-002：schema Valid 數與 Valid 率皆如實報（分母＝實際解壓成功者，非 allowlist）；
  Drift **全記錄無遺漏**（有 Drift 就報，不靜默丟棄）。
- SC-003：chunk 重組斷言全過（分母＝實際入庫者）；FORCED 斷點卷數如實統計
  （不設下限——0 個也是合法結果，只代表這批長卷宗切不出跨 chunk holding）。
- SC-004：凍結語料 sha 前後一致；預設檢索零引用新 profile。
- SC-005：profile manifest fingerprint 已記錄且可重算。

## Assumptions

- UnRAR 已安裝（完成）。allowlist 可重現（重跑指令實測：母體 25,986 → 選得 100 條，
  zero extraction）。P2 人審、promotion、live 接線全部不在本 spec。
