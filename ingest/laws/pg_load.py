#!/usr/bin/env python3
"""法規 metadata 全量落 PG（冪等 upsert）。

輸入：data/laws/laws_meta.jsonl、laws_flat.jsonl、data/laws/ChLaw.json（sha256 審計）
輸出：ragdemo 庫的 law / article / law_import 三表（見 pg_schema.sql）
用法：POSTGRES_DSN=postgresql://rag:<密碼>@localhost:5432/ragdemo python3 pg_load.py
依賴：asyncpg（uv 為主——根 pyproject.toml＋`uv sync`；如要鏡像用 `uv pip install -i https://pypi.tuna.tsinghua.edu.cn/simple asyncpg`）
"""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.common.jsonl import load_jsonl  # noqa: E402  # qdrant_load.py 共用同一份
import _hostenv  # noqa: E402  # 主機端環境變數（載入 .env＋改寫容器主機名）

DATA = ROOT / "data" / "laws"
# ⚠️⚠️ 2026-10-02 換掉 `os.getenv("POSTGRES_DSN") or "…@localhost:5432"`。
# 那個 fallback **在三台上都是壞的**（實測）：
#   · cron 在主機上跑，沒有 source .env → getenv 回 None → localhost
#   · postgres 只綁 `${TS_IP}:5432`（compose 唯一真相來源）→ ConnectionRefused
#   · 就算 source .env，DSN 裡的主機名是 `postgres`（容器服務名）→ 主機上 gaierror
# 症狀是「沒有錯誤輸出，只是法規沒更新」。x570 先撞到；wsl 被上游 500 擋在前面，
# 還沒暴露。細節與修法見 ingest/laws/_hostenv.py。
DSN = _hostenv.host_postgres_dsn()
DDL = (Path(__file__).resolve().parent / "pg_schema.sql").read_text(encoding="utf-8")
CHUNK = 2000


def sha256f(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


async def run() -> None:
    meta = load_jsonl(DATA / "laws_meta.jsonl")
    flat = load_jsonl(DATA / "laws_flat.jsonl")
    assert meta and flat, "缺少 laws_meta.jsonl / laws_flat.jsonl"

    con = await asyncpg.connect(DSN)
    try:
        await con.execute(DDL)

        law_rows = [
            (
                m["pcode"], m["law_name"], m["law_level"], m.get("source_api", ""),
                m.get("law_url", ""), m["law_category"],
                m["law_modified_date"], m["law_effective_date"], m["law_effective_note"],
                m["is_abandoned"], m["law_abandon_note"], m["has_eng"], m["eng_name"],
                m["attach_count"], m["law_foreword"], m["law_histories"], m["article_count"],
            )
            for m in meta
        ]
        LAW_SQL = """
            INSERT INTO law (pcode, law_name, law_level, source_api, law_url, law_category,
                             law_modified_date, law_effective_date, law_effective_note,
                             is_abandoned, law_abandon_note, has_eng, eng_name,
                             attach_count, foreword, histories, article_count)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
            ON CONFLICT (pcode) DO UPDATE SET
              law_name=EXCLUDED.law_name, law_level=EXCLUDED.law_level,
              source_api=EXCLUDED.source_api, law_url=EXCLUDED.law_url,
              law_category=EXCLUDED.law_category, law_modified_date=EXCLUDED.law_modified_date,
              law_effective_date=EXCLUDED.law_effective_date, law_effective_note=EXCLUDED.law_effective_note,
              is_abandoned=EXCLUDED.is_abandoned, law_abandon_note=EXCLUDED.law_abandon_note,
              has_eng=EXCLUDED.has_eng, eng_name=EXCLUDED.eng_name, attach_count=EXCLUDED.attach_count,
              foreword=EXCLUDED.foreword, histories=EXCLUDED.histories,
              article_count=EXCLUDED.article_count, updated_at=now()
        """
        art_rows = [
            (
                a["pcode"], a["article_seq"], a["article_no"], a["chapter"], a["article_content"],
                a["char_len"], a["is_repealed"], a["is_abandoned"], content_hash(a["article_content"]),
            )
            for a in flat
        ]
        ART_SQL = """
            INSERT INTO article (pcode, article_seq, article_no, chapter, content,
                                 char_len, is_repealed, is_abandoned, content_hash)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (pcode, article_seq) DO UPDATE SET
              article_no=EXCLUDED.article_no, chapter=EXCLUDED.chapter, content=EXCLUDED.content,
              char_len=EXCLUDED.char_len, is_repealed=EXCLUDED.is_repealed,
              is_abandoned=EXCLUDED.is_abandoned, content_hash=EXCLUDED.content_hash,
              updated_at=now()
        """

        async with con.transaction():
            # ⚠ **兩個 API 都要記錄**（2026-10-10）。原本只讀 ChLaw.json，
            # 結果 law_import 看起來像「已涵蓋全部法規」，實際上整個命令層缺席。
            # 記 sources 是為了讓日後看紀錄的人不會重蹈那個誤解。
            dates, shas = [], []
            for _tag, fn in (("law", "ChLaw.json"), ("order", "ChOrder.json")):
                p = DATA / fn
                if p.is_file():
                    with open(p, encoding="utf-8-sig") as f:
                        dates.append(json.load(f).get("UpdateDate", ""))
                    shas.append(sha256f(p))
            update_date = " | ".join(d for d in dates if d)
            sources = ",".join(t for t, fn in (("law", "ChLaw.json"), ("order", "ChOrder.json"))
                               if (DATA / fn).is_file())
            src_urls = ",".join(
                f"https://law.moj.gov.tw/api/ch/{t}/json"
                for t in ("law", "order") if (DATA / f"Ch{t.capitalize()}.json").is_file()
            )
            imp = await con.fetchrow(
                "INSERT INTO law_import (source_url, sources, update_date, source_sha256, "
                "laws_count, articles_count) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                src_urls, sources, update_date, ";".join(shas), len(meta), len(flat),
            )

            for i in range(0, len(law_rows), CHUNK):
                await con.executemany(LAW_SQL, law_rows[i:i + CHUNK])
            for i in range(0, len(art_rows), CHUNK):
                await con.executemany(ART_SQL, art_rows[i:i + CHUNK])

            # ⚠️ 刪除本次來源已不存在的條文（2026-10-05 補）。
            #
            # 實測殘留：pg 有 47,289 筆、parquet 只有 47,284 條 —— 5 筆多出來的
            # 是 2026-09-24 的舊版本，updated_at 全是那一天。
            #
            # 為什麼會殘留：`article_seq` 是**位置序號**（列舉 LawArticles 的次序），
            # 不是條號。司法院刪掉一條中間的條文時，後面所有條的 seq 往前挪一位
            # → ON CONFLICT (pcode, article_seq) DO UPDATE 更新的是「別條」，
            # 被刪的那個 (pcode, seq) 就永遠留在表裡。
            #   實例：醫療法第101條在上游是 seq=118（10-03 版），pg 裡 seq=117
            #   還留著舊的第101條（09-24 版）。
            #
            # 為什麼必須刪：三層一致是最高標準。留著舊版條文會讓 pg 查到
            # **已不存在的條文內容**，而且沒有任何徵兆 —— 比少一條更危險。
            #
            # 只刪「本次匯入範圍內、但本次沒出現」的，且整個動作在同一個
            # transaction 裡（與 upsert 同一個 con.transaction()），失敗會整批 rollback。
            # 用 (pcode, article_seq) 比對而非條號：條號會重複於不同章／不同法。
            n_del_art = await _delete_stale_articles(con, flat)
            n_del_law = await _delete_stale_laws(con, meta)
            if n_del_art or n_del_law:
                print(f"清除殘留：條文 {n_del_art} 筆、法規 {n_del_law} 部"
                      f"（上游已刪除但仍在表中的舊資料）")

            await con.execute("UPDATE law_import SET finished_at=now() WHERE id=$1", imp["id"])

        n_law, n_art, n_rep, n_aban = await con.fetchrow(
            "SELECT (SELECT count(*) FROM law), (SELECT count(*) FROM article),"
            "       (SELECT count(*) FROM article WHERE is_repealed),"
            "       (SELECT count(*) FROM article WHERE is_abandoned)"
        )
        print(f"law={n_law} article={n_art} is_repealed={n_rep} is_abandoned={n_aban} "
              f"law_import#{imp['id']} finished, sha256={sha256f(DATA/'ChLaw.json')[:12]}")
    finally:
        await con.close()


async def _delete_stale_articles(con, flat: list[dict]) -> int:
    """刪除「本次來源已不存在」的條文，回傳刪除筆數。

    ⚠️ 用 `unnest` 配兩個平行陣列，而不是 `(pcode, seq) = ANY(ARRAY[...record])`：
       PostgreSQL **不支援匿名 composite type 的輸入**（實測：
       `UnsupportedClientFeatureError: input of anonymous composite types is
       not supported`），宣告具名 composite type 又是一整個不必要的型別。
       兩個平行陣列 + unnest 是等價且 asyncpg 能處理的形狀。

    ⚠️ 一次刪完成，不是逐筆比對：47,284 筆逐筆 SELECT 是 47,284 次往返。

    ⚠️ 為什麼 key 是 (pcode, article_seq) 而非條號：條號在不同法規／不同章
       會重複（第1條到處都是），用它會誤刪別的條文。而 (pcode, seq) 正是
       ON CONFLICT 使用的唯一鍵，語意一致。
    """
    pcodes = [a["pcode"] for a in flat]
    seqs = [a["article_seq"] for a in flat]
    rows = await con.fetch(
        "DELETE FROM article a WHERE NOT EXISTS ("
        "  SELECT 1 FROM unnest($1::text[], $2::int[]) AS k(p, s)"
        "  WHERE k.p = a.pcode AND k.s = a.article_seq"
        ") RETURNING pcode",
        pcodes, seqs)
    return len(rows)


async def _delete_stale_laws(con, meta: list[dict]) -> int:
    """刪除「本次來源已不存在」的整部法規（法規被撤銷時會發生）。"""
    pcodes = [m["pcode"] for m in meta]
    rows = await con.fetch(
        "DELETE FROM law WHERE pcode <> ALL($1::text[]) RETURNING pcode", pcodes)
    return len(rows)


if __name__ == "__main__":
    asyncio.run(run())