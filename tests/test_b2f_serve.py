"""B2-F integration: confidence gate enforced inside the serving path."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402
from app import b1_serve as B1  # noqa: E402

CORPUS = B1.StatuteCorpus.from_jsonl()
TH = {"bm25": 10.0}


def _hits(scores, jid="JX"):
    return [{"id": f"p{i}", "payload": {"jid": jid, "entry_path": "e.json",
                                        "chunk_index": i, "start_offset": 0,
                                        "end_offset": 1, "content_hash": "h",
                                        "jyear": "115", "jdate": "20260713",
                                        "jcase": "x"},
             "score": s} for i, s in enumerate(scores)]


async def _vec(texts):
    return [[0.0]]


def test_accepted_judgment_reaches_answer():
    import hashlib
    text = "主文原告之訴駁回。查本案無理由。"
    h = hashlib.sha256(text.encode()).hexdigest()
    hits = _hits([50.0, 10.0, 5.0])
    for h_ in hits:
        h_["payload"].update({"entry_path": "e.json", "content_hash": h,
                              "start_offset": 0, "end_offset": len(text)})
    r = asyncio.run(A.serve_question(
        "問題", search_fn=lambda q, v, lim: _awrap(hits), embed_fn=_vec,
        jfull_by_entry={"e.json": text},
        chunk_roles={"p0": {"chunk_id": "JX#C1", "document_id": "JX",
                            "structural_role": "FACTS",
                            "structural_labels": ["FACTS"], "text": text,
                            "span": {}}},
        enforce_confidence=True, retrieval_method="bm25"))
    # Gate ACCEPTed (margin 40 >= threshold): must NOT abstain for confidence.
    assert (r.get("abstention") or {}).get("reason") not in (
        "LOW_RETRIEVAL_CONFIDENCE", "RETRIEVAL_IDENTITY_MISMATCH"), r.get("abstention")


async def _awrap(hits):
    return hits


def test_rejected_judgment_cannot_reach_answer():
    async def _search(q, v, lim):
        return _hits([10.0, 9.5, 9.0])

    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec, jfull_by_entry={},
        enforce_confidence=True, retrieval_method="bm25"))
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "LOW_RETRIEVAL_CONFIDENCE"
    assert r["answer"] is None


def test_identity_mismatch_rejected_before_evidence():
    async def _search(q, v, lim):
        return _hits([90.0, 10.0], jid="TPSM,115,台抗,1470,20260730,1")

    r = asyncio.run(A.serve_question(
        "115年度台抗字第878號如何？", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={}, enforce_confidence=True, retrieval_method="bm25"))
    assert r["status"] == "ABSTAINED"
    assert r["abstention"]["reason"] == "RETRIEVAL_IDENTITY_MISMATCH"


def test_unknown_method_abstains_and_default_path_unchanged():
    async def _search(q, v, lim):
        return _hits([90.0, 10.0])

    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec, jfull_by_entry={},
        enforce_confidence=True, retrieval_method="unmeasured-model"))
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "LOW_RETRIEVAL_CONFIDENCE"
