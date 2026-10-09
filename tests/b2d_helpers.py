"""B2-D shared test doubles (synthetic JX-prefixed data, never real law)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402

def _node(eid, etype, text, jid="JX", chunk="JX#C1"):
    return {"evidence_id": eid, "evidence_type": etype, "judgement_id": jid,
            "document_id": jid, "chunk_id": chunk, "text": text,
            "source_span": {"start": 0, "end": len(text), "unit": "code_point",
                            "basis": "chunk_text"},
            "provenance": {}}


def _stat(jid="JX", eid="st:P#1", law="民法", art="第184條", pcode="P", seq=1):
    return {"evidence_id": eid, "evidence_type": "statute", "law_name": law,
            "article": art, "paragraph": None, "document_id": pcode,
            "chunk_id": f"{pcode}#{seq}", "citation": f"{law}{art}",
            "text": "法條本文",
            "provenance": {"pcode": pcode, "article_seq": seq,
                           "corpus_version": {"sha256": "s"},
                           "historical_text_unverified": True}}


def _cit(eid="st:P#1", jid="JX", surface="民法第184條"):
    return {"citation_id": "cit:JX#c0:0-7", "citation_type": "EXPLICIT_CITATION",
            "judgement_id": jid, "raw_text": surface, "evidence_id": eid,
            "resolution_status": "RESOLVED", "source_span": {"start": 0, "end": 7},
            "evidence_text": surface,
            "statute_evidence": _stat(jid=jid, eid=eid)}


def _graph(nodes):
    return {"judgement_ids": ["JX"], "nodes": nodes,
            "edges": [{"from": n["evidence_id"], "to": "judgement:JX",
                       "relation": "same_document"} for n in nodes]}


def good_response():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
             _node("reasoning:JX#C1:6-12", "REASONING", "查原告未受損害。")]
    evs = [_stat()]
    return A.build_answer(
        "准了嗎", judgement_id="JX",
        graph=_graph(nodes),
        citations=[_cit()], statute_evidences=evs, jdate="20260713")

