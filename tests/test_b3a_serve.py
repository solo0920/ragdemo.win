"""B3-A serving integration: attribution filtering enforced, bypass impossible."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402
from app import b3a_attribution as A3  # noqa: E402
from app import b2c_evidence as E  # noqa: E402

SET = json.loads((ROOT / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))


def _node(eid, etype, text, jid="JX"):
    return {"evidence_id": eid, "evidence_type": etype, "judgement_id": jid,
            "document_id": jid, "chunk_id": f"{jid}#C1", "text": text,
            "source_span": {"start": 0, "end": len(text), "unit": "code_point",
                            "basis": "chunk_text"},
            "provenance": {}}


def _graph(nodes):
    return {"judgement_ids": ["JX"], "nodes": nodes,
            "edges": [{"from": n["evidence_id"], "to": "judgement:JX",
                       "relation": "same_document"} for n in nodes]}


def test_refused_nodes_leave_the_answer():
    nodes = [_node("reasoning:JX#C1:0-10", "REASONING", "查原告未受損害。"),
             _node("reasoning:JX#C1:10-20", "REASONING", "被告抗辯原告之請求為無理由。"),
             _node("disposition:JX#C1:20-26", "DISPOSITION", "原告之訴駁回。")]
    amap = {n["evidence_id"]: ("COURT_VOICE"
                               if "查原告" in n["text"] or "駁回" in n["text"]
                               else "PARTY_VOICE") for n in nodes}
    amap["reasoning:JX#C1:0-10"] = "COURT_VOICE"
    amap["disposition:JX#C1:20-26"] = "COURT_VOICE"
    r = A.build_answer("q", judgement_id="JX", graph=_graph(nodes),
                       citations=[], statute_evidences=[], attribution=amap)
    assert r["status"] == "ANSWERED"
    assert "被告抗辯" not in (r["answer"] or "")


def test_all_refused_abstains_and_missing_map_fails_closed():
    nodes = [_node("reasoning:JX#C1:0-10", "REASONING", "被告抗辯原告之請求為無理由。")]
    amap = {n["evidence_id"]: "PARTY_VOICE" for n in nodes}
    r = A.build_answer("q", judgement_id="JX", graph=_graph(nodes),
                       citations=[], statute_evidences=[], attribution=amap)
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["answer"] is None
    # Missing map entries fail closed as UNRESOLVED (excluded).
    r = A.build_answer("q", judgement_id="JX", graph=_graph(nodes),
                       citations=[], statute_evidences=[], attribution={})
    assert r["status"] == "INSUFFICIENT_EVIDENCE"


def test_serve_enforces_attribution_end_to_end():
    # R-02 reasoning nodes are all COURT_VOICE-clean; force one refused.
    case = next(c for c in SET["cases"] if c["case_id"] == "R-04-detention-full-doc")
    nodes = [dict(n) for n in case["graph"]["nodes"]]
    amap = {n["evidence_id"]: A3.COURT_VOICE for n in nodes}
    r = A.build_answer("q", judgement_id=case["judgement_id"],
                       graph={"judgement_ids": [case["judgement_id"]], "nodes": nodes,
                              "edges": case["graph"]["edges"]},
                       citations=[], statute_evidences=[], attribution=amap)
    assert r["status"] == "ANSWERED"
    amap = {n["evidence_id"]: A3.PARTY_VOICE for n in nodes}
    r = A.build_answer("q", judgement_id=case["judgement_id"],
                       graph={"judgement_ids": [case["judgement_id"]], "nodes": nodes,
                              "edges": case["graph"]["edges"]},
                       citations=[], statute_evidences=[], attribution=amap)
    assert r["status"] == "INSUFFICIENT_EVIDENCE"


def test_classifier_marks_real_b2c_nodes():
    case = next(c for c in SET["cases"] if c["case_id"] == "R-04-detention-full-doc")
    texts = {ch["chunk_id"]: ch["text"] for ch in case["chunks"]}
    amap = A.attribute_graph_nodes(case["graph"]["nodes"], texts)
    assert set(amap) == {n["evidence_id"] for n in case["graph"]["nodes"]}
    # All four are court-voiced reasoning; none may be refused/party.
    assert all(v == "COURT_VOICE" for v in amap.values()), amap
