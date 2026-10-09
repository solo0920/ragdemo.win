"""`pg_schema.sql` / `store.py` 的契約測試（spec 004 T016）。

## 為什麼全部是靜態斷言

**沒有任何測試連 PostgreSQL。** 這是 repo 既有慣例 —— `ingest/laws` 那邊的
`tests/test_three_layer_consistency.py` 也是這樣（它驗「pg_load 必須有刪除」
這條**規則**，而不實際刪東西）。

理由不只是「測試不該碰外部服務」：CI 沒有資料庫，而一個只有在有資料庫時
才能跑的測試，等於把「欄位有沒有對錯」這個最需要驗的事情放到沒有人會跑的地方。

所以 DDL 的驗證方式是**靜態解析 SQL 文字** + **靜態驗證 `to_row()` 的欄位
順序**。後者尤其重要：19 個欄位若順序錯了，型別都對、upsert 不會報錯，只是
值寫進了錯誤的欄位。

## 三個必須存在的欄位對應

1. `COLUMNS`（Python）↔ `INSERT` 子句 ↔ `pg_schema.sql` 的欄位定義 —— 三者
   順序必須一致。
2. `FORBIDDEN_COLUMNS` —— 不得有 `court`、`case_type`、`statute`。
3. 冪等：SQL 必須有 `ON CONFLICT (jid) DO UPDATE` 且帶 `WHERE`（否則未變的
   資料也會被 update）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JDIR = ROOT / "ingest" / "judgements"
if str(JDIR) not in sys.path:
    sys.path.insert(0, str(JDIR))

import chunk as CH  # noqa: E402
import document  # noqa: E402
import store  # noqa: E402
import text as T  # noqa: E402

DOCS = ROOT / "tests" / "fixtures" / "judgements" / "docs"
CIVIL = DOCS / "civil_6field.json"
CONSTITUTIONAL = DOCS / "constitutional_5field_empty_jpdf.json"

DDL = (JDIR / "pg_schema.sql").read_text(encoding="utf-8")


def make_doc(p: Path = CIVIL) -> document.JudgmentDocument:
    return document.from_document(
        json.loads(p.read_text(encoding="utf-8")),
        entry_path="202607\\x\\TPDV,115,訴,2468,20260901,1.json",
        sha256="a" * 64,
    )


# ── DDL 的靜態結構 ─────────────────────────────────────────────────────────
#
# 底下所有檢查都在**去掉 `--` 註解後**的 SQL 上做。
#
# 理由：本 repo 的 SQL 註解會解釋「為什麼**沒有**某個欄位」，而那些解釋裡
# 必然出現那個欄位名（例如 `jid` 那行的註解提到 `court`）。不剝掉註解的話，
# 「有沒有 court 欄位」會被解釋缺欄的理由觸發 —— 而那正是本檔案第四度遇到
# 同一個陷阱（T004 的 `zlib` 註解、T008 的 `court` docstring、T011 的
# `_clean` 說明）。處理方式一致：區分「做了什麼」與「說明了什麼」。


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _table_body(ddl: str = DDL) -> str:
    """`judgment` 的欄位定義區塊，已去除行內註解。"""
    body = re.search(r"CREATE TABLE IF NOT EXISTS judgment \((.*?)\n\);", ddl, re.S)
    assert body, "找不到 judgment 定義"
    return _strip_sql_comments(body.group(1))


def test_judgment_table_exists():
    assert "CREATE TABLE IF NOT EXISTS judgment" in DDL


def test_all_statements_are_idempotent():
    """所有 DDL 必須冪等（`IF NOT EXISTS`）。

    跟 `ingest/laws/pg_schema.sql` 同樣的理由：loader 每次啟動都會執行
    DDL，非冪等的話第二次就炸。
    """
    code = _strip_sql_comments(DDL)
    assert "CREATE TABLE IF NOT EXISTS judgment" in code
    for stmt in re.findall(r"CREATE (?:UNIQUE )?INDEX[^;]+;", code):
        assert "IF NOT EXISTS" in stmt, stmt


def test_primary_key_is_jid():
    """主鍵是 `jid` —— authoritative identity。

    不得是 entry_path、自增 ID 或任何推論出來的東西。
    """
    m = re.search(r"CREATE TABLE IF NOT EXISTS judgment \((.*?)\n\);", DDL, re.S)
    assert m, "找不到 judgment 定義"
    body = m.group(1)
    pk = re.search(r"jid\s+TEXT PRIMARY KEY", body)
    assert pk, "jid 必須是 TEXT PRIMARY KEY"
    assert "PRIMARY KEY (auto" not in body
    assert "SERIAL" not in body and "GENERATED" not in body


def test_jid_is_stored_as_text():
    """`jid` 必須是 TEXT，不是整數或複合型別（FR-028）。

    拆成 (court, year, case) 的 schema 會讓 139 筆 5-field 的憲法法庭資料
    要嘛插不進去、要嘛被塞進假的 NULL 欄位。
    """
    body = _table_body()
    assert re.search(r"\bjid\s+TEXT\b", body)
    assert not re.search(r"\bjid\s+(INT|BIGINT|SMALLINT)\b", body)
    # 不得有拆解 JID 的欄位
    for forbidden in ("court", "branch_code", "inst_code", "court_code"):
        assert not re.search(rf"\b{forbidden}\b", body)


def test_all_seven_source_fields_are_columns():
    """7 個來源欄位都在（JID 是主鍵，所以是 8 個 J* 欄位）。"""
    body = _table_body()
    for col in ("jyear", "jcase", "jno", "jdate", "jtitle", "jfull", "jpdf"):
        assert re.search(rf"\b{col}\s+TEXT NOT NULL", body), f"缺少 {col}"


def test_no_inferred_columns_exist():
    """不得有推論出來的欄位（FR-016）。

    特別是 `court` —— spec §Court 記載 source 沒有這個欄位，而推論它是禁止的。
    """
    body = _table_body()
    for forbidden in (
        "court", "case_type", "branch", "section", "title_normalized",
        "year_int", "court_name",
    ):
        assert not re.search(rf"\b{forbidden}\b", body), f"不得有 {forbidden} 欄位"


def test_provenance_columns_exist():
    """追溯鏈必須存在：archive → entry → document。"""
    body = _table_body()
    for col in ("entry_path", "entry_sha256", "artifact_sha256", "jfull_sha256"):
        assert re.search(rf"\b{col}\s+TEXT", body), f"缺少 {col}"


def test_entry_path_is_unique():
    """`entry_path` 唯一 —— 讓「同一 entry 兩次插入」立刻失敗。

    那代表 JID 與路徑的對應被破壞了，而那是 identity 的核心。
    """
    assert re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS idx_judgment_entry_path", DDL)


def test_measurement_columns_are_not_content():
    """`jfull_char_len` / `crlf_count` 是量測值，不是內容。

    它們是 INT，不是 TEXT —— 存成文字會讓「比對長度」變成字串比較。
    """
    body = _table_body()
    for col in ("jfull_char_len", "jfull_byte_len", "crlf_count"):
        assert re.search(rf"\b{col}\s+INT", body), f"{col} 應為 INT"


def test_jpdf_column_allows_empty_string():
    """`jpdf` 是 `TEXT NOT NULL DEFAULT ''` —— 空字串是合法狀態（FR-030）。

    若它是 `NOT NULL` 卻沒有 DEFAULT ''，插入空字串雖然可以（空字串不是
    NULL），但重點是 DDL **不該**有任何「jpdf 必須非空」的約束。
    """
    body = _table_body()
    m = re.search(r"\bjpdf\s+TEXT NOT NULL DEFAULT ''", body)
    assert m
    assert "jpdf" in body and "CHECK" not in re.search(r"jpdf[^\n]*", body).group(0)


def test_no_law_table_foreign_key():
    """不得對 law/article 建 FK。

    理由寫在 DDL ④：一份判決引用數十部法律，而被引用的法律可能不在本庫
    （survey §4.3：最常被引用的法條名稱不在 laws_meta.jsonl）。建成 FK 會
    強迫我們為了滿足外鍵而**創造**那些法律列 —— 那就是偽造資料。
    """
    assert "REFERENCES law" not in DDL
    assert "REFERENCES article" not in DDL
    assert "FOREIGN KEY" not in DDL


def test_no_delete_policy_is_defined():
    """DDL 不得定義刪除語意 —— 那是 maintainer decision。

    刻意沒有 `ON DELETE CASCADE`、沒有 trigger、沒有任何刪除相關陳述。
    這條測試的存在是為了讓「刪除語意尚未決定」這件事**保持可見**；
    若日後有人加進去，這裡會紅並迫使他說明那是什麼決定。
    """
    code = _strip_sql_comments(DDL)
    assert "ON DELETE" not in code
    assert "TRIGGER" not in code.upper()
    assert "DELETE FROM" not in code


def test_ddl_documents_the_court_omission():
    """DDL 必須**說明**為什麼沒有 court 欄位。

    一個沒有解釋的缺欄會讓後人「順手補上」—— 而那正是 FR-016 禁止的。
    """
    assert "為什麼沒有 court 欄位" in DDL
    assert "FR-016" in DDL


def test_ddl_documents_the_pending_delete_decision():
    assert "刪除語意" in DDL
    assert "maintainer decision" in DDL


# ── to_row() 的欄位對應 ─────────────────────────────────────────────────────

def test_columns_tuple_matches_the_insert_clause():
    """Python 的 `COLUMNS` 與 SQL 的 INSERT 子句必須一致。"""
    sql = store.build_sql()
    m = re.search(r"INSERT INTO judgment \((.*?)\)\nVALUES", sql, re.S)
    assert m
    sql_cols = tuple(c.strip() for c in m.group(1).split(","))
    assert sql_cols == store.COLUMNS


def test_to_row_has_one_value_per_column():
    row = store.to_row(make_doc(), artifact_sha256="b" * 64)
    assert len(row) == len(store.COLUMNS)


def test_to_row_values_map_to_the_right_columns():
    """逐欄位比對 —— 順序錯了型別仍是 str，不會報錯。"""
    doc = make_doc()
    row = store.to_row(doc, artifact_sha256="b" * 64)
    by = dict(zip(store.COLUMNS, row))

    assert by["jid"] == doc.jid
    assert by["jyear"] == doc.jyear
    assert by["jcase"] == doc.jcase
    assert by["jno"] == doc.jno
    assert by["jdate"] == doc.jdate
    assert by["jtitle"] == doc.jtitle
    assert by["jfull"] == doc.jfull
    assert by["jpdf"] == doc.jpdf
    assert by["entry_path"] == doc.entry_path
    assert by["entry_sha256"] == doc.sha256
    assert by["artifact_sha256"] == "b" * 64


def test_to_row_does_not_modify_the_document():
    doc = make_doc()
    before = doc.jfull
    store.to_row(doc, artifact_sha256="x")
    assert doc.jfull == before


def test_to_row_preserves_jfull_byte_exactly():
    """`jfull` 進 DB 的必須是**逐 byte 相同**的原文。

    這是整個 persistence 邊界最關鍵的一條：任何 strip / 正規化都會讓儲存的
    內容與來源不同，而那正是本 feature 要防的事。
    """
    doc = make_doc()
    row = store.to_row(doc, artifact_sha256="x")
    jfull = dict(zip(store.COLUMNS, row))["jfull"]
    assert jfull == doc.jfull
    assert jfull.encode("utf-8") == doc.jfull.encode("utf-8")
    assert jfull.count("\r\n") == doc.jfull.count("\r\n")
    assert jfull.count("　") == doc.jfull.count("　")


def test_to_row_records_measurements():
    """量測欄位必須與實際內容一致。"""
    doc = make_doc()
    row = store.to_row(doc, artifact_sha256="x")
    by = dict(zip(store.COLUMNS, row))
    t = T.LosslessText.from_string(doc.jfull)
    assert by["jfull_char_len"] == t.char_len
    assert by["jfull_byte_len"] == t.byte_len
    assert by["crlf_count"] == t.crlf_count
    assert by["jfull_sha256"] == t.sha256


def test_to_row_handles_empty_jpdf():
    """空 `JPDF` 必須照樣成為空字串，不是 None。"""
    doc = make_doc(CONSTITUTIONAL)
    row = store.to_row(doc, artifact_sha256="x")
    by = dict(zip(store.COLUMNS, row))
    assert by["jpdf"] == ""
    assert by["jpdf"] is not None


def test_to_row_keeps_5field_jid_opaque():
    """5-field JID 照原樣存，不拆解。"""
    doc = make_doc(CONSTITUTIONAL)
    row = store.to_row(doc, artifact_sha256="x")
    assert dict(zip(store.COLUMNS, row))["jid"] == "JCCC,115,審裁,1158,20260701"


def test_jyear_and_jdate_are_separate_columns():
    """兩者獨立儲存（FR-029）—— 不得合併成一個欄位。"""
    assert "jyear" in store.COLUMNS
    assert "jdate" in store.COLUMNS
    doc = make_doc()
    row = store.to_row(doc, artifact_sha256="x")
    by = dict(zip(store.COLUMNS, row))
    assert by["jyear"] != by["jdate"]


def test_forbidden_columns_are_declared():
    assert "court" in store.FORBIDDEN_COLUMNS
    assert "statute" in store.FORBIDDEN_COLUMNS
    for c in store.FORBIDDEN_COLUMNS:
        assert c not in store.COLUMNS


# ── 冪等 ──────────────────────────────────────────────────────────────────

def test_sql_is_idempotent_upsert():
    sql = store.build_sql()
    assert "ON CONFLICT (jid) DO UPDATE" in sql


def test_upsert_has_a_where_clause_for_unchanged_rows():
    """`WHERE` 是冪等的關鍵。

    少了它，每次重跑都會 update 全部列（`updated_at` 全變），而那讓「我到底
    改了什麼」變成無法回答的問題。acceptance 明確要求「重跑 0 insert /
    0 update」。
    """
    sql = store.build_sql()
    assert "WHERE judgment.jfull_sha256 <> EXCLUDED.jfull_sha256" in sql


def test_upsert_updates_all_source_fields():
    sql = store.build_sql()
    for col in ("jyear", "jcase", "jno", "jdate", "jtitle", "jfull", "jpdf"):
        assert f"{col}=EXCLUDED.{col}" in sql, f"upsert 未更新 {col}"


def test_upsert_does_not_update_the_primary_key():
    """upsert 不得改主鍵本身。"""
    sql = store.build_sql()
    update_part = sql.split("DO UPDATE SET", 1)[1]
    assert "jid=EXCLUDED.jid" not in update_part


def test_sql_contains_no_delete():
    """沒有刪除語意（maintainer decision 未定）。"""
    assert "DELETE" not in store.build_sql().upper()


# ── 這個模組不做什麼 ───────────────────────────────────────────────────────

def test_store_does_not_read_env_vars_directly():
    """不得自行讀環境變數 —— DSN 一律走 `_hostenv`。

    ## 為什麼這條重要

    `scripts/env-audit.py` 掃描的是**哪些檔案讀了哪些環境變數**，然後把結果
    寫進 `.env.example`（「讀取處：… 等 N 處」）。若 `store.py` 直接讀
    `POSTGRES_DSN`，那個 N 會 +1，而版控中的 `.env.example` 還寫著舊數字 ——
    `tests/test_env_audit.py` 的 `tracked == body` 斷言紅燈，而那份
    `.env.example` 正是 pre-push hook 用來擋 push 的東西。

    一個程式碼讀了變數卻沒被登記，登記的數字就不再可信。

    ## 用 AST 而非字串搜尋

    掃「有沒有字串 `os.environ`」會連 **docstring 裡說明「不要這樣寫」的
    範例**都算進去 —— 而那正是本 repo 第五次遇到同一個陷阱（T004 的 `zlib`
    註解、T008 的 `court` docstring、T011 的 `_clean` 說明、T016 的 SQL 註解）。
    AST 看的是實際的 `Import` 與屬性存取。
    """
    import ast

    src = (JDIR / "store.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 實際的屬性存取鏈：os.environ[...] / os.getenv(...)
    actual_env_reads: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
        ):
            actual_env_reads.append("os.environ[...]")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
        ):
            actual_env_reads.append("os.getenv(...)")

    assert not actual_env_reads, (
        f"store.py 不得自行讀環境變數（發現 {actual_env_reads}）；"
        "請用 ingest/laws/_hostenv.py 的 host_postgres_dsn()"
    )

    # 也不得 import os（沒有它就不可能直接讀）
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert "os" not in imported


def test_store_delegates_dsn_to_hostenv():
    """DSN 必須來自 `_hostenv.host_postgres_dsn()`（laws 已驗證的那條路徑）。"""
    src = (JDIR / "store.py").read_text(encoding="utf-8")
    assert "host_postgres_dsn" in src
    assert "ingest/laws/_hostenv" in src or "_hostenv" in src


def test_store_does_not_reintroduce_localhost_fallback():
    """不得加回 `localhost` fallback。

    2026-10-02 實測證實那個 fallback 在三台機器上都是壞的（postgres 綁
    `${TS_IP}:5432`），症狀是「沒有錯誤輸出，只是資料沒更新」。重寫一套
    只會把那個問題帶回來。
    """
    import ast

    tree = ast.parse((JDIR / "store.py").read_text(encoding="utf-8"))
    literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "localhost" in n.value
    }
    # 只允許出現在「說明不要這樣做」的 docstring；程式碼裡不得有
    code_literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "\n" not in n.value
        and "localhost" in n.value
    }
    assert not code_literals, code_literals
    # docstring 裡提到是允許的（記錄為什麼不做）
    assert literals or True


def test_store_does_not_import_asyncpg_at_module_level():
    """`asyncpg` 只在 `run()` 裡 import。

    這是讓 `to_row()` 可被測的前提 —— 若 module level import asyncpg，
    沒有該套件的環境就連 row 都組不出來。
    """
    import ast

    tree = ast.parse((JDIR / "store.py").read_text(encoding="utf-8"))
    top_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            top_level.append(node.module or "")
    assert "asyncpg" not in top_level, top_level
    # 且 asyncpg 只出現在 run() 內部
    inner = [n for n in ast.walk(tree) if isinstance(n, ast.Import) and any(
        a.name == "asyncpg" for a in n.names)]
    assert len(inner) == 1


def test_store_has_no_schema_migration_tooling():
    import ast

    tree = ast.parse((JDIR / "store.py").read_text(encoding="utf-8"))
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not (called & {"migrate", "alembic", "run_migrations"})


def test_store_does_not_infer_fields():
    """不得從 JFULL 或 JID 推論任何欄位。"""
    import ast

    src = (JDIR / "store.py").read_text("utf-8")
    code = src.split('"""')[-1]
    for forbidden in ("infer", "court", "extract_court", "parse_jid"):
        assert forbidden not in code, forbidden
