#!/usr/bin/env python3
"""法規 metadata 全量落 PG（冪等 upsert）。

輸入：data/laws/laws_meta.jsonl、laws_flat.jsonl、data/laws/ChLaw.json（sha256 審計）
輸出：ragdemo 庫的 law / article / law_import 三表（見 pg_schema.sql）
用法：POSTGRES_DSN=postgresql://rag:changeme@localhost:5432/ragdemo python3 pg_load.py
依賴：asyncpg（uv 為主——根 pyproject.toml＋`uv sync`；如要鏡像用 `uv pip install -i https://pypi.tuna.tsinghua.edu.cn/simple asyncpg`）
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "laws"
DSN = os.getenv("POSTGRES_DSN", "postgresql://rag:changeme@localhost:5432/ragdemo")
DDL = (Path(__file__).resolve().parent / "pg_schema.sql").read_text(encoding="utf-8")
CHUNK = 2000


def sha256f(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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
                m["pcode"], m["law_name"], m["law_level"], m["law_category"],
                m["law_modified_date"], m["law_effective_date"], m["law_effective_note"],
                m["is_abandoned"], m["law_abandon_note"], m["has_eng"], m["eng_name"],
                m["attach_count"], m["law_foreword"], m["law_histories"], m["article_count"],
            )
            for m in meta
        ]
        LAW_SQL = """
            INSERT INTO law (pcode, law_name, law_level, law_category,
                             law_modified_date, law_effective_date, law_effective_note,
                             is_abandoned, law_abandon_note, has_eng, eng_name,
                             attach_count, foreword, histories, article_count)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (pcode) DO UPDATE SET
              law_name=EXCLUDED.law_name, law_level=EXCLUDED.law_level,
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
            with open(DATA / "ChLaw.json", encoding="utf-8-sig") as f:
                update_date = json.load(f).get("UpdateDate", "")
            imp = await con.fetchrow(
                "INSERT INTO law_import (source_url, update_date, source_sha256, laws_count, articles_count) "
                "VALUES ('https://law.moj.gov.tw/api/ch/law/json', $1, $2, $3, $4) RETURNING id",
                update_date, sha256f(DATA / "ChLaw.json"), len(meta), len(flat),
            )

            for i in range(0, len(law_rows), CHUNK):
                await con.executemany(LAW_SQL, law_rows[i:i + CHUNK])
            for i in range(0, len(art_rows), CHUNK):
                await con.executemany(ART_SQL, art_rows[i:i + CHUNK])

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


if __name__ == "__main__":
    asyncio.run(run())