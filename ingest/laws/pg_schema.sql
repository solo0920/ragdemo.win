-- 法規全量 metadata DDL（law.moj.gov.tw → normalize.py → 本庫）
-- 與 registry 的 backends 同庫（ragdemo），asyncpg 可跑。全部冪等（IF NOT EXISTS）。
-- 用途：去重／增量／不重複 embedding、前端篩選 faceting、來源審計。
--
-- ⚠ **2026-10-10：涵蓋兩個 API**（原本只有 law）
--   law   https://law.moj.gov.tw/api/ch/law/json    1,351 部（法律 1,342＋憲法 9）
--   order https://law.moj.gov.tw/api/ch/order/json  10,452 部（命令）
--   兩個 `Laws[]` schema 相同，pcode 與 LawName 實測**零重疊**，故合併進同一張表。
--   缺席 order 層的代價：金融法規 99 部中有 52 部是命令，缺了就**連條文全文都取不到**
--   （spec 007 §11／FR-017）。

-- ① 法規層：一部一列，pcode 為主鍵（A0000001…，LawURL 末段，API 無重複）
CREATE TABLE IF NOT EXISTS law (
    pcode              TEXT PRIMARY KEY,                -- A0000001…（LawURL 末段）
    law_name           TEXT NOT NULL DEFAULT '',        -- 中華民國憲法
    law_level          TEXT NOT NULL DEFAULT '',        -- 憲法 / 法律 / **命令**（order API）
    source_api         TEXT NOT NULL DEFAULT '',        -- law / order（哪個 API 來的；FR-017）
    law_url            TEXT NOT NULL DEFAULT '',        -- 官方頁 URL。**引用呈現的連結來源**
                                                         -- （spec 007 FR-018），不可省
    law_category       TEXT NOT NULL DEFAULT '',        -- 行政＞交通部＞組織目…（非詞表，當自由 faceting）
    law_modified_date  TEXT NOT NULL DEFAULT '',        -- 異動日期 YYYYMMDD（字串保留原樣，要 DATE 可隨時 cast）
    law_effective_date TEXT NOT NULL DEFAULT '',        -- 生效日期 YYYYMMDD
    law_effective_note TEXT NOT NULL DEFAULT '',        -- 生效內容（例如「施行日期由行政院定之」）
    is_abandoned       BOOLEAN NOT NULL DEFAULT FALSE,  -- LawAbandonNote 非空＝已廢止
    law_abandon_note   TEXT NOT NULL DEFAULT '',        -- 廢止註記原文（例：112年6月30日廢止）
    has_eng            BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否英譯
    eng_name           TEXT NOT NULL DEFAULT '',        -- 英文法規名稱（906 部有）
    attach_count       INT NOT NULL DEFAULT 0,          -- 附件數
    foreword           TEXT NOT NULL DEFAULT '',        -- 前言（僅 3 部有，少數用）
    histories          TEXT NOT NULL DEFAULT '',        -- 沿革全文（1025 部有，可做「法條變遷/除罪」QA）
    article_count      INT NOT NULL DEFAULT 0,          -- 條文數（快取，免 count）
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 引用呈現要按「法規名」找；命令層有 10,452 部，名字會大量撞名（不同主管機關的
-- 「管理規則」），故**不建 UNIQUE**，只建普通索引。
CREATE INDEX IF NOT EXISTS idx_law_name   ON law(law_name);
CREATE INDEX IF NOT EXISTS idx_law_source ON law(source_api, law_level);

-- ⚠ 2026-10-10 新增欄位（既有表需 ALTER；schema.sql 只處理 IF NOT EXISTS 的表，
-- 所以對**已存在**的 law 表要另外跑一次 migrations。見 scripts/migrate-laws-20261010.sql）

-- ② 條文層：一條一列，(pcode, article_seq) 複合主鍵 → upsert 冪等
CREATE TABLE IF NOT EXISTS article (
    pcode        TEXT NOT NULL REFERENCES law(pcode) ON DELETE CASCADE,
    article_seq  INT NOT NULL,                          -- 法規內序號（flatten 序）
    article_no   TEXT NOT NULL DEFAULT '',              -- 第 1 條（去空）
    chapter      TEXT NOT NULL DEFAULT '',              -- 章節標題（C 型態就地帶入；29% 無章節則為空）
    content      TEXT NOT NULL DEFAULT '',              -- 條文全文（已清洗：BOM/CRLF/去首尾空白）
    char_len     INT NOT NULL DEFAULT 0,                -- 字數（分塊判斷用）
    is_repealed  BOOLEAN NOT NULL DEFAULT FALSE,        -- 「（刪除）」已刪除條文（1,019 則）
    is_abandoned BOOLEAN NOT NULL DEFAULT FALSE,        -- 去正規化自 law.is_abandoned（Qdrant payload 免 join）
    content_hash TEXT NOT NULL DEFAULT '',              -- sha256(content) hex=64；增量比對，相同即跳過 re-embed
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pcode, article_seq)
);
-- 檢索/增量常用路徑：整部法規→pcode；辨別「可吃」→ (is_repealed,is_abandoned)
CREATE INDEX IF NOT EXISTS idx_article_pcode  ON article(pcode);
CREATE INDEX IF NOT EXISTS idx_article_hash   ON article(content_hash);
CREATE INDEX IF NOT EXISTS idx_article_usable ON article(is_repealed, is_abandoned);

-- ③ 匯入審計：每次全量/增量餵一次，留來源變動軌跡
CREATE TABLE IF NOT EXISTS law_import (
    id             BIGSERIAL PRIMARY KEY,
    source_url     TEXT NOT NULL DEFAULT '',    -- https://law.moj.gov.tw/api/ch/law/json
    sources        TEXT NOT NULL DEFAULT '',    -- 本次涵蓋的 API 清單（law,order；2026-10-10 起）
    update_date    TEXT NOT NULL DEFAULT '',    -- API UpdateDate（如 2026/9/11 上午 12:00:00）
    source_sha256  TEXT NOT NULL DEFAULT '',    -- ChLaw.json 的 sha256 → 抓了相同檔就直接跳過全量
    laws_count     INT NOT NULL DEFAULT 0,
    articles_count INT NOT NULL DEFAULT 0,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ                 -- NULL=沒跑完（中斷標記）
);