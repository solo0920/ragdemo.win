# Feature Specification: S1 判決三層儲存（parquet 原文 ＋ postgres metadata ＋ qdrant 向量）

- Status: Draft **rev4**（**specify 階段**；未 clarify／plan／tasks／analyze／implement）
  2026-10-10 · rev4 納入 review 後的三處修正（§實測基線 §3 事實錯誤、SC-009 分支、
  SC-002 雙向驗證）與兩個流程決定（S1-3 exit criteria、manifest 納入版控）。
  異動見文末〈修訂記錄 rev3 → rev4〉。
  `【建議・待裁】`＝尚未裁決；`【待裁決】`＝**阻擋性**未決項（Open Question 8）。
- Scope: 待開 `S1`。**獨立於 M1**（M1 已於 2026-10-10 關閉，產出 100 卷 profile；
  本 spec 消費那份產物，**不重做解壓**）。
- **六個 maintainer 裁決已定**（見〈裁決記錄〉）
- **Constitution Check（specify 階段先行自查）**：
  I 小切片（parquet → postgres → qdrant 逐層可獨立驗收）／
  III 不造新抽象（沿用既有 `asyncpg` ＋ `httpx` 直打 Qdrant）／
  VI＋XI 原文逐字、每個衍生值可回溯、拒絕偽造／
  VIII 零新增服務、`pyarrow` 為唯一新依賴且已記錄理由。
  已知張力見〈與既有凍結物的關係〉。**plan 階段再逐條 I–XI 複查**。

---

## 這份要解決什麼

**一句話**：把判決資料從「解壓在磁碟上、靠 grep 找」變成「三層各有其職、
查得到也查得準、且原文永不被改寫」。

| 層 | 存什麼 | 為什麼是它 | 誰能重建它 |
|---|---|---|---|
| **parquet** | `JFULL` **逐字原文** ＋ 8-key，按 `year=YYYY/month=MM` 分區、snappy | 原文是 authoritative。它是**唯一**能證明「其他兩層沒改寫原文」的東西 | **不能**（它是源） |
| **postgres** | 可篩選 metadata，每欄帶 provenance | 支撐 pre-filter：「只查 114 年後、簡易庭、引用民法§767」 | 可從 parquet 重建 |
| **qdrant** | 結構切分 chunk 向量 ＋ context header | 檢索用，**純衍生** | 可從 parquet 重建 |

**分層的實質意義**：沒有任何一層是另一層的「唯一真相」。這正是為什麼原文
必須有一層不能被衍生污染——否則三層都是二手資料，沒有人能當仲裁者。

## User Scenarios & Testing

### User Story 1 — 原文可查、且證明未被改寫（Priority: P1）

研究者要取得某一份判決的**逐字原文**（用於引用、比對或法律研究），且必須能
**證明**這份原文與 archive 內的 bytes 一致。

驗收：任取 20 筆 parquet 記錄，`jfull_sha256` == 從 archive 重算的
`entry_sha256`；且 `jfull_char_len`／`crlf_count` 與原文實測一致。
**這兩條合起來就是「清理沒有污染這一層」的機器證明。**

### User Story 2 — metadata 可 pre-filter（Priority: P1）

使用者想找「114 年以後、臺灣新北地方法院、引用民法§767」的案件。這些條件
全部是**結構化欄位**，不該靠向量檢索猜。

驗收：postgres 具備 `court`／`case_no`／`judgment_date`／`laws[]` 等欄位，
**每個欄位都能回溯到來源**（`court` ← 目錄、`laws` ← qdrant payload）。
一次該條件的查詢能回傳結果，**且不解壓任何檔案**。

### User Story 3 — chunk 可反查回原文（Priority: P1）

向量檢索命中一個 chunk，使用者要能取到「它在原文的哪一段」——**逐字**，
不是清理後的版本（清理過的文本不能拿去當引用原文）。

驗收：每個 qdrant point 帶 `(jid, section_path, chunk_id, char_start, char_end)`；
以該區間回 parquet 的 `jfull` 取**原文切片**（**單位為字元**；CRLF 的 `\r\n` 算 2 字元），
並通過兩條斷言：
① 切片是 `jfull` 的連續子串（區間合法、不越界）；
② `clean(切片) == payload.text`——`clean` 指**該 `parser_version` 對切片做的清理**。spec 不綁其內部實作，
但要求它是**單一具名、有版本、可單獨呼叫、決定性**的函式。切片起點若落在段落中間，
清理結果可能與「整份文件清理後再取該段」不同；**容差於 offset 切片實測後才定義**
（見〈實作切片順序〉），在此之前不得把 ② 寫成絕對斷言。
⚠ **不得**要求「切片 == payload.text」——`text` 是清理版（見 FR-012、Open Question 2）。

### User Story 4 — 新舊兩套 chunker 並存（Priority: P2）

既有 seed 索引與 B2/B3/B4 的 eval 基線**不能因為本 spec 壞掉**；同時新的結構
切分要能獨立檢索。兩者共存於不同 collection。

驗收：既有 `chunk.py`（凍結）與結構切分各自對應不同 collection；
既有 `pytest` 全綠、既有 eval 數字不變。

### Edge Cases

- **解析 `partial` 的卷**：仍入庫（原文完整），但 metadata 標 `parse_status` 與
  `parse_issues`。**不得**因為 metadata 不全就丟掉原文。
- **上游截斷的原文**（實測已有 1 例）：標 `possibly_truncated_source`，
  **不硬解析**；原文照存。這是資料品質問題，不是 parser 問題。
- **裁定（無言詞辯論）**：`hearing_closed` 為空是**正常**，不降級 `parse_status`。
- **parent 錯配**（實測 20 個 child 的 `text` 不在 `parent.text` 內）：成因有二，
  必須分開處理——**8 個**是重複編號使後者覆蓋前者（真 bug，修法＝唯一 uid，見 FR-008）；
  **12 個**是「空殼標題併入下一塊」使 child 開頭多了上層標題（修法＝標題另存
  `heading_prefix`，不混進 `text`）。`parent_id` 必須指向該 child 真正所屬的章節 uid；
  **錯配比缺失更隱蔽**（不會報錯但回傳錯上下文）。
- **缺「事實及理由」標題但結構完整**（實測 2 例）：以 `壹、`／`甲、` 為起點照常解析，
  記為 `parse_notes`（資訊），**不降級** `parse_status`（見 FR-006）。
- **非民事／刑事／行政**【UNVERIFIED】：`section_type` 規則不同，標頭不含「民事判決／裁定」
  者標 `unsupported_court_type`（issue，降 `partial`），**且該文件所有 chunk 的 `section_type`
  一律為 `unclassified`**——不得帶民事語意的標籤。（實測：把樣本標頭改成「刑事判決」後，
  v2 初版仍切出 70 個帶 `plaintiff_claim`／`court_reasoning` 的 chunk，這就是「靜默套用民事規則」，
  已修。）此分支在 100 卷**民事**樣本內**無任何真實案例**；上線前須對高院、刑事、行政判決
  **各抽 20 份**驗證，並通過負向測試（SC-012）。
- **pyarrow 未安裝**：即報缺依賴並 exit 非零。

## Requirements

### Functional Requirements

- FR-001：**parquet 層**——每份判決一列，欄位至少含 `jid`／`jyear`／`jcase`／
  `jno`／`jdate`／`jtitle`／**`jfull`（逐字）**／`jpdf`／`entry_path`／
  `entry_sha256`／`artifact_sha256`／`jfull_sha256`／`jfull_char_len`／
  `jfull_byte_len`／`crlf_count`。**此層不得做任何清理**（裁決 ③）。
- FR-002：**分區鍵取自 `jid`**——`jid` 形如
  `TPDV,109,金,24,20260731,4`，第 5 欄是 8 位裁判日期。**實測 100/100 可用**
  （§實測基線 §1），因此分區**不依賴任何 JFULL 解析**。
  分區路徑 `year=YYYY/month=MM/`；取不到值者落 `year=unknown/`，**不猜**。
  檔內依 `court` 排序，用 parquet row-group 的 min/max 統計做 court 過濾
  （裁決 ④：**兩層分區，不用三層**）。
- FR-003：parquet 的 schema 版本、壓縮 codec（snappy）、partition 路徑寫進
  manifest；**重跑同樣輸入必須產生逐位元組相同的檔案**。
- FR-004：**postgres 層 `judgment_meta`**——`jid` **同時是 PK 與 FK**（指向
  `judgment(jid)`），由資料庫保證一對一。欄位：`doc_type`（`judgment`／`ruling`）／
  `court`／`court_raw`／`court_in_text`／`case_no`／`case_type`／`hearing_closed`／
  `judgment_date`／`cause`／`judges`／`clerk`／`outcome_rule`／`costs_to`／
  `provisional_execution`／`appeal_days`／`amounts_twd[]`／`laws[]`／`issue_tags`／
  `parse_status`／`parse_issues[]`／`parse_notes[]`／`parser_version`／`trailing_chars`。
  **court 三個欄位的語意**（rev2 釐清）：`court_raw`＝archive 目錄原字串
  （如 `臺灣新北地方法院民事`）；`court`＝由目錄正規化的**地院級**值；
  `court_in_text`＝判決書標頭所載（可到庭級，如 `臺灣新北地方法院三重簡易庭`），
  **僅供參考，不參與 SC-002**。
  **每欄帶 provenance**（`court`／`court_raw` ← 目錄；`court_in_text` ← JFULL 標頭；
  `laws` ← qdrant payload；其餘 ← parser＋`parser_version`）；推論不出來存 NULL，
  **不填猜的值**。
- FR-005：**「有原文、沒 metadata」是合法狀態**——解析失敗也要**寫一列**
  （`parse_status='failed'` ＋ `parse_issues`），**不得**用「列不存在」表示。
  規則修好後可**只重跑 `partial`／`failed`**（靠 `parser_version` 篩）。
  **`parse_issues`／`parse_notes` 分級（兩問規則，雙方共識）**：問「這個旗標是否表示資料
  缺失、可疑，或規則不適用？」——是 → `parse_issues`（降級）；否（parser 走了備援路徑但結果
  完整）→ `parse_notes`（不降級，但**保留記錄與計數**以監控規則變異）。
  理由：把已完整解析的卷標 `partial`，會讓「只重跑 partial」永遠白跑（實測 2 卷）。
- FR-006：**關鍵欄位依文書類型定義**（裁決）：`judgment` 必填 `case_no`／
  `court`／`judgment_date`／`hearing_closed`／`judges`／`clerk`；
  `ruling`（裁定）免 `hearing_closed`。**缺任一必填 → `parse_status='partial'`**
  （不是 `ok`——`ok` 必須代表「該有的都有」），並記 `missing_required:<欄位>`。
  文書類型由標頭「民事判決／民事裁定」判定；兩者皆非 → `unsupported_court_type`。
  **issues**（降級）：`missing_required:*`／`no_footer`／`possibly_truncated_source`／
  `unsupported_court_type`／`no_judge`／`no_clerk`（前兩者即使必填欄位恰好齊全仍是 issue）；
  **notes**（不降級）：`no_reasoning_title`。
  ⚠ `hearing_closed` 在「分別言詞辯論」案件是多值（實測 1/100），型別設計見
  **Open Question 8【待裁決】**；裁決前該類文件維持 `partial`＋`missing_required:hearing_closed`。
  ⚠ 若 Open Q8 採方案 A（恆為陣列），FR-006 的必填規則同步改為
  **`doc_type='judgment'` 時陣列長度 ≥1；`ruling` 允許空陣列**，且 SC-009 需對應補上
  同一個分支（見 SC-009 註）。
- FR-007：**qdrant 層（結構切分）**——chunk 邊界依判決書結構
  （`甲、`／`壹、`／`一、`／`（一）`／`㈠`／`⒈` 等標記，階層依建樹），**不以固定字數為主要邊界**；
  僅超長段才細切，`max_chars=800`（裁決 ①，實測見 §實測基線 §4）。
  只有「整個節點放得進一塊」且 `section_type` 相同的**兄弟節點**才可合併；已被拆開的節點
  不再往上層合併（避免跨章節混塊）。
  每個 chunk 帶 `id`／`parent_id`／`section_path`／`section_type`／`title`／
  `text`（清理版，**不含** `heading_prefix`）／`heading_prefix`（空殼標題，僅有時）／
  `part`（同一 `section_path` 被細切時的 `i/n`）／`embed_text`（含 context header 與
  `heading_prefix`）／`laws[]`／`char_start`／`char_end`。
  `trailing_chars`（簽名區後的附表字數）是**文件層**欄位，不屬於 chunk。
  `unsupported_court_type` 文件的 `section_type` 一律 `unclassified`（見 Edge Cases）。
  ⚠ `char_start`／`char_end` **v2 尚未輸出**，見 FR-016；**在其實作前，FR-007 不可驗收**。
- FR-008：**parent-child**——`chunk.id` 是**流水號**（`case_no#004`，因合併後
  一個 chunk 可能涵蓋多節）；**`parent_id` 是章節 uid**（`case_no#貳、一`），
  指向 payload 的 `parents`。父塊 `embed=false`、子塊 `embed=true`。
  **節點 uid ＝ 父 uid ＋ `、` ＋ 標記；同一父節點下標記重複時加後綴 `[2]`、`[3]`…**，
  否則後者覆蓋前者 → parent 內容錯配。
  parent 取最近的「`一、` 層（含）以上」祖先；其文字超過 `PARENT_MAX=6000` 時不存 parent，
  child 標 `parent_oversize=true`、`parent_id=NULL`（由呼叫端改取相鄰 chunk）。
  **驗收必須斷言 `child.text`（整段）是 `parent.text` 的子字串**（這是 parent-child
  檢索的唯一失敗模式，且它**不會報錯**）。
- FR-009：**qdrant 版本表達**——collection `judgments_struct_v1` ＋ alias
  `judgments_struct`（裁決 ③）；payload 內記 `chunking_version` 與
  `embed_model`。**換 embedding 模型必須新 collection**（向量空間不同）。
- FR-010：**metadata 的 `laws[]`**——只存去重後的法條，作為 **filter 用**，
  明示是 **chunk 層的衍生值**；**不在 judgment 層重複存「法條→chunk」對應**
  （那是兩個真相來源）。回溯走 qdrant payload。跨判決檢索用 PG 陣列 ＋
  **GIN index**，不建 FK、不建 view。
- FR-011：**法條解析的雙門檻**（裁決 ②）：
  ① 人工標註 **20 份**，量 **precision** 與 **recall**（門檻由 plan 定）。
  ② **未解出條號分類報告**：parser 把「抓到條號但解不出法規名」者依前文線索分兩類計數——
  `laws_unresolved_referential`（前文有「同法／上開規定／該法…」＝**指涉型，維持未解出**，
  **不得**為了讓它變 0 而猜測指涉對象，那會是偽造）與 `laws_unresolved_unknown`（無線索）。
  **`unknown` 不等於「清單漏掉」**——實測其中混有契約條款、施行細則、省略前法的準用、
  判決自訂簡稱，故驗收＝**人工檢視頻率最高的 N 個 `unknown` 上下文並分四類**
  （漏法規／縮寫／非法規／省略前法），**只有前兩類**計入「清單問題」，降到 plan 訂定的門檻以下。
  **憲法**（實測有「憲法第11條」）屬**法源**，歸「漏法規」：認得 `憲法第N條`→`憲法§N`、
  `憲法增修條文第N條`→`憲法增修條文§N`，**各用獨立前綴**——兩者是不同規範，
  也避免與法律條文混用同一命名。
  縮寫處理【雙方共識】：以判決內「法規全名（下稱簡稱）」建立**該份文件的簡稱對照**為**主要機制**
  （判決自己的定義，非猜測；實測樣本內 10 種簡稱全為判決自訂，其中 `投保法`、`就保法`、
  `民航法` 不在全域表），全域縮寫表（`勞基法`→勞動基準法 等確定性對照）僅作**後備**
  （判決未定義簡稱時才用）。
- FR-012：**清理只發生在衍生層**（裁決 ③）——硬換行合併、段落空白移除、
  民國日期轉 ISO **只在 `text`／`embed_text` 與 metadata 抽取時發生**；
  parquet 的 `jfull` と chunk の `char_start/char_end` 一律指向**未清理**原文。
  `normalize` 必須是**決定性**函式並記入 `parser_version`（SC-003 ② 依賴它）。
- FR-013：**不重建既有 collection**——既有 seed 索引與 B2/B3/B4 的 eval
  基線不得因本 spec 失效（`pytest` 全綠、既有 eval 數字不變）。
- FR-014：**依賴宣告**——`pyarrow`（**已於 2026-10-10 安裝 26.0.0**）、
  `asyncpg`（已有）、Qdrant 走 `httpx` 直打 API（沿用 `ingest/laws/qdrant_load.py`
  既有做法，**不引入** `qdrant-client`）。
- FR-015：**`pg_schema.sql` 註解同步更新**——裁決 ⑤ 推翻了其「拒絕存 `court`」的舊理由，
  推翻的證據（目錄核心字串 40/40 出現在 `JFULL` line 0；目錄是 authoritative 結構）
  必須寫在該決策**原處**，否則下一位讀者會看到矛盾理由（SC-010 驗收）。
- FR-016：**parser 必須輸出 `char_start`／`char_end`**——清理階段需為每個保留下來的字元
  記錄其在原文 `jfull` 的 offset，chunk 取首尾對應（單位字元，含 CRLF）。
  ⚠ `split_judgment_v2.py`（rev2／rev3）**尚未實作**此項；它阻擋 FR-007、US3、SC-003、SC-008，
  於〈實作切片順序〉列為**獨立切片**（S1-3），不當作 v2 收尾。
  **S1-3 的 exit criteria ＝ SC-003 ② 在 parser 層成立**（詳見〈實作切片順序〉）。

### Key Entities

- **parquet partition**（`year=YYYY/month=MM/*.parquet`）：authoritative 原文層
- **`judgment_meta`**（postgres）：可篩選 metadata，每欄帶 provenance，
  `parse_status` 讓「可重跑」有依據
- **qdrant `judgments_struct_v1`**（＋ alias `judgments_struct`）：
  payload 帶 `(jid, section_path, chunk_id, parent_id, char_start, char_end,
  chunking_version, embed_model)`
- **既有 `judgements` collection**（凍結 seed）：**不動**
- **`eval-manifest.json`**（**進版控**，見〈共同評估分母〉）：所有比率門檻的錨點
- **parse_report**：逐卷解析結果與失敗分類

## Success Criteria

### Measurable Outcomes（技術無關、可量測）

- SC-001：parquet 中任取 N=20 筆，`jfull_sha256` == 從 archive 重算的
  `entry_sha256`（20/20）；且 `crlf_count` 與原文實測一致（20/20）。
- SC-002：court 三欄位**非同義反覆**的驗收（抽 20 筆全對）：
  ① `court_raw` 可回溯到 `entry_path`（目錄原字串）；
  ② `court_in_text` 與 `court_raw` 的**地院名**必須相同——
     實作為**雙向**斷言：`court_in_text.startswith(court)` **且**
     `normalize_district(court_raw) == court`。
     單靠 `startswith` **不足**：它無法排除「兩者都開頭於同一地院名但分屬不同法院」
     的簡式地名碰撞（機率低，但 SC-002 的門檻語意是「非同義反覆」，故雙向）。
     此條即「目錄核心字串 40/40 出現在 `JFULL` line 0」的正式化；
  ③ `jid` 第一欄法院代碼（如 `TPDV`）與 `court` 的對應一致（代碼↔地院表另列於 plan）。
- SC-003：qdrant 每個 point 的 `char_start/char_end` 回 parquet 取**原文切片**，
  同時滿足 ① 切片是 `jfull` 的合法連續子串；② `clean(切片) == payload.text`
  （`clean` 見 US3；**容差於 offset 切片實測後定義**）。抽 50 筆，含至少 3 筆 CRLF 原文；
  單位為字元。
- SC-004：**parent-child 正確性**——抽 50 個 child，斷言**整段** `child.text` ⊂
  `parent.text`（**50/50**）。這條在實作時會抓到重複編號造成的錯配；
  `heading_prefix` 分離後，標題併入不再造成假失敗。
- SC-005：既有基線**不變**——`pytest` 全綠（當前 1775 passed 不回退）；
  既有 `eval_judgements_slice.py` 數字不變。
- SC-006：解析狀態**如實報出**（分母＝輸入卷數），附 `parse_issues` 分組計數；
  **不允許**為了讓失敗率好看而放寬規則。
- SC-007：parquet 重跑**逐位元組相同**。
- SC-008：完整 round-trip——qdrant 命中 → 取 parent → 回 parquet 取原文 →
  顯示，全程**不需解壓 RAR**。
- SC-009：`judgment_meta` 中 `parse_status='ok'` 的列，**其必填欄位無 NULL**
  （分文書類型，FR-006）。此門檻會抓到「`ok` 但欄位空」那類靜默缺陷。
  ⚠ **若 Open Q8 採方案 A（`date[]`）**，此判斷同步改為陣列語意：
  `doc_type='judgment'` → `cardinality(hearing_closed_dates) >= 1`；
  `doc_type='ruling'` → 允許空陣列。**不得**同時保留單值欄位（那是兩個真相來源）。

- SC-010：`pg_schema.sql` 中 `court` 的決策註解**不再含舊理由**，且含推翻證據
  （grep 驗收；FR-015）。
- SC-011：法條——人工標註 20 份的 precision／recall 達 plan 門檻；`unknown` 頻率前 N 名
  已分類，「漏法規＋縮寫」兩類已清零或低於門檻（FR-011）。

- SC-012：**負向測試**——刑事或行政文書（真實樣本，或把樣本標頭改成「刑事／行政判決」構造）
  必須產生 `unsupported_court_type`，且該文件**全部 chunk** 的 `section_type=='unclassified'`
  （防止「靜默套用民事規則」；FR-007／Edge Cases）。

## 實作切片順序與驗收範圍

三層對 offset 的依賴不同，故在**同一個 spec 內**依下列順序實作，各切片可獨立驗收；
「本 spec 完成」＝**所有 SC 通過**。

| 切片 | 內容 | 依賴 offset？ | 可獨立驗收 |
|---|---|---|---|
| S1-1 parquet | FR-001～003、014 | 否 | SC-001、007 |
| S1-2 postgres | FR-004～006、010、015 | 否 | SC-002、009、010、011 |
| S1-3 offset | FR-016（parser 層，只需原文，**不需三層儲存**） | — | **SC-003 ② 的 parser 層版本**；並在此定義切片邊界容差 |
| S1-4 qdrant | FR-007～009、012、013 | **是** | SC-003、004、005、008、012 |

- **FR-007 在 S1-3 完成前不可驗收**（parser 現行不輸出 offset）。
- **S1-3 的 exit criteria ＝ SC-003 ② 在 parser 層成立**，即：對 manifest 中每個 chunk，
  以其 `char_start/char_end` 切出原文切片，`clean(切片) == chunk.text` 成立
  （容差依 S1-3 實測結果定義）。**此判斷不需要 postgres／qdrant**——
  只需要原文（parquet 已有）與 parser，因此可在 S1-1 之後立刻執行。
  ⚠ **不驗收的部分**：SC-003 ①（區間不越界）同樣在 S1-3 可驗，故 ①②皆屬 S1-3 範圍；
  S1-4 只驗「payload 裡的 offset 與 text 與 parser 一致」。
- SC-005（既有基線不變）、SC-006（如實報出）於**每個切片**都須成立。

## 共同評估分母

- 分母＝M1 的 **100 卷**，由 **`specs/007-judgment-three-tier-storage/eval-manifest.json`**
  （schema `m1-eval-manifest/1`）固定：`document_count=100`、
  `doc_ids_sha256=3b51cf6b…`、`entries_sha256=7afade72…`。
  manifest 的 `documents` 為**全量逐卷明細**，**失敗的卷也留在分母內**（篩掉失敗卷會讓失敗率
  自動變好看，正是要防的靜默劣化）。
- **manifest 進版控**：它是所有比率門檻的錨點，若只存在 repo 外，下一個 clone 的維護者
  無法驗證「2/100」怎麼來的。檔案為 100 筆明細（數十 KB，可接受）。
  manifest 須同時記 `parser.version` 與 parser **檔案 sha256**（解決「同標 2.0 但程式已改」
  的無法定版問題）。rev3 起 `PARSER_VERSION='2.1'`
  （2.0→2.1：縮寫表、`doc_type`／必填欄位、`heading_prefix`、`unclassified`）；
  **標 2.0 的結果不得與 2.1 混比**。
- manifest 的 `documents[]` 建議加一個 **`notes_candidate[]`** 欄位，
  記錄「依兩問規則應歸為 notes 而非 issues」的 issue 旗標（如 `no_reasoning_title`）。
  理由：§實測基線 §2 說「改後 `partial` 應為 2 件」是**預測而非實測**——
  parser 尚未實作 notes 分級。把預測寫進 manifest 並標明「預測」，可避免 plan 階段
  把那個數字當成已驗收的結果。
- **所有比率型門檻一律寫成「分子／100」**並註明此分母；任何 96 卷等舊數字須換算（例 2/96→2/100）。
- ⚠ 此分母是**測試 fixture，不是母體估計**（單一月份、4 個地院、僅民事）。`/100` 的門檻是
  **回歸閘門**，不得當成對 108,409 件的失敗率推論。擴到全庫是**新 manifest**
  （加法，不改寫舊的）。

## Assumptions

- M1 的 100 卷 profile 存在於 `data/judgements/profile-m1/raw/`（gitignore）。
  本 spec **消費**它，不重做解壓。
- `pyarrow` 已安裝（26.0.0，已驗 `import pyarrow.parquet` 成功）。
- 本機 postgres 與 qdrant 已啟動且 healthy（2026-10-10 實測：
  `ragdemo-postgres-1`／`ragdemo-qdrant-1` 均 Up (healthy)）。
  ⚠ 連線細節（實測）：pg 的 user 是 **`rag`**（compose 寫死，非 `.env` 猜的名字）；
  pg／qdrant 都**只綁 Tailscale IP `100.119.83.111`**，不是 `127.0.0.1`。
- 結構切分規則來自 `split_judgment_v2.py`（`PARSER_VERSION='2.1'`）。已含：全形空白容忍的
  footer、依文書類型的必填欄位、`unsupported_court_type`＋`unclassified`、`heading_prefix`、
  唯一節點 uid、全域縮寫表、`referential`／`unknown` 計數。**尚未含**：`char_start/char_end`
  （FR-016）、`parse_notes` 分級（FR-005）、判決內「下稱」簡稱解析（FR-011 主要機制）、
  「分別言詞辯論」多日期（Open Q8）。

## 實測基線（2026-10-10，全部實跑，非推論）

### §1 jid 作為分區鍵：100/100 可用

```
欄數分布：6 欄 × 100 卷（100%）
第 5 欄是 8 位合法日期：100/100
示例：TPDV,109,金,24,20260731,4 → ['TPDV','109','金','24','20260731','4']
```
**結論：分區不依賴任何 JFULL 解析**（解決了「規則壞掉就分不了區」的耦合）。

### §2 parser v1 → v2 的失敗率變化（分母 100，見〈共同評估分母〉）

| | v1（原始） | v2 |
|---|---:|---:|
| `ok` | 4 | **96** |
| `partial` | 0 | **4** |
| `failed` | 96 | **0** |
| 合計 | 100 | 100 |

v2 的 4 件 `partial`：`PCDV,113,重訴更一,1`（上游截斷：`no_footer`＋`possibly_truncated_source`＋
缺 `judgment_date`／`judges`／`clerk`）、`TPDV,114,重訴,383`（缺 `hearing_closed`，原因是
**分別言詞辯論**、非 regex 問題，見 Open Q8），以及 2 件 `no_reasoning_title`
（rev2 時列為 partial；依兩問規則**應**改為 `parse_notes`，**改後預期** `partial` 為 2 件
——此為**預測非實測**，parser 尚未實作 notes 分級，見〈共同評估分母〉的 `notes_candidate`）。
`doc_type`：judgment 97／ruling 3。

**v1 的三個根因**（皆由 v2 修正，已實測驗證）：
① footer 正則不容許全形空白（`中　　華　　民　　國　　115 　年…`）→ 約 87 卷
② 理由段標題只認 `事實及理由` → 約 8 卷
③ footer 必須在行首、且假定後接簽名區 → 7 卷末尾是附表

**v2 修正過程中另發現的三個問題**（v1 整份失敗所以當時看不到）：
④ 簽名行前面帶庭別（`民事第五庭　法官　X`）→ v2 初版約 81 卷 `judges` 全空
⑤ `hearing_closed` 的 regex 要求「民國」且不容許空白 → 2 卷判決空值
⑥ 重複編號使 parent 被覆蓋 → 8 個 child 錯配（見 Edge Cases）

⚠ **我先前的診斷有錯、已更正**：我曾說「日期與書記官被硬換行拆成兩行」
是死因之一，實測該情形在 100 卷裡是 **0 卷**。真正原因是**全形空白**。

### §3 v2 的分佈（max_chars=800，100 卷）

| 指標 | 值 |
|---|---|
| chunk 總數 | **3,224**（每卷中位 31、最大 100） |
| chunk 長度 | 中位 **577**、p10 143、p90 780、最大 **2,874** |
| 超過 `max_chars` | **84**（2.6%） |
| 過短（<100） | **222**（6.9%） |
| `parse_issues` | `no_reasoning_title` 2、`no_footer` 1、`possibly_truncated_source` 1、`missing_required` 4 項（涉及 2 卷）|

`section_type` 分布：`court_reasoning` 1,429／`plaintiff_claim` 490／
`other` 483／`defendant_defense` 299／`procedure` 156／`conclusion` 152／
`holding` 115／`issues` 100。

> **rev4 更正（事實錯誤）**：本節**不得**引用 `forced_break_documents`＝37／100
> 作為 v2 的性質——**那 37 是 M1 凍結 `chunk.py` 的量測**，而 **v2 根本不產
> `boundary_kind` 欄位**。實測：v2 的 chunk 欄位為
> `{char_len, embed, embed_text, id, laws, parent_id, section_path, section_type, text, title}`，
> `boundary_kind` 出現次數 **0**；而 `profile-manifest.json` 的
> `boundary_kind_histogram` 欄位值域為 `{forced, exact, natural, tail}`，來自 `chunk.py`。
> 兩者的 `forced` **不是同一個概念**：`chunk.py` 的 forced =「找不到自然邊界而硬切」，
> v2 的硬切 =「被迫以句號切斷」。**S1-4 不得假設結構切分繼承了 37 卷這個性質。**

### §4 `max_chars` 四檔實測（裁決 ① 的依據）

| `max_chars` | 100 卷塊數 | 外推 108,409 件 | 超標 | 過短<100 |
|---:|---:|---:|---:|---:|
| 600 | 4,233 | 459 萬 | 269 | 265 |
| **800** | **3,224** | **350 萬** | **84** | **222** |
| 1000 | 2,687 | 291 萬 | 34 | 209 |
| 1200 | 2,301 | 249 萬 | 17 | 195 |

**超標數幾乎線性下降（269→17）而塊數只降 46%**，故 1000 之後收益遞減明顯。
**採 800**：超標率 2.6% 可接受，600 的 6.4% 已影響檢索粒度。

### §5 法條未解出的成因：**縮寫缺失為主**，但「無線索」不等於「漏法規」

清單裡的 `勞動基準法`、`證券交易法` **都在**，缺的是**縮寫**：

| 縮寫 | 出現次數 | 對應全名 | 全名是否已在 `LAWS` |
|---|---:|---|---|
| `勞基法` | **213** | 勞動基準法 | ✅ 已在 |
| `證交法` | **85** | 證券交易法 | ✅ 已在 |
| `勞退條例` | 31 | 勞工退休金條例 | ✅ 已在 |
| `投顧法` | 28 | **證券投資信託及顧問法** | ❌ 需補全名 |
| `職安法` | 27 | 職業安全衛生法 | ❌ 需補全名 |
| `消保法` | 19 | 消費者保護法 | ✅ 已在 |
| `醫師法` | 5 | 醫師法 | ❌ 需補（rev2 已補） |
| | **408 合計** | | |

⚠ **rev1 更正**：`投顧法` 的全名是《證券投資信託及顧問法》，**不是**《證券投資顧問業管理規則》。
樣本內 2 份判決自己寫了「證券投資信託及顧問法（下稱投顧法）」；「證券投資顧問業管理規則」
在 100 卷樣本中**一次都沒出現**。

另有少量**真的漏法規**：`離島建設條例`（25）、`營利事業所得稅查核準則`（8）、
`預售屋買賣定型化契約應記載及不得記載事項`、`會計師查核簽證財務報表規則`、
`用電設備裝置規則`（rev2 已補入清單）。

**100 卷實測**：補縮寫與全名後，`laws_unresolved_referential` 中位 0（最大 6）；
`laws_unresolved_unknown` 中位 4（最大 88），共 854 種上下文／936 次，最大值集中在少數卷
（故補 7 個縮寫只讓整體中位數降 2）。頻率前 20 的上下文**全部落在下列四類**，佐證 FR-011 的分類：

| 類別 | 例 | 計入清單問題？ |
|---|---|---|
| 省略前法 | `準用第41條、第42條…同法第79`；`第168條至第172條及前條…` | 否 |
| 契約條款（非法規） | `依原證1契約第11條第1項第4款約定` | 否 |
| 施行細則／漏法規／憲法 | `證交法施行細則第6條`；`離島建設條例增訂第18條之1`；`地籍清理條例第20條`；`憲法第11條` | **是** |
| 縮寫 | `職安辦法第12條之1` | **是** |

`unknown` 內混有非法規項目，**不能視為清單缺口**——這是門檻 ② 要求人工分類的原因。

### §6 容量估算（100 卷實測外推，**估計值非驗收數字**）

`JFULL` 原始 5.3 MB／zlib-6 後 1.7 MB（**壓縮比 32.9%**）→
外推 108,409 件約 **5.7 GB 原始／1.9 GB 壓縮**。用來說明分區必要性。

## 裁決記錄（2026-10-10，maintainer）

| # | 議題 | 裁決 | 影響 |
|---|---|---|---|
| ① | 結構切分 vs 凍結 `chunk.py` | **並存**，結構切分另建 collection | FR-007／FR-013 |
| ② | 法條門檻 | **人工標註 20 份量 precision/recall** ＋ `laws_unresolved` 只算清單漏掉的 | FR-011 |
| ③ | qdrant 版本 | `judgments_struct_v1` ＋ alias `judgments_struct`；payload 記 `chunking_version`／`embed_model` | FR-009 |
| ④ | 分區粒度 | **`year/month` 兩層**，檔內依 `court` 排序 ＋ row-group min/max；分區鍵取自 `jid`；取不到落 `year=unknown/` **不猜** | FR-002 |
| ⑤ | `court` 是否存 | **從目錄取，採納**（推翻 `pg_schema.sql` 舊決策） | FR-004 |
| ⑥ | 文字清理 | **只發生在衍生層** | FR-012 |
| （附帶） | `max_chars` | **800** | FR-007 |
| （附帶） | `issue_tags` | **NULL 表示尚未抽取**（空陣列會混淆「沒抽到」與「確定沒有」）；**LLM 抽取排除在本 spec 外** | FR-004 |
| （rev4） | **評估 manifest 納入版控** | `specs/007-…/eval-manifest.json` 進版控（門檻錨點不可只在 repo 外） | 〈共同評估分母〉、Key Entities |
| （rev4） | **S1-3 的 exit criteria** | ＝ SC-003 ② 在 parser 層成立（①②皆屬 S1-3，不需 postgres／qdrant） | FR-016、〈實作切片順序〉 |

## 與既有凍結物的關係（Constitution Check 的張力所在）

| 既有物 | 它的狀態 | 本 spec 的處置 |
|---|---|---|
| `ingest/judgements/chunk.py` | **凍結**（spec 004 T015，B2/B3/B4 依賴） | **零改動**。結構切分是**另一支** parser，另存 collection。⚠ **兩者的邊界語意不同**（見 §實測基線 §3 的 rev4 更正） |
| `ingest/judgements/pg_schema.sql` の `judgment` 表 | 已存在，B2/B3/B4 依賴 | **零改動**。新 metadata 進新表 `judgment_meta` |
| `pg_schema.sql` **拒絕存 `court`** 的決策 | 理由是「推論 court 是 FR-016 禁止的 synthesize」 | **推翻其結論，保留其表**。理由：當時的前提「沒有任何 court↔目錄 の対応存在」**不成立**——實測目錄核心字串 **40/40** 出現在 `JFULL` line 0，而目錄本身是 authoritative 結構。**`judgment_meta.court` 取自目錄（地院級）**；判決書標頭所載（可含庭級）另存 `court_in_text`，兩者不混用 |
| `judgment_meta` 的 FK 與 `jid` | `judgment(jid)` 已是 PK | FK 只掛在 `jid`（一對一，由 DB 保證）；既有表**零改動** |

⚠ **`pg_schema.sql` 需同步更新註解**（推翻決策的證據必須寫在原處，
否則下一位讀者會看到矛盾的理由）——已升為明確交付物 **FR-015／SC-010**。

## 重驗結果（100 卷，分母見〈共同評估分母〉）

1. **`hearing_closed`**：空值 4 件＝3 件家親聲裁定（正常）＋ **1 件判決 `TPDV,114,重訴,383`**。
   rev2 曾寫「3 件判決空值已消失」是**說過頭**（評估端的 96 卷樣本不含 383）；實際是
   **3 件中 2 件已修（regex）、383 仍空**，且 383 的原因不是 regex，而是原文為
   「分別言詞辯論終結」（兩個日期）。處置見 **Open Q8【待裁決】**。
2. **parent 錯配**：749 個有 parent 的 child，**749/749 正確，0 錯配**（修前 20 個：
   8 個重複編號覆蓋＋12 個標題併入）。
3. **`laws_unresolved`**：見 §5；剩餘為指涉型與非法規，**不預期趨近 0**。

## Open Questions（clarify 階段待裁）

1. **`max_chars` 在 plan 階段是否需要 A/B 實測**？裁決 ① 已定 800，但
   §4 顯示 1000 的塊數少 17%、超標少 60%。是否要實際建兩個 collection 比較
   檢索品質？（會使成本翻倍）
2. **SC-003 的定義衝突**【建議・待裁】：payload 的 `text` 是**清理後**的，而
   `char_start/char_end` 指向**未清理**原文，故「切片逐位元組等於 `text`」自相矛盾。
   rev1 建議 payload 同存 `raw_text`；**rev2 改建議：不存 `raw_text`**（那是在向量層
   複製一份 authoritative 原文，違反「parquet 是唯一原文」），改為 payload 只存
   `text`＋offset，SC-003 改成「切片合法 ＋ `normalize(切片)==text`」（已寫入 US3／SC-003）。
   單位統一用**字元**，不用位元組。前提是 FR-016（offset 輸出）要先做出來。
3. **`court` 的語意**【建議・待裁】：存 `court`（目錄正規化、地院級）、`court_raw`（目錄原字串）、
   `court_in_text`（判決書標頭，可達庭級）三者（已寫入 FR-004）。範例：同一個法院
   `臺灣新北地方法院民事`（目錄）vs `臺灣新北地方法院三重簡易庭`（標頭）。
   （rev1 的範例混用了新北與臺北兩個不同法院，已更正。）
   ⚠ **rev4**：`normalize_district()` 的規則（如何從 `臺灣新北地方法院民事` 得到
   `臺灣新北地方法院`、從 `臺灣高等法院刑事` 得到什麼）**尚未定義**，且必須處理
   **地方法院／高等法院／最高法院／分支法院**多層級。它是 SC-002 ② 的實作前提，
   建議 plan 階段連同「`jid` 第一欄代碼 ↔ 法院名對照表」一起定案（SC-002 ③）。
4. **parquet の `jfull` 是否進 vector 檢索的 payload**？若進，payload 會非常大
   （中位 19,000 字）。建議**不進**，只進 `(char_start, char_end)`。
5. **`parse_issues` 是否分級**【雙方共識，待 maintainer 確認；規則見 FR-005】：`no_reasoning_title` 的卷已完整解析，
   標 `partial` 會讓「只重跑 partial」白跑。建議分 `parse_issues`（降級）與
   `parse_notes`（資訊），已寫入 FR-005／006。
6. **是否納入「判決內『下稱』簡稱解析」**【建議・待裁】：以判決自己的定義建立該份文件的
   簡稱對照，取代／補充全域縮寫表（此次 `投顧法` 對照出錯即例）。確定性規則、不猜測；
   建議納入 plan 階段，全域縮寫表留作 fallback。
7. **`char_start/char_end` 的實作切分**【雙方共識，待 maintainer 確認】：留在本 spec，但列為
   獨立切片 S1-3（見〈實作切片順序〉）：parquet／postgres 不依賴它，可先驗收；offset 只需原文，
   可獨立驗收；FR-007 在其完成前不可驗收。**rev4 已補上 S1-3 的 exit criteria**（＝SC-003 ② 的
   parser 層版本）——原本 S1-3 只有內容沒有門檻，是可驗收性缺口。
8. **【待裁決】`hearing_closed` の多值情形**（分別言詞辯論；實測 1/100：`TPDV,114,重訴,383`）。
   原文標頭：「本院於民國115年6月1日（米樂管理顧問有限公司以外之被告）、6月8日（米樂管理顧問
   有限公司）分別言詞辯論終結」——兩個日期、被硬換行切斷、中間夾括號、第二個日期省略年份。
   選項：**A** 存陣列；**B** 存「主日期」＋ raw 原文；**C** 維持單值並永遠 `partial`。
   - C 的問題：重跑不會變好，正是 issues／notes 分級要消除的那類無效重跑。
   - 【Claude 的建議】型別採「**恆為陣列**」`hearing_closed_dates date[]`（單日期案件長度 1，
     所以**只有一種形狀**，不同於 `jid` 欄數不一致的情形）＋ `hearing_closed_raw`（原句，provenance）；
     便利用單值 `hearing_closed` 為由陣列**衍生**的 generated column（PG≥12 方可），
     **不另存**，避免兩個真相來源。單值取**最後一個**（全案辯論皆已終結之日）；
     B 案取「第一個」亦屬確定性規則——**兩者擇一皆可，必須寫明所選規則**，並以
     「各日期 ≤ `judgment_date`、遞增」為不變式驗證。
   - 解析限制：只在「分別」緊鄰「言詞辯論終結」時才啟用多日期；日期須落在「本院於」與「分別」之間；
     後續日期省略年份時沿用前一個；**不得**抓句中其他日期（樣本中 6 卷簡上案件的同一句內另有
     原審判決日期，即為反例）。
   - **UNVERIFIED**：目前只有 1 個樣本；上線前須在全庫掃描「分別…辯論終結」計數並抽驗。
   - 裁決前：該類文件維持 `partial`＋`missing_required:hearing_closed`（FR-006）。
   - ⚠ **rev4**：採 A 時 FR-006 與 SC-009 都要補陣列分支（已寫入兩處）。

## 已排除（明確不做，記錄理由）

- **LLM metadata 抽取**（`issue_tags`、部分勝訴等結果判定）：
  ① 本地模型在判決任務の eval 為 3/14（方向性證據，使用者無法獨立驗證，
  但結論成立）；② 無驗收基準；③ 垂直切片原則（先讓三層存得起來）。
  `issue_tags` 存 `NULL`。
- **materialized view / 法條對照表**：FR-010 已說明——會製造第二個真相來源。
- **把結構切分接上 live 服務**：屬後續 scope（索引 + 檢索 + 生成）。
- **在 `pg_schema.sql` の `judgment` 表加欄位**：會動到 B2/B3/B4 的依賴。
- **指涉型法條の解析狀態機**（「同法第X條」「上開規定第X條」→ 還原法規名）：需維護前文指涉狀態，
  任何誤判都是偽造資料（FR-011）。指涉型維持未解出並計數。
- **以「`unknown` 降到 0」作為法條門檻**：該類別混有非法規（契約條款等），自動歸零只能靠猜（FR-011）。

## 修訂記錄 rev1 → rev2

| # | 位置 | 類型 | 內容 |
|---|---|---|---|
| 1 | §5 | **更正** | `投顧法` 全名誤為《證券投資顧問業管理規則》，應為《證券投資信託及顧問法》（樣本 2 份判決自述） |
| 2 | 待重驗②／Edge Cases／FR-008 | **更正** | 20 個 child 錯配拆成兩個成因：8 重複編號＋12 標題併入；新增 `heading_prefix` 分離與 uid 規則 |
| 3 | Open Q3 | **更正** | 範例混用兩個不同法院；改為同一法院的目錄值與標頭值 |
| 4 | FR-004／SC-002 | 新增 | `court_in_text`；SC-002 只比對目錄值（地院級），避免簡易庭案件誤判失敗 |
| 5 | US3／FR-012／SC-003／Open Q2 | 修訂 | SC-003 改為「切片合法＋`normalize(切片)==text`」；不存 `raw_text`；單位改字元【建議・待裁】 |
| 6 | FR-016 | 新增 | parser 須輸出 offset；v2 尚未實作，阻擋 FR-007／SC-003／SC-008 |
| 7 | FR-015／SC-010 | 新增 | `pg_schema.sql` 註解同步更新升為明確交付物 |
| 8 | FR-011／SC-011／§5 | 修訂 | 門檻 ② 改為人工分類 `unknown`；`referential`／`unknown` 兩計數；補「下稱」簡稱建議 |
| 9 | FR-005／006／Open Q5 | 修訂 | `parse_issues`／`parse_notes` 分級；必填缺失記 `missing_required:*`【建議・待裁】 |
| 10 | FR-007 | 修訂 | 兄弟節點合併規則；chunk 欄位含 `heading_prefix`／`part`；`trailing_chars` 改為文件層 |
| 11 | §2 | 補充 | 新增根因④⑤⑥（簽名行帶庭別、hearing regex、重複編號） |
| 12 | 待重驗／Assumptions | 更新 | rev2 已在 96 卷驗證；100 卷待 maintainer 確認；列出 v2 已含／未含項目 |
| 13 | 已排除 | 追加 | 指涉型狀態機、`unknown` 歸零門檻 |

## 修訂記錄 rev2 → rev3

| # | 位置 | 類型 | 內容 |
|---|---|---|---|
| 1 | 〈重驗結果〉① | **更正** | rev2「3 件判決空值已消失」說過頭：實為 2/3 已修，383 因「分別言詞辯論」仍空 |
| 2 | Open Q8 | 新增【待裁決】 | `hearing_closed` 多值設計；Claude 建議「恆為陣列＋衍生單值＋raw」；UNVERIFIED |
| 3 | SC-002 | 重寫 | 改為三條非同義反覆驗收（`court_raw` 回溯、`court_in_text.startswith(court)`、`jid` 代碼對應） |
| 4 | US3／SC-003 | 修訂 | `normalize`→`clean`（具名、有版本、可單獨呼叫）；容差待 offset 切片實測定義 |
| 5 | 〈實作切片順序〉 | 新增 | parquet→postgres→offset→qdrant；FR-007 在 S1-3 前不可驗收 |
| 6 | FR-005／006 | 修訂 | issues／notes 兩問規則；`possibly_truncated_source` 等即使必填齊全仍是 issue |
| 7 | FR-011 | 修訂 | 憲法歸「漏法規」，獨立前綴 `憲法§N`／`憲法增修條文§N`；「下稱」簡稱解析為主、全域表為後備 |
| 8 | Edge Cases／SC-012 | 修訂＋新增 | `unsupported_court_type` 標 UNVERIFIED；其文件全部 chunk `unclassified`；負向測試；各抽 20 份驗證 |
| 9 | 〈共同評估分母〉 | 新增 | 以 manifest 固定 100 卷；比率寫「分子／100」；fixture 非母體；記 parser 版本＋檔案 sha256 |
| 10 | §2／§3／§5 | 更新 | 改用 100 卷數字（ok 96／partial 4；referential／unknown 分計；四類分類佐證） |
| 11 | Assumptions | 更新 | `PARSER_VERSION` 2.0→2.1（行為已變：`unclassified` 等），2.0 與 2.1 結果不混比 |

## 修訂記錄 rev3 → rev4

| # | 位置 | 類型 | 內容 |
|---|---|---|---|
| 1 | §實測基線 §3 註記 | **更正（事實錯誤）** | `forced_break_documents`＝37／100 是 **M1 凍結 `chunk.py`** 的量測，**不是 v2 的性質**——實測 v2 的 chunk 欄位不含 `boundary_kind`（出現次數 0），而 `profile-manifest.json` 的 `boundary_kind_histogram` 值域 `{forced, exact, natural, tail}` 來自 `chunk.py`。兩者 `forced` **不是同一概念**（硬切原因不同）。已加「S1-4 不得假設結構切分繼承 37 卷這性質」的禁令 |
| 2 | SC-002 ② | 修訂 | 由單向 `startswith` 改為**雙向**：加上 `normalize_district(court_raw) == court`。理由：`startswith` 無法排除「兩者都開頭於同一地院名但分屬不同法院」的簡式地名碰撞，與「非同義反覆」的門檻語意不符 |
| 3 | FR-006／SC-009 | 修訂 | 補 **Open Q8 方案 A 的陣列分支**：`doc_type='judgment'` → 陣列長度 ≥1；`ruling` 允許空陣列。並明令**不得同時保留單值欄位**（兩個真相來源） |
| 4 | 〈實作切片順序〉 | 補充 | 定義 **S1-3 的 exit criteria ＝ SC-003 ② 在 parser 層成立**（①②皆屬 S1-3）；原本只有內容沒有門檻，是可驗收性缺口 |
| 5 | 〈共同評估分母〉／Key Entities | 新增 | **eval manifest 納入版控**（`specs/007-…/eval-manifest.json`）：門檻錨點不可只在 repo 外，否則下個 clone 無法驗證「2/100」的來源 |
| 6 | 〈共同評估分母〉 | 新增 | manifest 加 **`notes_candidate[]`** 欄位，並在 §2 把「改後 partial 應為 2 件」明確標為**預測非實測**（parser 尚未實作 notes 分級） |
| 7 | Open Q3 | 補充 | `normalize_district()` 的規則**尚未定義**，且需處理地院／高院／最高法院／分支法院多層級；它是 SC-002 ② 的實作前提，與 SC-002 ③ 的代碼對照表一併在 plan 定案 |
| 8 | 裁決記錄 | 追加 | 兩項 rev4 流程裁決（manifest 進版控、S1-3 exit criteria） |