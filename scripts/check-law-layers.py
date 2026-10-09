#!/usr/bin/env python3
"""三層條文稽核：parquet vs PostgreSQL vs Qdrant（spec 002 FR-007/FR-020）。

用法：
    python3 scripts/check-law-layers.py [--laws N] [--per-law M] [--layers parquet,postgres,qdrant]

每層連不上就整層標 unavailable（exit 2 區分「查不到」與「查到不一致」）；
任一條 mismatch/missing 即 exit 1；全過 exit 0。輸出指到條／層／差異。
憑證走環境變數（POSTGRES_PASSWORD、QDRANT_API_KEY），一律不印值。
執行一律用 `.venv/bin/python`（asyncpg/httpx 在 venv 裡，不在系統 python）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingest" / "laws"))

import layer_check as LC  # noqa: E402


def env(k: str) -> str:
    for line in open(ROOT / ".env", encoding="utf-8"):
        line = line.strip()
        if line.startswith(k + "="):
            return line.split("=", 1)[1]
    return os.getenv(k, "")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--laws", type=int, default=10)
    ap.add_argument("--per-law", type=int, default=5)
    ap.add_argument("--layers", default="parquet,postgres,qdrant")
    args = ap.parse_args()
    want = [l.strip() for l in args.layers.split(",") if l.strip()]

    # ── parquet（本地檔）──
    flat = []
    if "parquet" in want:
        for line in open(ROOT / "data/laws/laws_flat.jsonl", encoding="utf-8"):
            d = json.loads(line)
            flat.append((d["law_name"], d["article_no"], d["article_content"]))

    # ── postgres／qdrant（連不上就整層 unavailable，不硬查）──
    pg = q = None
    pg_ok = q_ok = False
    if "postgres" in want:
        try:
            import asyncpg
            pg = await asyncpg.connect(user="rag", password=env("POSTGRES_PASSWORD"),
                                       database="ragdemo", host="100.119.83.111",
                                       port=5432, timeout=10)
            pg_ok = True
        except Exception as e:  # noqa: BLE001
            print(f"postgres unavailable: {type(e).__name__}")
    if "qdrant" in want:
        try:
            import httpx
            q = httpx.AsyncClient(
                base_url=os.getenv("QDRANT_URL", "http://100.119.83.111:6333"),
                headers={"api-key": env("QDRANT_API_KEY")} if env("QDRANT_API_KEY") else {},
                timeout=30)
            r = await q.get("/collections/laws")
            q_ok = r.status_code == 200
            if not q_ok:
                print(f"qdrant unavailable: HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            print(f"qdrant unavailable: {type(e).__name__}")

    # ── 取樣（確定性：法名＋條號排序取前 N）──
    seen: dict[str, int] = {}
    sample = []
    for law, art, text in sorted(flat, key=lambda r: (r[0], r[1])):
        if law not in seen:
            if len(seen) >= args.laws:
                break
            seen[law] = 0
        if seen[law] < args.per_law:
            seen[law] += 1
            sample.append((law, art, text))
    print(f"sample: {len(sample)} articles across {len(seen)} laws")

    bad = 0
    for law, art, text in sample:
        layers: dict[str, str | None] = {"parquet": text if "parquet" in want else None,
                                         "postgres": None, "qdrant": None}
        if pg_ok:
            row = await pg.fetchrow(
                "SELECT a.content FROM article a JOIN law l ON l.pcode=a.pcode "
                "WHERE l.law_name=$1 AND a.article_no=$2", law, art)
            layers["postgres"] = row["content"] if row else None
        if q_ok:
            r = await q.post("/collections/laws/points/scroll",
                             json={"filter": {"must": [
                                 {"key": "law_name", "match": {"value": law}},
                                 {"key": "article_no", "match": {"value": art}}]},
                                 "limit": 5, "with_payload": ["text"]})
            pts = r.json()["result"]["points"]
            layers["qdrant"] = pts[0]["payload"].get("text") if pts else None
        res = LC.compare(law, art, layers)
        if res["verdict"] != "ok":
            bad += 1
            print(f"  {res['verdict'].upper()}: {law} {art} —— {res['detail']}")
    if pg is not None:
        await pg.close()
    print(f"done: {len(sample)-bad}/{len(sample)} ok")
    if not pg_ok or not q_ok:
        return 2
    return 1 if bad else 0


raise SystemExit(asyncio.run(main()))
