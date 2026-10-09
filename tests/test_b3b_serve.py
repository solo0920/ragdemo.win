"""B3-B serving integration: temporal limitations in answers, invalid excluded."""
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
from app import b3b_temporal as T  # noqa: E402
from b2d_helpers import _node, _stat, _cit, _graph  # noqa: E402


def test_current_only_appends_limitation_and_passes():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
             _node("reasoning:JX#C1:6-12", "REASONING", "查原告未受損害。")]
    evs = [_stat()]
    r = A.build_answer("q", judgement_id="JX", graph=_graph(nodes),
                       citations=[_cit()], statute_evidences=evs,
                       temporal={"st:P#1": "CURRENT_ONLY"})
    assert r["status"] == "ANSWERED"
    assert T.LIMIT_CURRENT in (r["answer"] or "")
    assert T.LIMIT_VERIFIED not in (r["answer"] or "")


def test_verified_wording_only_with_verified_status():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
             _node("reasoning:JX#C1:6-12", "REASONING", "查原告未受損害。")]
    evs = [_stat()]
    r = A.build_answer("q", judgement_id="JX", graph=_graph(nodes),
                       citations=[_cit()], statute_evidences=evs,
                       temporal={"st:P#1": "HISTORICAL_VERIFIED"})
    assert r["status"] == "ANSWERED"
    assert T.LIMIT_VERIFIED in (r["answer"] or "")
    assert T.LIMIT_CURRENT not in (r["answer"] or "")


def test_invalid_version_excluded_with_note():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
             _node("reasoning:JX#C1:6-12", "REASONING", "查原告未受損害。")]
    evs = [_stat()]
    r = A.build_answer("q", judgement_id="JX", graph=_graph(nodes),
                       citations=[_cit()], statute_evidences=evs,
                       temporal={"st:P#1": "TEMPORALLY_INVALID"})
    assert r["status"] == "ANSWERED"
    assert "法條本文" not in (r["answer"] or "")  # exact text withheld
    assert T.LIMIT_EXCLUDED in (r["answer"] or "")
    assert r["statutes"] == [] and r["citations"] == []


def test_no_map_preserves_legacy_output():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
             _node("reasoning:JX#C1:6-12", "REASONING", "查原告未受損害。")]
    evs = [_stat()]
    r = A.build_answer("q", judgement_id="JX", graph=_graph(nodes),
                       citations=[_cit()], statute_evidences=evs)
    assert r["status"] == "ANSWERED"
    for lim in T.TEMPORAL_LIMITATIONS:
        assert lim not in (r["answer"] or "")


def test_serve_enforces_temporal_with_payload_jdate():
    import hashlib
    text = "主文原告之訴駁回。查本案依民法第184條無理由。"
    h = hashlib.sha256(text.encode()).hexdigest()
    hits = [{"id": "p0",
             "payload": {"entry_path": "e.json", "jid": "JX", "chunk_index": 0,
                         "start_offset": 0, "end_offset": len(text),
                         "content_hash": h, "jyear": "115", "jdate": "20260713",
                         "jcase": "x"}, "score": 0.9}]

    async def _search(q, v, lim):
        return hits

    async def _vec(texts):
        return [[0.0]]

    roles = {"p0": {"chunk_id": "JX#C1", "document_id": "JX",
                    "structural_role": "FACTS", "structural_labels": ["FACTS"],
                    "text": text, "span": {}}}
    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={"e.json": text}, chunk_roles=roles,
        enforce_temporal=True))
    assert r["status"] == "ANSWERED", (r.get("abstention") or {})
    assert T.LIMIT_CURRENT in (r["answer"] or "")
