"""Judgement QA slice (S0-S3): store, live wiring, retrieval body shape.

Hermetic: seed docs are synthetic JX fixtures in tmp dirs (never the real
seed file); Qdrant/ollama are faked at the gateway boundary. Live behaviour
against the real seed index is proven by agent/scripts/eval_judgements_slice.py.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import judgement_store as JSTORE  # noqa: E402
from app import retrieve as RET  # noqa: E402
from app import b1_serve as B1  # noqa: E402


def _seed_doc(path: Path):
    doc = {"entry_path": "202607/X/JX,115,X,1,20260701,1.json",
           "jid": "JX,115,X,1,20260701,1", "jyear": "115",
           "jdate": "20260701", "jcase": "X",
           "jfull": "原告依民法第184條請求，為無理由，應予駁回。",
           "chunks": []}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_store_loads_seed_dir(monkeypatch, tmp_path):
    _seed_doc(tmp_path / "a.json")
    monkeypatch.setattr(JSTORE, "_SEED_DIRS", [tmp_path])
    monkeypatch.setattr(JSTORE, "_CACHE", (0.0, {}))
    m = JSTORE.jfull_map()
    assert m == {"202607/X/JX,115,X,1,20260701,1.json":
                 "原告依民法第184條請求，為無理由，應予駁回。"}
    meta = JSTORE.doc_meta("202607/X/JX,115,X,1,20260701,1.json")
    assert meta["jid"] == "JX,115,X,1,20260701,1"
    assert JSTORE.doc_meta("missing.json") is None


def test_store_missing_dir_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(JSTORE, "_SEED_DIRS", [tmp_path / "nope"])
    monkeypatch.setattr(JSTORE, "_CACHE", (0.0, {}))
    assert JSTORE.jfull_map() == {}


def test_store_bad_file_skipped(monkeypatch, tmp_path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(JSTORE, "_SEED_DIRS", [tmp_path])
    monkeypatch.setattr(JSTORE, "_CACHE", (0.0, {}))
    assert JSTORE.jfull_map() == {}


def test_serve_live_uses_store(monkeypatch):
    from app import gateway as GW
    jfull = "原告依民法第184條請求，為無理由，應予駁回。"

    async def fake_embed(texts):
        return [[0.1] * 1024 for _ in texts]

    async def fake_search(question, vector, limit=50, **kw):
        return [{"id": "x" * 64,
                 "payload": {"entry_path": "e.json", "jid": "J",
                             "chunk_index": 0, "start_offset": 0,
                             "end_offset": len(jfull), "content_hash": __import__(
                                 "hashlib").sha256(jfull.encode()).hexdigest(),
                             "jyear": "115", "jdate": "20260701", "jcase": "X"},
                 "score": 0.9}]

    monkeypatch.setattr(GW, "embed", fake_embed)
    monkeypatch.setattr(RET, "search_judgments", fake_search)
    monkeypatch.setattr(JSTORE, "jfull_map",
                        lambda: {"e.json": jfull})
    r = asyncio.run(B1.serve_live("民法第184條請求准了嗎？"))
    assert r["status"] == "grounded", r.get("abstention")
    assert "民法第184條" in r["answer"]


def test_serve_live_empty_store_abstains(monkeypatch):
    from app import gateway as GW

    async def fake_embed(texts):
        return [[0.1] * 1024 for _ in texts]

    async def fake_search(question, vector, limit=50, **kw):
        return [{"id": "x" * 64,
                 "payload": {"entry_path": "e.json", "jid": "J",
                             "chunk_index": 0, "start_offset": 0,
                             "end_offset": 4, "content_hash": "0" * 64,
                             "jyear": "115", "jdate": "20260701", "jcase": "X"},
                 "score": 0.9}]

    monkeypatch.setattr(GW, "embed", fake_embed)
    monkeypatch.setattr(RET, "search_judgments", fake_search)
    monkeypatch.setattr(JSTORE, "jfull_map", lambda: {})
    r = asyncio.run(B1.serve_live("民法第184條請求准了嗎？"))
    assert r["status"] == "insufficient_evidence"
    assert r["abstention"]["reason"] == "no_quotable_evidence"


def test_search_judgments_always_raw_vector_form(monkeypatch):
    # Qdrant /points/query rejects {"name","vector"} (400); the raw
    # {"query": vector, "using": "dense"} form works on named collections
    # (verified live 2026-10-09). _HAS_NAMED (laws-driven) must not flip it.
    seen = {}

    async def fake_req(kind, candidates, method, path, **kw):
        seen.update(kw.get("json", {}))

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"result": {"points": []}}
        return R()

    from app import gateway as GW
    monkeypatch.setattr(GW, "_req", fake_req)
    monkeypatch.setattr(RET, "_HAS_NAMED", True)
    asyncio.run(RET.search_judgments("民法第184條", [0.1] * 8, limit=5))
    assert isinstance(seen.get("query"), list), seen.get("query")
    assert seen.get("using") == "dense"
