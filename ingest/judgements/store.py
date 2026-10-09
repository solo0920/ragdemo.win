#!/usr/bin/env python3
"""判決 metadata 落 PG 的**冪等 upsert**（spec 004 T016 / FR-014, FR-015, FR-024）。

## 這個檔案的結構：純邏輯與 I/O 分離

```
document.JudgmentDocument
  ↓  to_row()          ← 純函式，可測
  (tuple, 19 values)
  ↓  run()             ← 需要 asyncpg，測試不碰
  PostgreSQL
```

分界線在 `to_row()`。它把 domain 物件轉成 DDL 裡那個欄位順序的 tuple，
**不做任何欄位加工**：每個值都與來源逐 byte 相同。

這個分離不是潔癖，是必要的：`tests/test_judgement_pg_schema.py` 依 repo 慣例
**不連 PostgreSQL**（law 那邊的 `test_three_layer_consistency.py` 也是這樣）。
若欄位對應邏輯埋在一個必須 import asyncpg、必須能連線的模組裡，那個邏輯就
永遠無法被測到 —— 而「19 個欄位有沒有對錯」正是最需要測的部分。

## 冪等語意

`ON CONFLICT (jid) DO UPDATE ... WHERE jfull_sha256 <> EXCLUDED.jfull_sha256`

那個 `WHERE` 是關鍵：對**未變**的資料重跑會 **0 insert / 0 update**（T016
acceptance 的明確要求）。少了它，每次重跑都會更新 108,409 列的
`updated_at`，而那讓「我到底改了什麼」這個問題變得無法回答。

## 刪除語意 —— 刻意未實作

spec 002 的「never delete」不適用於判決（一份判決可能被撤回）。但 delete
policy 是一個 **maintainer decision**（plan.md §Remaining blockers #3）。

這個檔案因此**沒有**任何 `DELETE`。加進去的話，未來的維護者會誤以為那是
有意設計的政策而不是遺漏 —— 而它確實是遺漏，遺漏的是一個決定。

`ingest/laws` 那邊的教訓在 `tests/test_three_layer_consistency.py`：只 upsert
從不刪除，會讓上游已消失的條文永遠留在庫裡。判決同樣面臨這個問題，但解法
是 delete policy，不是偷偷在 upsert 裡加刪除。

## 不推論任何欄位

沒有 `court`、沒有 `case_type`、沒有從 `JFULL` line 0 推論的東西。
理由寫在 `pg_schema.sql` ②：spec §Court 記載 source **沒有** court 欄位，
而 7 個例外中有一筆 line 0 是案件說明而非法院名稱。存推論值會讓下游以為
那是權威事實（FR-016）。

用法：
    .venv/bin/python ingest/judgements/store.py  < rows.jsonl > /dev/null
    （DSN 由 ingest/laws/_hostenv.py 提供，不在這裡設環境變數）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from document import JudgmentDocument  # noqa: E402

DDL = (Path(__file__).resolve().parent / "pg_schema.sql").read_text(encoding="utf-8")


def _host_dsn() -> str:
    """主機端 ingest 用的 postgres DSN —— 沿用 laws 那條已驗證的路徑。

    刻意**不**自行讀環境變數，理由見 `run()` 的 docstring：那會讓這個檔案被
    `scripts/env-audit.py` 登記成新的 DSN 讀取處，而 `.env.example` 記著舊的
    數字。

    `ingest/laws/_hostenv.py` 處理的是 2026-10-02 實測發現的兩個真問題：
    容器路徑的主機名（`postgres`）在主機上解析不到，以及 `localhost` fallback
    會靜默連不上但看起來像「跑了沒資料」。重寫一套只會把那兩個問題帶回來。

    ## 為什麼這段說明裡不能出現 `os.environ` 的實際寫法

    `env-audit.py` 的 `PY_REFS` 是 regex，它掃的是**檔案文字**而非 AST ——
    所以連 docstring 裡「不要這樣寫」的範例都被算成真的讀取處。這一版的說明
    因此用中文描述該函式而**不貼範例碼**：貼了會讓 audit 多算 1–2 個讀取處，
    而那正是本函式要避免的事。

    同一個陷阱在 `tests/test_judgement_store.py` 也有對應版本（用 AST 而非
    字串搜尋）。
    """
    sys.path.insert(0, str(ROOT / "ingest" / "laws"))
    import _hostenv

    return _hostenv.host_postgres_dsn()

# INSERT 的欄位順序 —— 必須與 `to_row()` 的 tuple 順序、`pg_schema.sql` 的
# 欄位定義三者一致。三者不一致時 upsert 會把值寫進錯誤的欄位，而那**不會**
# 報錯（型別都對，只是位置錯了）。`tests/test_judgement_pg_schema.py` 靜態
# 斷言這個順序。
COLUMNS: tuple[str, ...] = (
    "jid",
    "jyear",
    "jcase",
    "jno",
    "jdate",
    "jtitle",
    "jfull",
    "jpdf",
    "entry_path",
    "entry_sha256",
    "artifact_sha256",
    "jfull_sha256",
    "jfull_char_len",
    "jfull_byte_len",
    "crlf_count",
)

# 沒有 `court`、沒有 `case_type`、沒有 `branches`。理由見 pg_schema.sql ②。
FORBIDDEN_COLUMNS: tuple[str, ...] = ("court", "case_type", "branch", "statute", "law")

_BUILD_SQL = f"""
INSERT INTO judgment ({",".join(COLUMNS)})
VALUES ({",".join(f"${i}" for i in range(1, len(COLUMNS) + 1))})
ON CONFLICT (jid) DO UPDATE SET
  jyear=EXCLUDED.jyear, jcase=EXCLUDED.jcase, jno=EXCLUDED.jno,
  jdate=EXCLUDED.jdate, jtitle=EXCLUDED.jtitle, jfull=EXCLUDED.jfull,
  jpdf=EXCLUDED.jpdf, entry_path=EXCLUDED.entry_path,
  entry_sha256=EXCLUDED.entry_sha256, artifact_sha256=EXCLUDED.artifact_sha256,
  jfull_sha256=EXCLUDED.jfull_sha256,
  jfull_char_len=EXCLUDED.jfull_char_len, jfull_byte_len=EXCLUDED.jfull_byte_len,
  crlf_count=EXCLUDED.crlf_count,
  updated_at=now()
WHERE judgment.jfull_sha256 <> EXCLUDED.jfull_sha256
"""


def to_row(doc: JudgmentDocument, *, artifact_sha256: str = "") -> tuple:
    """`JudgmentDocument` → 符合 `COLUMNS` 順序的 tuple。

    純函式：**不碰資料庫、不寫檔、不修改 doc**。

    ## 每個欄位都直接來自 doc

    沒有 trim、沒有 case 轉換、沒有日期格式化。`jfull_char_len` /
    `jfull_byte_len` / `crlf_count` 是**量測值**（數字），不是內容的改寫 ——
    它們記錄「這份原文有多長、怎麼斷行」，讓下游不必重新計算。

    `artifact_sha256` 是額外參數而非 doc 的欄位：一份 document 屬於哪個
    artifact 是**匯入批次**的資訊，不是文件本身的屬性。
    """
    from text import LosslessText

    t = LosslessText.from_string(doc.jfull)
    return (
        doc.jid,                     # jid
        doc.jyear,                   # jyear
        doc.jcase,                   # jcase
        doc.jno,                     # jno
        doc.jdate,                   # jdate
        doc.jtitle,                  # jtitle
        doc.jfull,                   # jfull — 原樣，無加工
        doc.jpdf,                    # jpdf — 可能是空字串，那是合法的
        doc.entry_path,              # entry_path
        doc.sha256,                  # entry_sha256
        artifact_sha256,             # artifact_sha256
        t.sha256,                    # jfull_sha256
        t.char_len,                  # jfull_char_len
        t.byte_len,                  # jfull_byte_len
        t.crlf_count,                # crlf_count
    )


def build_sql() -> str:
    """回傳 upsert SQL（給測試與除錯用；不執行）。"""
    return _BUILD_SQL


async def run(rows: list[tuple], *, dsn: str | None = None) -> int:
    """把 rows upsert 進 judgment 表。

    **需要 asyncpg 與一個可連線的 PostgreSQL** —— `tests/` 裡沒有任何測試
    呼叫這個函式（repo 慣例：資料庫測試只做靜態 DDL 斷言）。

    回傳實際寫入的列數（含 insert 與 update）。

    ## DSN 從哪裡來：`_hostenv`，不是自行讀環境變數

    直接在本檔案讀 `POSTGRES_DSN` 會讓 `scripts/env-audit.py` 把它算成新的
    DSN 讀取處，而 `.env.example` 裡寫著舊的數字（「4 處」）—— 於是
    `tests/test_env_audit.py` 的 `tracked == body` 斷言紅燈。

    那一個數字不是裝飾：它是 `.env.example` 產生器的輸出，而那份檔案會被
    pre-push hook 拿來擋 push。一個程式碼讀了變數卻沒被登記，登記的數字就不
    再可信 —— 而那正是那個 hook 存在的理由。

    `ingest/laws/pg_load.py` 在 2026-10-02 已經處理過同一個問題：它用
    `_hostenv.host_postgres_dsn()`，那個函式會從 `.env` 載入並把主機名從容器路徑
    改成主機路徑。判決這邊沿用同一條路徑，而不是發明第二套 DSN 解析。

    註：這個 docstring 刻意**不**貼出 `os.environ` 的實際寫法當反面範例 ——
    `env-audit.py` 用 regex 掃檔案文字，連 docstring 的範例都會被算成讀取處。
    同一個陷阱也讓 `tests/test_judgement_store.py` 必須用 AST 而非字串搜尋。
    """
    import asyncpg

    if dsn is None:
        dsn = _host_dsn()

    con = await asyncpg.connect(dsn)
    try:
        await con.execute(DDL)
        # `execute_many` 讓 asyncpg 逐筆送出並回報每筆的影響列數；累加即為
        # 「insert + update 的總數」。`WHERE jfull_sha256 <> ...` 讓未變的列
        # 回報 0 —— 那正是 acceptance 要求的「重跑 0 insert / 0 update」。
        written = 0
        CHUNK = 2000   # 分批，避免一次送 108,409 筆撐爆記憶體
        for i in range(0, len(rows), CHUNK):
            result = await con.executemany(_BUILD_SQL, rows[i : i + CHUNK])
            written += sum(result)
        return written
    finally:
        await con.close()


def main() -> None:
    """CLI 入口 —— 需要一組 document rows。

    刻意**不**包含「從 archive 抽取 → 建立 document」的流程：那是 T005/T009
    的事，而 T007（selection scope）還是 BLOCKED —— 一個未決定的 corpus
    範圍不該被硬寫在 loader 裡。呼叫端決定要送哪些 rows 進來。
    """
    import json

    rows: list[tuple] = []
    from document import from_document
    from schema import parse_and_validate

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        entry_path = payload.pop("entry_path", "")
        artifact_sha = payload.pop("artifact_sha256", "")
        result = parse_and_validate(payload)
        if not result.valid:
            print(f"跳過（schema drift）：{result.reason}", file=sys.stderr)
            continue
        doc = from_document(payload, entry_path=entry_path, sha256="")
        rows.append(to_row(doc, artifact_sha256=artifact_sha))

    if not rows:
        print("沒有可寫入的 rows（stdin 為空或全部 drift）", file=sys.stderr)
        return

    import asyncio

    written = asyncio.run(run(rows))
    print(f"寫入 {written} 列（共 {len(rows)} 筆 input）")


if __name__ == "__main__":
    main()
