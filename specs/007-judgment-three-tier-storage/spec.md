# Feature Specification: S1 判決三層儲存（parquet 原文 ＋ postgres metadata ＋ qdrant 向量）

- Status: Draft **rev5**（**specify 階段收斂完成；可進 clarify／plan**）
  2026-10-10 · rev5 納入 **§實測基線 §7**（檢索上下文對法條抽取的影響實測）
  及其衍生的 **FR-011b／FR-011c**（`laws[]` 分文件層／片段層，各用實測選定的模式）
  與 **SC-011b**；另加 **User Story 5**（只留接口，實作屬 spec 008）；
  **Open Question 8 已裁決**（`hearing_closed` 恆為陣列，方案 A）。
  rev4 的三處修正與兩個流程決定保留，異動見文末兩份修訂記錄。
  ⚠ **僅 Q8 阻擋進 plan 的狀態已解除**；其餘 Open Questions 多為
  【建議・待裁】或【雙方共識】，可在 plan 階段定案（Q3 的 `normalize_district()`
  規則是 SC-002 ② 的實作前提，plan 必須先定）。
- **rev5 的量測推翻了三個先前假設**：「句首殘缺回退前句」根本不存在；「帶鄰居 chunk
  無效、順序無關」是**實作把上下文丟掉**造成的假象（正確實作 +13.5pp）；
  「整份依序掃描」不能既當基準又當受評模式。詳見 §實測基線 §7 的錯誤表。
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

### User Story 5 — 抽取模式可切換、且切換有資料支持（Priority: P3，**本 spec 只留接口**）

同一份判決的「文件層法條」與「片段層法條」是**兩個不同問題**，各有最合適的
抽取模式（§實測基線 §7）。片段層若多給一點上下文（±1 鄰居依序），
召回由 0.7283 升到 0.9494——但**代價是引用必須標明法條來自哪個區塊**。
未來使用者應能在**引用時選擇用哪個模式的結果**，並把「哪個答案比較好」的
判斷收回來作為長期調校的依據。

**本 spec 的範圍只有接口**：`judgment_meta.laws_mode` 與 chunk payload 的
`laws_mode` 兩個欄位（FR-011c），**不含** UI、後端切換端點、feedback 表。
那些屬 **spec 008（可選檢索模式與使用者回饋閉環）**，跨檢索／生成／UI／資料表，
違反憲法 I（小切片）與 X（不擴張 scope）。

⚠ 設計前提已由本 spec 免費提供：`laws_mode` 欄位到位後，切換模式**不必改 schema**，
spec 008 只需新增「產生器」與「回饋表」兩件事。

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
  ⚠ **`normalize_district()` 規則草案已定（§實測基線 §8）**，輸入是 archive 目錄字串
  （**權威事實**），輸出 `court_raw`（＝原字串逐字）／`doc_kind`（目錄尾字）／`court`，
  **全部確定性導出、無猜測**。層級判定關鍵字與順序見 §8。
  ⚠ **`court` 的剝除規則有三個未決難題**（簡易庭無法院全名／`福建` 前綴／
  `--` 雙連字號），plan 必須先裁決才實作，否則 SC-002 ② 無法成立。
  ⚠ **`jid` 第一欄代碼與 `court_dir` 是嚴格 1:1（137/137 實測）**，
  故兩者**互為驗證**：不符即資料損毀，**必須失敗而非猜測**。
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
  ⚠ `hearing_closed` 在「分別言詞辯論」案件是**多值**（實測 1/100），型別已定：
  **`hearing_closed_dates date[]` 恆為陣列** ＋ `hearing_closed_raw`（原句，provenance）。
  **單值 `hearing_closed` 為 PG generated column，取陣列最後一個日期**
  （＝全案所有被告的言詞辯論皆已終結之日），**不另存、不手動維護**——
  手動維護等於兩個真相來源（憲法 VI）。
  因為單日期案件長度為 1，**全庫只有一種形狀**（與 `jid` 欄數不一致的情形不同）。
  必填規則同步改為：**`doc_type='judgment'` 時陣列長度 ≥1；`ruling` 允許空陣列**；
  **缺任一日期即 `partial`** ＋ `missing_required:hearing_closed`。
  ⚠ **解析限制（實測反例支持，不得放寬）**：只在「分別」緊鄰「言詞辯論終結」時才啟用
  多日期；日期須落在「本院於」與「分別」之間；後續日期省略年份時沿用前一個。
  **實測 100 卷中「含辯論終結的句子」有 9 句含 ≥2 個日期，其中只有 1 句含「分別」，
  其餘 8 句是原審判決日期落入同一句附近**——若放寬成「句內兩個日期就算多值」，
  這 8 卷會被誤判。故此限制是**有實測反例支撐的**，不是保守估計。
  ⚠ **UNVERIFIED**：只有 1 個樣本。上線前須在全庫掃描「分別…辯論終結」計數並抽驗
  （SC-012 一併處理）。
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
- FR-011b：**`laws[]` 依用途分兩層，各用**實測選定**的抽取模式**（2026-10-10 實測，
  詳 §實測基線 §7）：
  - `judgment_meta.laws[]`＝**文件層**，抽取模式 `doc_ordered`（整份 `jfull` **依序**掃描，
    法規名游標跨 chunk 延續）。ingest 時原文就在手上，**成本為零**。用於 **pre-filter**——
    漏一條就是漏掉一個案子，recall 優先。
  - qdrant chunk payload 的 `laws[]`＝**片段層**，**預設 `chunk`**
    （**只**用該 chunk 自己的文字，無線索時不外推）。**實測召回 0.7283（M1 chunker）／
    0.7124（v2 chunker）**。用於**引用顯示**——多算就是偽造，precision 優先。
  - **備選模式 `ordered_ab`**（±1 鄰居**依序串接**掃描 ＋ 縮寫表），
    **實測召回 0.9494（M1）／0.8597（v2）**，由 spec 008 的 UI 讓使用者選用。
    ⚠ **硬約束**：`ordered_ab` 命中某條法條時，該法規名可能位於**鄰居區塊**而非被引用
    的 chunk 內，故**必須同時回傳每條法條的來源 chunk 位置**，否則引用顯示會偽造來源
    （憲法 VI／XI）。
  - **`parent_expand` 不納入**（實測 0.7333，低於 `ordered` 0.7938；且 parent 覆蓋率
    僅 23.3%、16 卷完全無 parent）。量測工具保留，待 parent 指派規則改進後重測。
  - ⚠ **兩層回答不同的問題**（文件「引用了哪些法條」vs 片段「這一段依據哪些法條」），
    **不是同一件事的兩個真相來源**——與 `court`／`court_in_text` 的文件層／原文層之分同理。
  - ⚠ **現況缺陷（實測）**：v2 的 `doc['laws']` 是**逐 chunk 抽再取聯集**（＝`chunk`，
    召回 0.7124），**不是** `doc_ordered`。故 FR-011b 要求 parser 額外輸出文件層的
    `laws_doc`（＝`doc_ordered` 的結果），否則 `judgment_meta.laws[]` 會繼承約 20pp 的
    召回損失。
- FR-011c：**`laws_mode` 欄位必填**——`judgment_meta.laws_mode`（＝`'doc_ordered'`）與
  chunk payload 的 `laws_mode`（＝`'chunk'` 或 `'ordered_ab'`）各自記錄
  **實際使用的抽取模式代號**。
  這是 FR-011b 的可稽核性要求（沒有它，下一位維護者無法知道某個值是哪個模式產生的），
  也是未來 **spec 008（可選檢索模式與使用者回饋閉環）**的接口點：
  切換模式時只需改這個欄位對應的產生器，不必改 schema。
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
  ③ `jid` 第一欄法院代碼（如 `TPDV`）與 `court` 的對應一致。
     ⚠ **rev5 已強化**：代碼 ↔ 目錄是**嚴格 1:1**（137/137 實測，§8），
     故 ③ 的機檢形式是**雙向斷言**（代碼 → 目錄、且目錄 → 代碼），
     **不符即失敗而非記 warning**（那是資料損毀，不是規則容差）。
     ⚠ **取樣必須跨層級**：全庫有地院／簡易庭／高等／行政／最高／少年家事／智財
     **七個層級**（§8 層級分布）。**抽 20 筆若全落在地院民事，就等於沒驗到其餘
     六層**——驗收報告必須列出**每一層的抽樣數**，某層 0 筆就標該層 UNVERIFIED。
     ⚠ **簡易庭層（13,235 檔／12.2%）與 `court_in_text` 的雙向匹配目前 UNVERIFIED**：
     100 卷樣本**完全不含簡易庭**，而簡易庭目錄沒有法院全名（§8 難題 1）。
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
  ⚠ **`hearing_closed` 採陣列語意**：`doc_type='judgment'` →
  `cardinality(hearing_closed_dates) >= 1`；`doc_type='ruling'` → 允許空陣列。
  ⚠ **generated column 必須真的由陣列衍生**：驗收時**改寫 `hearing_closed_dates`
  陣列內容後讀 `hearing_closed`，必須跟著變**；若它是手動維護的欄位（兩個真相來源），
  此驗收即失敗。另外斷言 `hearing_closed = hearing_closed_dates[last]`
  （取最後一個日期，FR-006 已載明理由）。

- SC-010：`pg_schema.sql` 中 `court` 的決策註解**不再含舊理由**，且含推翻證據
  （grep 驗收；FR-015）。
- SC-011：法條——人工標註 20 份的 precision／recall 達 plan 門檻；`unknown` 頻率前 N 名
  已分類，「漏法規＋縮寫」兩類已清零或低於門檻（FR-011）。
- SC-011b：**片段層法條抽取的召回符合 §實測基線 §7 的實測值**——在同一個
  `chunker` 下，`laws_mode='chunk'` 召回 **≥ 0.70**（實測 0.7283／0.7124）；
  `laws_mode='ordered_ab'` 召回 **≥ 0.85**（實測 0.9494／0.8597），
  且**每條法條都帶有可回溯的來源 chunk 位置**（FR-011b 的硬約束）。
  ⚠ 門檻**必須與量測同基準重跑**（同一 manifest、同一 `chunker`、同一規則基準），
  **跨 `chunker` 版本不直接比較**（實測：同一模式召回 0.7283 vs 0.7124）。
  ⚠ **這些門檻衡量的是「相對規則基準的損失」，不是絕對正確率**——在 FR-011 ① 的
  20 份人工標註完成前，**不得**把它們當作品質證明（§實測基線 §7〈量測本身的限制〉）。
  ⚠ 基準本身會因縮寫而誤歸屬（實測例證見 §7），故 **precision 不設門檻**，
  待人工標註後再定。

- SC-012：**負向測試**——刑事或行政文書（真實樣本，或把樣本標頭改成「刑事／行政判決」構造）
  必須產生 `unsupported_court_type`，且該文件**全部 chunk** 的 `section_type=='unclassified'`
  （防止「靜默套用民事規則」；FR-007／Edge Cases）。
  ⚠ **rev6：已不再是「構造樣本」，200 卷有 103 卷真實觸發**
  （`unsupported_court_type` 103/200，其中**刑事 68 卷**）。故 SC-012 的驗收對象
  改為**這 103 卷的真實子樣本**，並新增三個 100 卷看不到的失敗模式（見
  §實測基線 §9）。

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

### ⚠ **rev6：分母由 100 卷改為 200 卷**（2026-10-10 使用者裁決「全面改用 200 卷」）

**新分母＝200 卷**，錨點 **`specs/007-judgment-three-tier-storage/eval-manifest-200.json`**，
由 **`allowlist-200.json`**（分層抽樣，`agent/scripts/sample_stratified_200.py` 產生）固定。

**為什麼換**：M1 的 100 卷**全部落在 4 個地院民事目錄**（583/108,409 ＝ 0.54%），
導致三處**實測**的 UNVERIFIED：

| 缺口 | 母體 | 100 卷樣本 | 200 卷樣本 |
|---|---:|---:|---:|
| 簡易庭 | 13,235（12.2%） | **0** | **75** |
| 刑事 | 38,932（35.9%） | **0** | **82** |
| 行政 | 3,836（3.5%） | **0** | **12** |
| `jid` 5-field（憲法法庭） | 139 | **0** | **1** |
| 法院目錄 | 137 | 4 | **137（全部）** |

即：SC-002 ② 的簡易庭分支（§8 難題 1）、SC-012 的 `unsupported_court_type`、
FR-028 的 5-field `jid`，在 100 卷上**都無樣本可驗**。

**200 卷實測結果**（`~/.local/bin/unrar` 實際解壓，2026-10-10）：

```
extracted 200/200｜size_crc_ok 200/200｜schema_valid 200/200｜chunked 200/200
失敗 0｜chunk 9,016｜72 卷有 FORCED 斷點
七層皆有產出：地院 82卷/5,341chunk、高院 18/1,864、簡易庭 75/1,065、
             行政 10/347、最高 10/191、智財 3/157、少年家事 2/51
boundary_kind：natural 7,345 / forced 1,212 / exact 259 / tail 200
```

⚠ **200 卷不是全庫的無偏樣本**：它按**法院目錄**分層（每目錄 ≥1，剩餘按容量比例），
所以**每個目錄都被過度代表**、大目錄（如臺灣臺北地方法院民事 7,171 檔）被相對
underrepresented。它是**型別覆蓋的測試 fixture**，仍**不是母體估計**。
⚠ **兩套分母並存，數字不可混比**：§實測基線 §1–§7 的所有數字都是在 **100 卷**上量的，
那些章節的分母標題維持 `（分母 100）` 不變，**不得**與 200 卷的數字並列比較。
**SC-011b 的召回門檻（0.70／0.85）必須在 200 卷上重測**才有意義（簡易庭 75 卷的
chunk 長度與 FORCED 率都與地院民事不同）。

### 分母規則（兩套並用）

- 分母固定於 manifest：**200 卷**＝`eval-manifest-200.json`（現行）；
  **100 卷**＝`eval-manifest.json`（M1 歷史錨點，**保留不刪**）。
  manifest 的 `documents` 為**全量逐卷明細**，**失敗的卷也留在分母內**
  （篩掉失敗卷會讓失敗率自動變好看，正是要防的靜默劣化）。
- **manifest 進版控**：它是所有比率門檻的錨點，若只存在 repo 外，下一個 clone 的維護者
  無法驗證「2/200」怎麼來的。
  manifest 須同時記 `parser.version` 與 parser **檔案 sha256**（解決「同標 2.0 但程式已改」
  的無法定版問題）。rev3 起 `PARSER_VERSION='2.1'`
  （2.0→2.1：縮寫表、`doc_type`／必填欄位、`heading_prefix`、`unclassified`）；
  **標 2.0 的結果不得與 2.1 混比**。
- manifest 的 `documents[]` 建議加一個 **`notes_candidate[]`** 欄位，
  記錄「依兩問規則應歸為 notes 而非 issues」的 issue 旗標（如 `no_reasoning_title`）。
  理由：§實測基線 §2 說「改後 `partial` 應為 2 件」是**預測而非實測**——
  parser 尚未實作 notes 分級。把預測寫進 manifest 並標明「預測」，可避免 plan 階段
  把那個數字當成已驗收的結果。
- **所有比率型門檻一律寫成「分子／分母」並註明是哪一套**（`/200` 或 `/100`）；
  任何 96 卷等舊數字須換算（例 2/96→2/100）。
- ⚠ 兩套分母**都是測試 fixture，不是母體估計**。`/200` 的門檻是**回歸閘門**，
  不得當成對 108,409 件的失敗率推論。擴到全庫是**新 manifest**（加法，不改寫舊的）。

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

### §7 檢索上下文對法條抽取的影響（2026-10-10 實測，決定 FR-011b）

**動機**：S2 候選點 `PCDV,110,重訴,571` 的切點把「依民事訴訟法**、第78條…**」切成兩半。
但**規則抽取在跨 chunk 時到底損失多少**必須實測，否則只是「看起來有問題」，不能當 spec 依據。

#### ⚠ 本節的量測程序出過三個錯，錯誤與更正一併記錄（比數字更重要）

第一次量測的結論有兩條是錯的，錯在**量測程式本身**，不是被量的對象：

| # | 錯誤 | 後果 | 更正 |
|---|---|---|---|
| 1 | 把「整份依序掃描」本身也當成一個受評模式，用「掃描到每個 chunk 末端的**前綴**再取聯集」實作 | chunk 覆蓋全文時，前綴聯集**恆等於整份掃描＝基準**，必然得到召回 1.0000／precision 1.0000——**同義反覆，什麼都沒量到** | 整份掃描**只當基準**，不列入受評模式 |
| 2 | `prev_fallback` 的 `fallback` 分支是 no-op，與 `chunk` 的唯一差別其實是**縮寫表有無**，卻把它標成「回退前句」 | 量到的 +7pp 被誤記為「回退的功勞」 | 模式名稱改正，縮寫表列為獨立變因 |
| 3 | 「句首殘缺時回退前句的 `cur`」 | 單一 chunk 內 `cur` 已跨句延續，「前一句的 `cur`」就是當前的 `cur`，**沒有東西可回退** | 此修法方向**直接廢棄**，不是實測無效 |
| 4 | `neighbor`（±1 鄰居）把每段**各自獨立掃描**再取聯集 | 鄰居帶來的 `cur` 套不到自己那篇，於是得出「順序無關＝上下文無效」的**錯誤結論** | 改為**依序串接後共用一個游標**掃描，才是真的帶上下文 |

第 4 項讓結論**整個反轉**：`ordered` 的召回遠高於 `chunk`，
「順序無關」其實是「上下文被實作丟掉了」。

#### 基準與它的侷限

基準＝**整份原文依序掃描 ＋ 縮寫表**。⚠ **基準不是「正確答案」**：它自己會因縮寫而
錯誤歸屬。實例（`PCDV,111,勞訴,165`）：

    …違反勞動基準法第63條、職安法第26條第1項等保護他人法律）、第185條第1項…

- 基準（認縮寫）→ `cur=職業安全衛生法` → 產出 `職業安全衛生法§185`
- 不認縮寫 → `cur=勞動基準法` → 產出 `勞動基準法§185` ✅ **後者才是判決原意**

故下表的 FP 欄必須讀作**「與基準不一致」**，而非「錯」。哪一邊對，要靠 FR-011 ① 的
**人工標註 20 份**判定——這正是門檻 ① 存在的理由。

#### 實測結果（99 卷有法條／基準 2017 條）

chunker ＝ M1 凍結 `chunk.py`：

| 模式 | 召回 | 漏抓 FN | 與基準不一致 | precision |
|---|---:|---:|---:|---:|
| `chunk`（單 chunk，不認縮寫）＝ v2 現況 | 0.7283 | 548 | 57 | 0.9626 |
| `chunk` ＋ 縮寫表 | 0.7992 | 405 | 0 | 1.0000 |
| `ordered`（±1 鄰居**依序串接**） | 0.8632 | 276 | 128 | 0.9315 |
| **`ordered` ＋ 縮寫表** | **0.9494** | **102** | 0 | 1.0000 |

chunker ＝ v2 結構切分（max_chars=800，同一基準，**與上表不可直接比較**）：

| 模式 | 召回 | 漏抓 FN | 與基準不一致 | precision |
|---|---:|---:|---:|---:|
| `chunk`＝ v2 現況 | 0.7124 | 580 | 268 | 0.8428 |
| `chunk` ＋ 縮寫表 | 0.7819 | 440 | 229 | 0.8732 |
| `ordered`（±1 依序） | 0.7938 | 416 | 369 | 0.8127 |
| `ordered` ＋ 縮寫表 | **0.8597** | 283 | 257 | 0.8709 |
| `parent`（父塊＋chunk 依序） | 0.7333 | 538 | 325 | 0.8198 |
| `parent` ＋ 縮寫表 | 0.8012 | 401 | 246 | 0.8679 |

#### 四個結論

1. **排序與縮寫表是兩個各自有效、可疊加的因素**。`chunk` → `ordered` 是 +13.5pp（M1）
   ／ +8.1pp（v2）；`chunk` → `chunk_ab` 是 +7.1pp／+7.0pp；兩者合用達 0.9494／0.8597。
2. **`ordered`（鄰居）優於 `parent`（父塊）**：0.7938 vs 0.7333（v2）。
   且 parent 覆蓋率僅 **743/3192 chunk（23.3%）**、**16 卷完全無 parent**——
   76.7% 的 chunk 自己就是父塊。所以 **`parent_expand` 不作為獨立模式**（FR-011b）。
3. **可追溯性是 `ordered` 的硬約束**（憲法 VI／XI）：擴窗後某條法規名可能在
   **鄰居區塊**，不在被引用的 chunk 裡。若顯示端不標明來源區塊，就是**偽造來源**。
   故 `laws_mode='ordered_ab'` 時**必須同時回傳每條法條的來源 chunk**。
4. **現況缺陷**：v2 的 `doc['laws']` 是**逐 chunk 抽再取聯集**（＝`chunk`，召回 0.7124
   ／0.7283），不是整份依序。若 S1 直接沿用，`judgment_meta.laws[]` 會繼承約 20pp 的
   召回損失——而它正是 FR-010／SC-011 的對象。故 FR-011b 要求 parser 額外輸出
   **文件層**的 `laws_doc`（整份 `jfull` 依序掃描）。

#### 對 spec 的實際影響

| 欄位 | 模式 | 召回 | 用途 |
|---|---|---:|---|
| `judgment_meta.laws[]` | **整份 `jfull` 依序**（ingest 時成本為零） | 基準 | **pre-filter**——漏一條就是漏掉一個案子 |
| chunk payload `laws[]` | **`chunk`**（預設）／`ordered_ab`（備選） | 0.7283 ／ 0.9494（M1） | **引用顯示**——預設 precision 優先；備選需附來源 chunk |

⚠ 文件層的「召回＝基準」**不是 1.0 的保證**：基準本身有上述縮寫誤歸屬問題，
其絕對正確性由 FR-011 ① 的 20 份人工標註決定（SC-011）。本節量的是**相對損失**。

#### 量測本身的限制（誠實記錄）

- **重現方式**：`agent/scripts/law_extraction_benchmark.py`（單一工具涵蓋全部模式，
  `--parser` 加測 v2 的 `parent` 系列）；錨點輸出
  **`specs/007-…/law-extraction-benchmark.json`**（與 eval-manifest 同一理由：
  **門檻錨點必須在版控內**，否則下個 clone 無法驗證 0.7283／0.9494 的來源），
  含逐卷明細與 `chunker` 欄位。**門檻重跑必須用同一組參數**。
- 樣本是 M1 的 **99 卷民事判決**，非全庫；`SC-012` 已標明刑事／行政分支 UNVERIFIED。
- 召回的**分母是規則基準**，不是真值。**沒有 20 份人工標註之前，這些數字只能
  用來比較模式之間的相對好壞，不能當絕對品質門檻**（SC-011b 因此要求同基準重跑）。
- **跨 chunker 兩表不可直接比較**（同一 `chunk` 模式召回 0.7283 vs 0.7124，基準相同）。
  任何量測報告都必須記錄 `chunker`——這是 FR-009（記 `chunking_version`）的延伸理由。

### §9 200 卷分層樣本上的 parser 實測（2026-10-10，**改分母後必須重測的第一項**）

錨點：`eval-manifest-200.json`（`doc_ids_sha256=d4416bda…`，200 筆逐卷明細，進版控）。
parser＝`split_judgment_v2.py`（**實際回報 `PARSER_VERSION='2.0'`**，
與 spec rev3 記載的 2.1 不一致——如實記錄，不寫期望值）。

#### 結果：`ok` 只有 71/200（35.5%）

```
parse_status  ok 71｜partial 110｜failed 19
doc_type      judgment 175｜ruling 6｜None 19（即 failed）
七層分佈：
  地方法院  ok 37 / partial 44 / failed  1   （82 卷）
  簡易庭    ok 24 / partial 36 / failed 15   （75 卷）
  高等法院  ok  7 / partial  9 / failed  2   （18 卷）
  最高      ok  0 / partial 10 / failed  0   （10 卷）
  行政法院  ok  0 / partial  9 / failed  1   （10 卷）
  智財法院  ok  1 / partial  2 / failed  0   （ 3 卷）
  少年家事  ok  2 / partial  0 / failed  0   （ 2 卷）
```

#### 發現 1：`unsupported_court_type` **103/200（51.5%）**，其中**刑事 68 卷**

100 卷全是民事，所以這條分支**從未被觸發**——spec 原先標「UNVERIFIED，
上線前須各抽 20 份高院／刑事／行政驗證」。**現在有真實資料，結論是它會命中
一半的樣本**。

⚠ 這**不是缺陷，是正確行為**（刑事／行政不能套民事規則，憲法 VI）。
但它意味著 **`judgment_meta.laws[]`／`court`／法條體系在刑事與行政上全部無效**，
而母體的 35.9% 是刑事。**S1 的 pre-filter 故事只對民事成立**——
這必須寫進 US2，否則介面會對用戶做出它做不到的承諾。

#### 發現 2：`failed` 的 19 卷是**三個獨立模式**，100 卷全部看不到

| 模式 | 卷數 | 實測證據 |
|---|---:|---|
| **`裁定如下` 不被當 body 開頭** | 7 | 少年秩事件（`旗秩`/`嘉秩`/`投秩`/`板秩`/`店秩`/`營秩`/`岡秩`）。正文有「本院裁定如下：」但無「判決如下」 |
| **有「判決如下」仍 failed** | 10 | 刑事簡易判決（`豐簡`/`沙金簡`/`士簡`）與高院上訴卷；`no_body_start` 規則抓不到位置 |
| **兩者都沒有** | 1 | `臺中高等行政法院宣示判決筆錄`（`交`,20,176 字元）——**行政法院用完全不同的體例**，連「判決如下」都不存在 |

第三類最嚴重：它證明**「行政」不是民事的變體，而是另一種文件**。
SC-012 原本只要求驗證「不會靜默套用民事規則」，但沒想到行政法院**連 body
開頭標記都不同**——那意味著行政分支需要**獨立的結構規則**，不是共用一套。

#### 發現 3：兩份 84,171／84,174 字元的相鄰高院卷

`金上訴` 兩卷長度只差 3 字元。**高度疑似同一案件的兩份副本**。
若確認，S1 的 `jid` 主鍵會有**內容重複但識別碼不同**的情形——那是
dedup 問題，不在本 spec 範圍，但**必須記錄**否則日後會被當成資料品質問題追查。

#### 這三項對 S1 的影響（尚未裁決）

1. **US2 的 pre-filter 承諾要限縮到民事**，或明確標示刑事／行政為
   `unsupported_court_type`（不提供 filter）。
2. **行政法院需要獨立的結構規則**，不能沿用民事的 `判決如下` 假設。
   這會影響 FR-007 的適用範圍。
3. `failed` 的 19 卷中 18 卷是**真實可解析的文件**被誤判，
   `failed` 率 9.5% 應視為**規則缺口**而非資料品質——SC-006 要求如實報出，
   所以這些必須留在分母內。


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
| （rev5） | **`laws[]` 分兩層，各用實測選定的抽取模式** | 文件層 `doc_ordered`（整份依序，ingest 成本為零，filter 用）／片段層預設 `chunk`（召回 0.7283，引用用）、備選 `ordered_ab`（召回 0.9494）。**v2 現況逐 chunk 抽再聯集＝`chunk`，會繼承約 20pp 召回損失** | FR-011b、§實測基線 §7 |
| （rev5） | **`laws_mode` 欄位必填** | 記錄實際使用的抽取模式代號；這是未來 spec 008（可選檢索模式＋使用者回饋閉環）的接口點，切換模式不必改 schema | FR-011c |
| （rev5） | **`parent_expand` 模式不納入** | 正確實作（父塊＋chunk **依序串接共用游標**）召回 0.7333，**低於**鄰居展開 `ordered` 0.7938；且 parent 覆蓋率僅 23.3%（743/3192 chunk）、16 卷完全無 parent。**保留量測工具**，待 parent 指派改進後重測 | §實測基線 §7 |
| （rev5） | **`ordered_ab` 必須附來源 chunk** | 擴窗命中時法規名可能在**鄰居區塊**；不標明來源就是偽造來源（憲法 VI／XI）。這是硬約束，不可當成選配 | FR-011b |
| （rev5） | **量測必須記錄 chunker 版本，且不得跨基準比較** | 同一模式在兩個 chunker 下召回差 1.6pp（0.7283 vs 0.7124）。**不記錄產生條件的數字不可重現** | FR-011c、§實測基線 §7 |
| （rev5） | **片段層暫不設 precision 門檻** | 基準本身會因縮寫而誤歸屬（§7 實測例證），故在 FR-011 ① 的 20 份人工標註完成前，precision 無可信基準；只設召回門檻且要求同基準重跑 | SC-011b |

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
   「分別言詞辯論終結」（兩個日期）。**已處置**：Q8 採方案 A（`hearing_closed_dates`
   date[] 恆為陣列，單值取最後一個日期為 generated column）——見 FR-006。
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
3. ~~**`court` 的語意**~~【已解算，2026-10-10】：存 `court`（目錄正規化、地院級）、
   `court_raw`（目錄原字串）、`court_in_text`（判決書標頭，可達庭級）三者
   （已寫入 FR-004）。範例：同一個法院 `臺灣新北地方法院民事`（目錄）
   vs `臺灣新北地方法院三重簡易庭`（標頭）。
   （rev1 的範例混用了新北與臺北兩個不同法院，已更正。）
   ⚠ **rev5：`normalize_district()` 規則草案已產出**（§實測基線 §8），
   依據是 **archive 目錄的 137 個法院目錄／108,409 檔**——**不是 100 卷樣本**
   （100 卷只佔 0.54%，用它定規則會得到只認地院民事的骨架）。
   **三個結論已實測**：①代碼 ↔ 目錄**嚴格 1:1**（互為驗證，不符即失敗而非猜測）；
   ②`jid` 有 5／6 兩種欄數，5-field 全屬憲法法庭（該層級無民事/刑事之分）；
   ③代碼末字 → 業務幾乎完整但有 `TPHA` 一個破口，且首 3 字有 56/78 種多對一，
   故**規則不得依賴代碼**。
   **仍有三個難題待裁決**（簡易庭無法院全名／`福建` 前綴／`--` 雙連字號），
   見 §8〈三個尚未解決的難題〉。
   ⚠ SC-002 ② 的雙向匹配門檻 **UNVERIFIED**：100 卷樣本**不含簡易庭與高等法院**，
   那三類必須另取樣，不能用 100 卷的數字宣稱通過。
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
8. ✅ **已裁決（2026-10-10，方案 A）**：`hearing_closed` 恆為陣列
   `hearing_closed_dates date[]` ＋ `hearing_closed_raw`；單值 `hearing_closed` 為
   **generated column，取最後一個日期**，不手動維護。**詳寫入 FR-006 與 SC-009。**
   裁決依據（新增實測，分母 100 卷）：含「辯論終結」的句子中，單日期 52 句、
   **多日期 9 句**、零日期 45 句；但 9 句多日期裡**只有 1 句含「分別」**，
   其餘 8 句是**原審判決日期落在同一句附近**（反例）。故真實多值 **1/100**，
   且「只在分別緊鄰辯論終結時啟用多日期」這條限制有實測反例支撐。
   仍待 plan／上線前處理：**UNVERIFIED**（只有 1 個樣本，須在全庫掃描「分別…辯論終結」
   計數並抽驗）。

### §8 `normalize_district()` 規則草案 ＋ `jid` 代碼對照表（2026-10-10 實測，解決 Q3）

**樣本來源**：archive **目錄 entry**（不解壓內容）—— **108,409 檔、137 個法院目錄**。
⚠ **這不是 100 卷樣本**：M1 的 100 卷只含 4 個地院民事目錄（583 檔，佔全庫 0.54%），
用它定規則會得到一個只認地院民事的骨架。規則草案必須覆蓋**七個層級**，
否則上線時另外 99.46% 會掉進未分類。
錨點：`specs/007-…/court-code-map.json`（137 列逐目錄明細）＋
`agent/scripts/court_code_map.py`（可重跑）。

#### 層級分布（實測，137 目錄全部有分類，`未分類` = 0）

| 層級 | 目錄數 | 檔案數 | 佔比 |
|---|---:|---:|---:|
| 地方法院 | 44 | 83,387 | 76.9% |
| 簡易庭 | 62 | 13,235 | 12.2% |
| 高等法院 | 13 | 5,985 | 5.5% |
| 行政法院 | 6 | 3,625 | 3.3% |
| 最高（含憲法法庭/懲戒/司法院） | 8 | 1,583 | 1.5% |
| 少年家事 | 1 | 516 | 0.5% |
| 智財法院 | 3 | 78 | 0.1% |
| **合計** | **137** | **108,409** | 100% |

#### 結論 1：`jid` 第一欄代碼 ↔ 法院目錄是**嚴格 1:1**

```
代碼種類 137｜法院目錄 137｜一碼多目錄 0
```

故兩者**互為驗證**：任一方單獨存在不算證據，**不一致即資料損毀，必須失敗而非猜測**
（憲法 VI／XI）。這比 SC-002 ③ 原訂的「對應一致」更強——它是可機檢的等價條件。

#### 結論 2：`jid` 有兩種形狀，且 5-field 只對應一個層級

```
欄數分布：6-field 108,270｜5-field 139
5-field 全部落在 憲法法庭憲法（代碼 JCCC）
```

**該層級沒有民事/刑事之分**。所以 `normalize_district()` 必須能處理
「業務不適用」的層級，否則 139 檔（0.13%）全部掉進未分類。

#### 結論 3：代碼末字 → 業務**幾乎完整對應，但有 1 個破口**

| 末字 | 業務 | 目錄數 |
|---|---|---:|
| `V` | 民事 | 67 |
| `M` | 刑事 | 57 |
| `A` | 行政 | 8 |
| `P` | 懲戒 | 2 |
| `C` | 憲法 | 1 |
| `U` | 家事 | 1 |
| `A` | **訴願** | **1（`TPHA`／`臺灣高等法院--訴願決定`，7 檔）** |

⚠ **但規則不得依賴代碼**，理由有兩條，兩條都來自實測：

1. **首 3 字不能反推業務**：78 種前綴中 **56 種對應多個目錄**。
   例：`TPS` → 最高法院刑事/民事/家事；`TPH` → 臺灣高等法院刑事/民事/訴願決定；
   `IPC` → 智慧財產及商業法院刑事/民事/行政。
2. **末字有破口**：`TPHA` 末字 `A` 卻是訴願決定。129 檔對 7 檔——
   **不足以否定規律，但足以決定「不依賴代碼」**。

#### 規則草案（`court_dir` → `court` / `court_raw` / `doc_kind`）

**輸入**是 `court_dir`（archive 目錄第二層字串，**權威事實**）。
**輸出**三個欄位，**全部可從輸入確定性導出，無任何猜測**：

| 輸出 | 規則 |
|---|---|
| `court_raw` | ＝ `court_dir` **原字串，逐字不加工** |
| `doc_kind` | 目錄**尾字**（`民事`/`刑事`/`行政`/`憲法`/`家事`/`訴願`/`懲戒`/`補償`）；取不到 → `NULL`（**不猜**） |
| `court` | 見下方四條剝除規則 |

**剝除順序有意義**（層級判斷先於地點剝除）：

| # | 規則 | 說明 |
|---|---|---|
| ① | 判**層級**（依 §8 層級分布的七類，判定關鍵字與順序見下） | 層級決定後續能剝什麼 |
| ② | 剝**業務尾字**：`doc_kind` 已取出，故 `court_dir` 去掉尾字 | 剩下的是法院名 |
| ③ | 剝**地點前綴**：`臺灣`／`福建` | ⚠ **見下方警告，這條有風險** |
| ④ | 簡易庭層級**不做**地點剝除（簡易庭目錄沒有法院全名，剝了只會得到無意義的單字） | 見下方「簡易庭的難題」 |

**層級判定關鍵字與順序**（順序有意義：`臺北高等行政法院 地方庭行政`
同時含「高等」與「行政」，必須先被行政法院吃掉）：

```
最高      ← 目錄以 最高／憲法法庭／懲戒法院／司法院 開頭
行政法院  ← 含「行政法院」
高等法院  ← 含「高等法院」
地方法院  ← 含「地方法院」
少年家事  ← 含「少年及家事」
智財法院  ← 含「智慧財產及商業法院」
簡易庭    ← 含「簡易庭」（fallback）
```

#### ⚠ 三個尚未解決的難題（**草案到此為止，不猜**）

**難題 1：`簡易庭` 沒有法院全名（62 目錄、13,235 檔、12.2%）**

`臺北簡易庭民事` 剝掉業務後是 `臺北簡易庭`——**這不是可辨識的法院全名**
（正式名稱是「臺灣臺北地方法院」）。而 `normalize_district(court_raw) == court`
（SC-002 ②）要求**雙向**匹配，故 `court` 必須是能與 `JFULL` line 0 對上的字串。
**M1 的 100 卷全在 `臺灣臺北地方法院民事` 等 4 個目錄，一個簡易庭樣本都沒有**——
這條分支在現有樣本上**無法驗證**。
可選處置：(a) `court` 存 `臺北簡易庭` 原字串，接受它不對應正式法院名；
(b) 補建「簡易庭 → 所屬地院」對照（**需外部資料，repo 內沒有，屬外部依賴**）；
(c) 落 `unknown` 並記 issue。**三者都需裁決**，草案不預選。

**難題 2：`臺灣`／`福建` 前綴的剝除有風險**

`福建高等法院金門分院`（13 檔）與 `福建連江地方法院`（25 檔）屬**福建省**法院，
不是臺灣。剝掉 `福建` 會讓它變成 `高等法院金門分院`／`連江地方法院`——
與 `臺灣高等法院臺中分院` 混淆。**保留前綴**較安全（`福建` 不與 `臺灣` 撞），
但那樣 `court` 就不是「單一法院」的乾淨名稱。**需要裁決**。

**難題 3：`--` 雙連字號（`臺灣高等法院--訴願決定`，7 檔）**

這是上游目錄的字面內容，`court_raw` 必須**逐字保留**（憲法 XI）。
但 `court` 該不該保留 `--`？保留則 `court` 不是合法法院名，去掉則是**改寫來源字串**。
**需要裁決**。

#### 這份草案的效力邊界

- ✅ **已實測**：層級分布、1:1 對應、欄數分布、代碼末字對應表、剝除順序的關鍵字。
- ❌ **UNVERIFIED**：難題 1/2/3 的處置；`court` 與 `JFULL` line 0 的**雙向匹配率**
  （SC-002 ② 的真門檻）只能在**簡易庭與高等法院抽樣**後量。目前 100 卷樣本
  **只覆蓋地院民事**，故那三類的門檻**必須另取樣**，不能用 100 卷的數字宣稱通過。


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
| 2 | Open Q8 | 新增【待裁決】→ **已裁決** | `hearing_closed` 多值設計；Claude 建議「恆為陣列＋衍生單值＋raw」；UNVERIFIED。**2026-10-10 採方案 A**，詳見 FR-006 |
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

## 修訂記錄 rev4 → rev5

依據：使用者要求「實測各種作法決定預設，留一個備選，未來可在 UI 切換並納入回饋閉環」。
先**量測**（100 卷全量，非只看 9 個候選點），再把結論寫進 spec。

⚠ **本節記錄兩次自我推翻**：第一輪量測的四個結論中有三個是錯的，錯在**量測程式**
本身。第二輪修正後結論反轉。細節見 §實測基線 §7 的錯誤表。

| # | 位置 | 類型 | 內容 |
|---|---|---|---|
| 1 | §實測基線 §7 | **新增** | 「檢索上下文對法條抽取的影響」實測（99 卷有法條／基準 2017 條，M1 chunker 與 v2 chunker 各一表）。**同時記錄四個方法學錯誤與更正**——這些比數字更值得留檔 |
| 2 | FR-011b | **新增** | `laws[]` **分兩層**：文件層 `doc_ordered`（ingest 成本為零，pre-filter 用）／片段層預設 `chunk`（召回 0.7283，引用用）、備選 `ordered_ab`（召回 0.9494）。明列兩層**回答不同問題**，非雙真相來源。並點出**現況缺陷**：v2 逐 chunk 抽再聯集＝`chunk`，會繼承約 20pp 召回損失，故要求 parser 額外輸出 `laws_doc` |
| 3 | FR-011b | **硬約束** | `ordered_ab` 命中時法規名可能在**鄰居區塊**，故**必須附每條法條的來源 chunk 位置**，否則偽造來源（憲法 VI／XI） |
| 4 | FR-011c | **新增** | `laws_mode` 欄位必填（記錄實際使用的模式代號）。**這是 spec 008 的接口點**：欄位到位後切換模式不必改 schema |
| 5 | User Story 5 | **新增** | 「模式可切換＋回饋閉環」明列為 **P3 且本 spec 只留接口**，實作（UI／端點／feedback 表）屬 spec 008。理由：跨檢索／生成／UI／資料表四層，違反憲法 I（小切片）與 X（不擴張 scope） |
| 6 | SC-011b | **新增** | 召回門檻（`chunk` ≥ 0.70、`ordered_ab` ≥ 0.85，同基準重跑）；**precision 暫不設門檻**，因為基準本身會因縮寫誤歸屬 |
| 7 | 裁決記錄 | 追加 | rev5 的六項裁決（`laws[]` 分層、`laws_mode` 必填、`ordered_ab` 附來源、`parent_expand` 不納入、量測須記 chunker、暫不設 precision 門檻） |
| 8 | FR-006／SC-009／Open Q8 | **裁決（方案 A）** | `hearing_closed_dates date[]` **恆為陣列** ＋ `hearing_closed_raw`；單值 `hearing_closed` 為 **generated column 取最後一個日期**（＝全案辯論皆終結之日），**不手動維護**（避免兩個真相來源）。因單日期案件長度為 1，**全庫只有一種形狀** |
| 8 | （否定結論留檔） | 記錄 | **`parent_expand` 不納入**：正確實作（父塊＋chunk 依序共用游標）召回 0.7333，**低於**鄰居展開 `ordered` 0.7938；且覆蓋率僅 23.3%、16 卷完全無 parent。**保留量測工具**，待 parent 指派規則改進後重測 |
| 9 | Open Q8 | **新增實測依據** | 分母 100 卷：含「辯論終結」的句子中單日期 52 句、多日期 9 句、零日期 45 句；但 9 句多日期裡**只有 1 句含「分別」**，其餘 8 句是**原審判決日期落在同一句附近**（反例）。故真實多值 **1/100**，且「只在分別緊鄰辯論終結時啟用多日期」有實測反例支撐（放寬會誤判那 8 卷） |
| 10 | SC-009 | **強化驗收** | generated column 必須**真的由陣列衍生**：改寫陣列內容後讀單值必須跟著變；並斷言 `hearing_closed == hearing_closed_dates[last]`。若單值是手動維護欄位，此驗收即失敗 |
| 11 | 狀態行 | 更新 | **specify 階段收斂完成，Q8 已裁決，可進 clarify／plan** |
| 9 | FR-006／SC-009／Open Q8 | **裁決（方案 A）** | `hearing_closed_dates date[]` **恆為陣列** ＋ `hearing_closed_raw`；單值 `hearing_closed` 為 **generated column 取最後一個日期**（＝全案辯論皆終結之日），**不手動維護**（避免兩個真相來源）。因單日期案件長度為 1，**全庫只有一種形狀** |
| 10 | Open Q8 | **新增實測依據** | 分母 100 卷：含「辯論終結」的句子中單日期 52 句、多日期 9 句、零日期 45 句；但 9 句多日期裡**只有 1 句含「分別」**，其餘 8 句是**原審判決日期落在同一句附近**。故真實多值 **1/100**，且「只在分別緊鄰辯論終結時啟用多日期」有實測反例支撐（放寬會誤判那 8 卷） |
| 11 | SC-009 | **強化驗收** | generated column 必須**真的由陣列衍生**：改寫陣列內容後讀單值必須跟著變；並斷言 `hearing_closed == hearing_closed_dates[last]`。若單值是手動維護欄位，此驗收即失敗 |

### 這次量測推翻了什麼（記錄以免日後重蹈）

1. **「句首殘缺時回退前句的 `cur`」不是無效，是根本不存在**。單一 chunk 內 `cur` 已跨句
   延續，「前一句的 `cur`」就是當前的 `cur`，**沒有東西可回退**。此修法方向直接廢棄。
2. **「帶 ±1 鄰居 chunk 無效、順序無關」是實作錯誤造成的假象**。第一版把鄰居**各自
   獨立掃描**再取聯集，游標從未跨越鄰居。改成**依序串接共用游標**後，召回
   **0.7283 → 0.8632（+13.5pp）**。教訓：**量測「上下文是否有效」時，
   必須確認實作真的把上下文傳下去了**，否則量到的是實作缺陷不是對象性質。
3. **「整份依序掃描」不能既當基準又當受評模式**——那會得到召回 1.0000／precision 1.0000
   的同義反覆結果。第一版正是如此浪費了一次量測。
4. **縮寫表不是無腦加上去就好**：它會把 `職安法` 當成游標更新來源，使後續
   `第185條` 被歸給《職業安全衛生法》，而判決原意是《勞動基準法》§185。
   故縮寫表**只作後備**（FR-011），且 precision 門檻在人工標註前不設。
5. 淨效果：**排序（+13.5pp）與縮寫表（+7.1pp）兩個因素各自有效且可疊加**，
   `ordered_ab` 召回 0.9494，代價是引用必須標明來源區塊。
