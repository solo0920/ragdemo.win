"""三層內容一致的**可執行**稽核（2026-10-05）。

## 為什麼要有這個檔案

2026-10-05 手工稽核發現兩個三層不一致的缺口，都**沒有任何徵兆**：

1. **Qdrant 少了 1,031 條廢止條文** —— ingest 與檢索兩處刻意排除。
   「少一條」看起來像資料庫比較小，不像故障。
2. **PostgreSQL 多了 5 條已不存在的條文** —— `pg_load` 只 upsert 從不刪除，
   而 `article_seq` 是位置序號（不是條號）。司法院刪掉一條中間的條文時，
   後面所有條的 seq 往前挪，被刪的那筆就永遠留在表裡。

   實例：醫療法第101條在上游是 `seq=118`（10-03 版），pg 裡 `seq=117`
   還留著 09-24 的舊版第101條 —— **內容不同**。
   這比「少一條」更危險：pg 會查到上游已經刪掉的條文內容。

這兩種都不會讓任何健康檢查變紅 —— `host-doctor` 的每一項都是綠的。
所以需要一個可重複執行的稽核。

## 為什麼不在這裡真的查三層

這支測試只驗**規則**（pg_load 必須有刪除、ingest 必須保留廢止條文），
真正的三層比對要連線到三個資料庫，跑在 tests/ 裡不合適（既有測試的
convention 是不碰外部服務）。實際比對用 ingest 流程本身：

    python3 ingest/laws/sync_daily.py --apply

執行後應由 `data/laws/sync_daily.log` 或各步驟的 stdout 反映條數一致。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingest" / "laws"))

import inspect

import qdrant_load  # noqa: E402


def test_pg_load_deletes_articles_that_vanished_upstream() -> None:
    """pg_load **必須**刪除本次來源已不存在的條文。

    ⚠️ 沒有這一段的症狀不是「少一條」，而是**多出一條舊版條文**：
    `article_seq` 是位置序號，司法院刪掉中間某條時後面全部往前挪，
    `ON CONFLICT (pcode, article_seq) DO UPDATE` 更新的是「別條」，
    被刪的那筆永遠留在表裡，而且內容是舊版。

    實測（2026-10-05）：pg 47,289 筆 vs parquet 47,284 條，多出 5 筆全是
    `updated_at = 2026-09-24` 的舊資料。
    """
    import pg_load
    src = inspect.getsource(pg_load)
    assert "_delete_stale_articles" in src, "pg_load 沒有清除殘留條文的邏輯"
    assert hasattr(pg_load, "_delete_stale_articles")

    # 刪除必須真的被呼叫 —— 定義了但沒呼叫等於沒有
    run_src = inspect.getsource(pg_load.run)
    assert "_delete_stale_articles(con" in run_src, \
        "_delete_stale_articles 定義了但沒在 run() 裡呼叫"


def test_pg_deletes_whole_laws_that_vanished() -> None:
    """整部法規被撤銷時也要能清掉（條文清了但 law 表留著是另一種不一致）。"""
    import pg_load
    assert hasattr(pg_load, "_delete_stale_laws")
    run_src = inspect.getsource(pg_load.run)
    assert "_delete_stale_laws(con" in run_src


def test_stale_delete_uses_pcode_plus_seq_not_article_no() -> None:
    """key 必須是 (pcode, article_seq)，不可用條號。

    條號在不同法規／不同章會重複（第1條到處都是），用它會誤刪別的條文。
    """
    import pg_load
    src = inspect.getsource(pg_load._delete_stale_articles)
    assert "pcode" in src and "article_seq" in src
    assert "article_no" not in src, \
        "刪除條件不可用條號 —— 條號跨法規重複，會誤刪別的條文"


def test_qdrant_ingest_keeps_repealed_articles() -> None:
    """廢止條文必須進 qdrant（三層一致是最高標準）。

    少一條看起來像「資料庫比較小」，不像故障 —— 這就是它沒被發現的原因。
    """
    src = inspect.getsource(qdrant_load.build_points)
    assert 'if a["is_repealed"]' not in src, \
        "build_points 又在排除廢止條文了 —— 三層會少一類條文"


def test_qdrant_payload_flags_are_not_constants() -> None:
    """payload 的 is_repealed / is_abandoned 必須是**真實值**。

    寫死 False 的後果是雙重的：檢索層的篩選失效（靜默），而且該層與
    parquet／pg 不一致（那兩層是真值）。
    """
    src = inspect.getsource(qdrant_load.build_points)
    assert '"is_repealed": bool(a["is_repealed"])' in src, \
        "is_repealed 必須取真實值，不可寫死"
    assert '"is_abandoned": bool(a["is_abandoned"])' in src, \
        "is_abandoned 必須取真實值，不可寫死"


def test_retrieve_layer_does_not_filter_repealed() -> None:
    """檢索層不可再過濾廢止條文。

    雙重排除（ingest + 檢索）讓「查不到已刪除條文」沒有任何徵兆。
    正確行為是**查得到並標示已廢止**。
    """
    sys.path.insert(0, str(ROOT / "backend"))
    from app import retrieve
    keys = {c.get("key") for c in retrieve._BASE_CONDITIONS if isinstance(c, dict)}
    assert "is_repealed" not in keys
    assert "is_abandoned" not in keys