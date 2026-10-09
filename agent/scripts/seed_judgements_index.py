#!/usr/bin/env python3
"""S0: seed the `judgements` Qdrant collection from frozen reviewed input.

Source: tests/fixtures/b1_judgement_serving/case_00450.json (vendored real
judgement with full provenance: entry_path + JID/JYEAR/JCASE/JDATE + JFULL).

Pipeline (all frozen, deterministic, no LLM rewriting):
  fixture -> LosslessText -> chunk.chunk_text (source_document=entry_path)
  -> index_load.build_point (T017 9-field payload, contract-asserted per point)
  -> ollama bge-m3 embed (same model as laws, no new model)
  -> Qdrant `judgements` (named dense 1024 Cosine; point id = canonical
     64-hex T015 id truncated to 32 hex — Qdrant rejects 64-hex point IDs,
     verified live 2026-10-09; truncation is deterministic so re-runs
     overwrite the same points instead of duplicating).

Also writes data/judgements/seed/<basename>.json (entry/JFULL/chunks) for the
S1 JFULL reader. Byte-reproducible: same fixture + same code = same bytes.

JPDF-key note: the fixture document carries 7/8 canonical keys (no JPDF key),
so ingest/judgements/document.py + schema.validate() cannot ingest it (they
rightly require the full key set for ARCHIVE ingestion). This script does not
bypass that gate for archive data: it seeds from an already-reviewed fixture,
and every payload value is a real frozen value. The 9-field payload contract
(which never includes jpdf) is asserted per point.

Isolation: touches ONLY the `judgements` collection. Records the `laws`
collection count before/after and refuses to continue if it changed.
Secrets (QDRANT_API_KEY) are read from the environment in-process and never
printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))

import chunk as CH  # noqa: E402
import index_load as IL  # noqa: E402
import text as T  # noqa: E402
from app import gateway  # noqa: E402
from app import retrieve as RET  # noqa: E402

COLLECTION = "judgements"
DIM = 1024


def qdrant_headers() -> dict:
    key = os.getenv("QDRANT_API_KEY", "").strip()
    return {"api-key": key} if key else {}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default=str(
        REPO / "tests/fixtures/b1_judgement_serving/case_00450.json"))
    ap.add_argument("--qdrant", default=os.getenv(
        "QDRANT_URL", "http://localhost:6333").rstrip("/"))
    ap.add_argument("--seed-dir", default=str(REPO / "data/judgements/seed"))
    ap.add_argument("--yes", action="store_true",
                    help="actually write to Qdrant (default is dry-run plan)")
    args = ap.parse_args()

    fx = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    entry_path = fx["entry_path"]
    doc = fx["document"]
    meta = SimpleNamespace(entry_path=entry_path, jid=doc["JID"],
                           jyear=doc["JYEAR"], jdate=doc["JDATE"],
                           jcase=doc["JCASE"])

    chunks = CH.chunk_text(T.LosslessText.from_string(doc["JFULL"]),
                           source_document=entry_path, jid=doc["JID"])
    print(f"doc={doc['JID']} jfull={len(doc['JFULL'])} chunks={len(chunks)} "
          f"kinds={[c.boundary_kind.value for c in chunks]}")

    points_meta = []
    for ch in chunks:
        pt = IL.build_point(ch, meta)
        IL.assert_payload_contract(pt["payload"])
        RET.validate_judgment_payload(pt["payload"])
        assert len(pt["id"]) == 64 and all(
            x in "0123456789abcdef" for x in pt["id"]), pt["id"]
        points_meta.append((ch, pt))
    print(f"payloads: {len(points_meta)} valid T017 (9-field, canonical 64-hex ids)")

    # Deterministic Qdrant transport IDs (Qdrant rejects 64-hex point IDs).
    qids = [pid[:32] for _, pt in points_meta for pid in [pt["id"]]]
    assert len(set(qids)) == len(qids), "transport id collision"

    seed = {"entry_path": entry_path, "jid": doc["JID"],
            "jyear": doc["JYEAR"], "jdate": doc["JDATE"], "jcase": doc["JCASE"],
            "jtitle": doc.get("JTITLE", ""),
            "jfull": doc["JFULL"],
            "chunks": [{"chunk_index": ch.chunk_index,
                        "start_offset": ch.start_offset,
                        "end_offset": ch.end_offset,
                        "boundary_kind": ch.boundary_kind.value,
                        "text": ch.text, "sha256": ch.sha256,
                        "point_id": pt["id"]}
                       for ch, pt in points_meta]}
    seed_bytes = (json.dumps(seed, ensure_ascii=False, indent=1,
                             sort_keys=True) + "\n").encode("utf-8")
    print(f"seed bytes: {len(seed_bytes)} sha256={hashlib.sha256(seed_bytes).hexdigest()[:16]}")

    base = args.qdrant
    headers = qdrant_headers()
    async with httpx.AsyncClient(base_url=base, headers=headers,
                                 timeout=60) as c:
        laws_before = (await c.post(
            f"/collections/{RET.COLLECTION}/points/count",
            json={"exact": True})).json()["result"]["count"]
        r = await c.get(f"/collections/{COLLECTION}")
        print(f"collection {COLLECTION}: {r.status_code} / laws count: {laws_before}")
        if not args.yes:
            print("dry-run: pass --yes to create collection + upsert + write seed")
            return 0
        if r.status_code == 404:
            r = await c.put(f"/collections/{COLLECTION}", json={
                "vectors": {"dense": {"size": DIM, "distance": "Cosine"}}})
            r.raise_for_status()
            print("collection created (named dense 1024 Cosine, no sparse)")
        vecs = await gateway.embed([ch.text for ch, _ in points_meta])
        assert all(len(v) == DIM for v in vecs), "embed dim mismatch"
        pts = [{"id": qid, "vector": {"dense": v}, "payload": pt["payload"]}
               for qid, v, (_, pt) in zip(qids, vecs, points_meta)]
        r = await c.put(f"/collections/{COLLECTION}/points",
                        json={"points": pts}, params={"wait": "true"})
        r.raise_for_status()
        n = (await c.post(f"/collections/{COLLECTION}/points/count",
                          json={"exact": True})).json()["result"]["count"]
        laws_after = (await c.post(
            f"/collections/{RET.COLLECTION}/points/count",
            json={"exact": True})).json()["result"]["count"]
        assert laws_after == laws_before, "laws collection changed!"
        print(f"upserted={len(pts)} count={n} laws_untouched={laws_after}")

    seed_dir = Path(args.seed_dir)
    seed_dir.mkdir(parents=True, exist_ok=True)
    out = seed_dir / (Path(entry_path).name)
    out.write_bytes(seed_bytes)
    print(f"seed wrote {out}")
    return 0


if __name__ == "__main__":
    import asyncio
    raise SystemExit(asyncio.run(main()))
