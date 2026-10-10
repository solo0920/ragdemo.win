-- 2026-10-10 法規層擴充：納入 order API（命令 10,452 部）
--
-- 為什麼需要這個檔：schema.sql 用的是 CREATE TABLE IF NOT EXISTS，
-- 對**已經存在**的 law 表**不會**加欄位。所以擴充必須另給 ALTER。
--
-- 全部冪等（IF NOT EXISTS），可重跑。
--
-- 背景見 spec 007 §實測基線 §11／FR-017：
--   原本只抓 law API（法律＋憲法 1,351 部），**order API（命令 10,452 部）從未下載**。
--   金融法規 99 部中有 52 部是命令（證券商管理規則、期貨商管理規則、
--   保險代理人管理規則…），缺了整個命令層，那些法規**連條文全文都取不到**。

-- ① law 表：新增來源與官方連結
--    law_url 是 spec 007 FR-018 引用呈現的連結來源——每處法條引用都要能跳官網。
ALTER TABLE law ADD COLUMN IF NOT EXISTS source_api  TEXT NOT NULL DEFAULT '';
ALTER TABLE law ADD COLUMN IF NOT EXISTS law_url     TEXT NOT NULL DEFAULT '';

-- ② law_level 的值域從「憲法/法律」擴到含「命令」。
--    不加 CHECK 約束：上游層級值可能再變（例如新增「自治條約」），
--    加約束會在下次同步時整批失敗。**寧可寬鬆也不要讓同步炸掉。**
COMMENT ON COLUMN law.law_level IS
  '憲法 / 法律 / 命令。2026-10-10 起含命令（order API 10,452 部）。不加 CHECK：上游層級值可能再變，約束會讓同步整批失敗';

COMMENT ON COLUMN law.source_api IS
  'law / order：該法規來自哪個 moj API。兩個 API 的 pcode 與 LawName 實測零重疊';

COMMENT ON COLUMN law.law_url IS
  '官方法規頁 URL（LawURL 原文）。spec 007 FR-018 的引用連結來源，不可省';

-- ③ 引用呈現要按法規名查；命令層 10,452 部名字會大量撞名（不同主管機關的
--    「管理規則」），故只建普通索引、**不建 UNIQUE**。
CREATE INDEX IF NOT EXISTS idx_law_name   ON law(law_name);
CREATE INDEX IF NOT EXISTS idx_law_source ON law(source_api, law_level);

-- ④ law_import 記錄「這次匯入涵蓋哪些 API」，否則日後看到 11,803 部會以為
--    全部來自 law API——而那正是本次修正前的錯誤认知。
ALTER TABLE law_import ADD COLUMN IF NOT EXISTS sources TEXT NOT NULL DEFAULT '';
COMMENT ON COLUMN law_import.sources IS
  '本次匯入涵蓋的 API 清單（逗號分隔），例：law,order';