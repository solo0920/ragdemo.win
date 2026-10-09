-- 司法判決 metadata DDL（spec 004 T016 / FR-014, FR-015, FR-024）
--
-- 與 registry 的 backends 同庫（ragdemo）。全部冪等（IF NOT EXISTS）。
--
-- ⚠️ 本檔案**不含**任何推論出來的欄位。理由見下方「為什麼沒有 court 欄位」。

-- ─────────────────────────────────────────────────────────────────────────
-- ① judgment 層：一份判決一列，jid 為主鍵
--
-- 主鍵是 `jid`，**不是** entry_path、不是自增 ID、不是陣列索引。
-- 理由：`jid` 是 source 的 authoritative identity（spec §Identity VERIFIED：
-- `JID` = archive entry basename 去掉 `.json`）。用檔名或自增 ID 會讓
-- 「這列對應到來源哪一份」這個問題變成需要另外查表的推論。
--
-- ⚠️ `jid` 是 **TEXT**，不是整數、不是複合型別。原因見 FR-028：它合法地有
-- 5-field 與 6-field 兩種形狀，而兩者編碼**不同的文件類別**（139 筆 5-field
-- 全部是憲法法庭）。任何把它拆成 (court, year, case, …) 的 schema 都會讓那
-- 139 筆要嘛無法插入、要嘛被塞進假的 NULL 欄位。
CREATE TABLE IF NOT EXISTS judgment (
    jid             TEXT PRIMARY KEY,        -- opaque composite；**不可**位置解析（FR-028）
    jyear           TEXT NOT NULL DEFAULT '',  -- 案件年份；與 jdate 獨立（FR-029）
    jcase           TEXT NOT NULL DEFAULT '',  -- opaque 代碼；不是 court、不是案件類型
    jno             TEXT NOT NULL DEFAULT '',
    jdate           TEXT NOT NULL DEFAULT '',  -- 發布批次日期；不可由 jyear 推導
    jtitle          TEXT NOT NULL DEFAULT '',
    jfull           TEXT NOT NULL DEFAULT '',  -- 唯一 authoritative 全文；**逐 byte 原樣**
    jpdf            TEXT NOT NULL DEFAULT '',  -- 可為空，且那是合法的（FR-030）

    -- ── provenance（追溯用，不是司法內容）──────────────────────────────
    entry_path      TEXT NOT NULL DEFAULT '',  -- archive 內路徑（反斜線，與 inventory 一致）
    entry_sha256    TEXT NOT NULL DEFAULT '',  -- 該 entry 解壓 bytes 的 SHA-256
    artifact_sha256 TEXT NOT NULL DEFAULT '',  -- 所屬 artifact 的 SHA-256
    jfull_sha256    TEXT NOT NULL DEFAULT '',  -- jfull 的 SHA-256（增量比對／漂移偵測）

    jfull_char_len  INT  NOT NULL DEFAULT 0,   -- 量測值，不是內容
    jfull_byte_len  INT  NOT NULL DEFAULT 0,   -- 量測值
    crlf_count      INT  NOT NULL DEFAULT 0,   -- 量測值；>0 表示原文是 CRLF

    -- ── import 稽核 ───────────────────────────────────────────────────
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- entry_path 唯一：同一份 archive 裡不可能有兩筆相同路徑。
-- 這個約束存在的理由是讓「同一個 entry 被插入兩次（entry_path 相同、jid 不同）」
-- 立刻失敗 —— 那代表 JID 與路徑的對應被破壞了，而那是 identity 的核心。
CREATE UNIQUE INDEX IF NOT EXISTS idx_judgment_entry_path ON judgment(entry_path);

-- 常用路徑：增量比對（jfull_sha256）、faceting（jyear/jdate/jcase）
CREATE INDEX IF NOT EXISTS idx_judgment_jfull_sha256 ON judgment(jfull_sha256);
CREATE INDEX IF NOT EXISTS idx_judgment_jyear         ON judgment(jyear);
CREATE INDEX IF NOT EXISTS idx_judgment_jdate         ON judgment(jdate);
CREATE INDEX IF NOT EXISTS idx_judgment_jcase         ON judgment(jcase);
CREATE INDEX IF NOT EXISTS idx_judgment_artifact      ON judgment(artifact_sha256);

-- ⚠️ 全文檢索索引**刻意沒有建**。
-- 若日後要建，必須是 tsvector（PostgreSQL 的實作），且那屬於 T019+ 的
-- 檢索設計。本檔案只定義「儲存什麼」，不定義「怎麼查」。

-- ─────────────────────────────────────────────────────────────────────────
-- ② 為什麼沒有 court 欄位
--
-- FR-015 說「judgment-level metadata MUST include the court」。但同一份 spec 的
-- §Court — NOT an authoritative field 記載了實測結果：
--
--   · source JSON **沒有** court 欄位（VERIFIED）
--   · court 名稱出現在 `JFULL` line 0，比例是 487/494
--   · 那 7 個例外裡有一筆 line 0 是**案件說明**而非法院名稱：
--     「因本件為對不公開案件聲請停止執行，故本件裁定不公開。」
--   · line 0 混合了法院與文件類型（民事裁定／高等裁定／支付命令…）
--   · **沒有**任何 court ↔ 目錄 的對應存在於 corpus 或本 repo
--
-- 所以「從 JFULL line 0 推論 court」會是 FR-016 明確禁止的 synthesize，
-- 而 tasks.md T022 的 acceptance 也直說「no court inference」。
--
-- 本檔案的處理：**不存 court 欄位**，而不是存一個推論出來的值。
-- 一個明確不存在的欄位，會讓 T022 在顯示時被迫處理「沒有 court 怎麼辦」；
-- 一個存了推論值的欄位，會讓下游以為那是權威事實。
--
-- 這是 spec 內部的張力（FR-015 vs §Court），而 spec 自己在 §Court 結尾寫：
-- 「`court` remains **UNKNOWN / INFERRED** and MUST NOT enter an authoritative
-- contract」。以證據優先於前提的原則，本檔案採後者。
-- 若維護者要推翻這個判斷，那是一個 maintainer decision，不該由 DDL 默默決定。

-- ─────────────────────────────────────────────────────────────────────────
-- ③ 刪除語意 —— **尚未決定**
--
-- spec 002 的「never delete」**不適用於判決**：一份判決可能被撤回
-- （vacated）或重新發布。若沒有刪除路徑，撤回的判決會永遠留在庫裡並被檢索到。
--
-- 本檔案**刻意不定義** delete policy —— 那是一個 maintainer decision
-- （plan.md §Remaining blockers #3）。
--
-- 本檔案定義的是 upsert 語意：`ON CONFLICT (jid) DO UPDATE`，
-- 對同一份未變的資料重跑會 **0 insert / 0 update**（acceptance 的明確要求）。
--
-- ⚠️ 注意 upsert 與 delete 是兩件事。`ingest/laws` 那邊的教訓
-- （tests/test_three_layer_consistency.py）：只 upsert 從不刪除，會讓上游已
-- 消失的條文永遠留在庫裡，症狀是「引用清單裡少了某條」而使用者看不出原因。
-- 判決這邊同樣面臨這個問題，但**解法是 delete policy，不是偷偷在 upsert 裡
-- 加刪除**。加進去的話，未來的維護者會誤以為那是有意設計的政策。

-- ─────────────────────────────────────────────────────────────────────────
-- ④ 與 law / article 表的界線
--
-- 本檔案定義的 `judgment` 與 `ingest/laws/pg_schema.sql` 定義的 `law` /
-- `article` 是**獨立的表**，沒有 foreign key、沒有 view、沒有 trigger。
--
-- 理由：一份判決引用某部法律，這個關係是「文本裡的字串」而不是「外鍵」。
-- 若建 FK，誰是主從？一份判決引用數十部法律，而被引用的法律可能不在本庫
-- （survey §4.3：本 corpus 最常被引用的法條名稱不在 laws_meta.jsonl 裡）。
-- 建成 FK 會強迫我們為了滿足外鍵而**創造**那些法律列 —— 那就是偽造資料。
--
-- citation 的關聯屬於 T012–T014 的 detection（已實作為 candidate）與之後的
-- Stage B（resolution，刻意延後）。它不是外鍵。
