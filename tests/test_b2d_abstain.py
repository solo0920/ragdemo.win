"""B2-D abstention tests: every reason code, never best-effort."""
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
from b2d_helpers import _cit, _graph, _node, _stat  # noqa: E402


def _graph_of(nodes):
    return {"judgement_ids": ["JX"], "nodes": nodes,
            "edges": [{"from": n["evidence_id"], "to": "judgement:JX",
                       "relation": "same_document"} for n in nodes]}


def _ans(**kw):
    base = dict(question="q", judgement_id="JX", graph=_graph_of([]),
                citations=[], statute_evidences=[])
    base.update(kw)
    return A.build_answer(**base)


def test_no_judgment_and_insufficient_and_no_reasoning():
    assert _ans()["status"] == "INSUFFICIENT_EVIDENCE"
    assert _ans()["abstention"]["reason"] == "NO_JUDGMENT"
    g = _graph_of([_node("reasoning:JX#C1:0-4", "REASONING", "查本案。")])
    r = A.build_answer(question="q", judgement_id="JX", graph=g,
                       citations=[], statute_evidences=[])
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "INSUFFICIENT_JUDGMENT_EVIDENCE"
    g = _graph_of([_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。")])
    r = A.build_answer(question="q", judgement_id="JX", graph=g,
                       citations=[], statute_evidences=[])
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "NO_REASONING_EVIDENCE"


def test_require_statutes_triggers_unresolved_abstention():
    g = _graph_of([_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
                   _node("reasoning:JX#C1:6-12", "REASONING", "查原告未受損害。")])
    r = A.build_answer(question="q", judgement_id="JX", graph=g, citations=[],
                       statute_evidences=[], require_statutes=True)
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "STATUTE_EVIDENCE_UNRESOLVED"
    # Same evidence without the requirement answers (judgment-only).
    r = A.build_answer(question="q", judgement_id="JX", graph=g, citations=[],
                       statute_evidences=[])
    assert r["status"] == "ANSWERED"


def test_contradictory_dispositions_abstain():
    g = _graph_of([_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
                   _node("disposition:JX#C2:0-6", "DISPOSITION", "被告應給付原告。"),
                   _node("reasoning:JX#C1:6-12", "REASONING", "查原告未受損害。")])
    r = A.build_answer(question="q", judgement_id="JX", graph=g, citations=[],
                       statute_evidences=[])
    assert r["status"] == "ABSTAINED"
    assert r["abstention"]["reason"] == "CONTRADICTORY_EVIDENCE"
    assert r["answer"] is None


def test_grounding_failure_abstains_not_best_effort():
    import app.b2d_answer as M
    real = M.answer_gate
    M.answer_gate = lambda *a, **k: (False, ["injected"])
    try:
        g = _graph_of([_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
                       _node("reasoning:JX#C1:6-12", "REASONING", "查原告未受損害。")])
        r = A.build_answer(question="q", judgement_id="JX", graph=g, citations=[],
                           statute_evidences=[])
        assert r["status"] == "ABSTAINED"
        assert r["abstention"]["reason"] == "GROUNDING_FAILURE"
    finally:
        M.answer_gate = real


def test_serve_abstains_without_hits_and_without_roles():
    async def _empty(q, v, lim):
        return []

    async def _vec(texts):
        return [[0.0]]

    r = asyncio.run(A.serve_question(
        "問題", search_fn=_empty, embed_fn=_vec, jfull_by_entry={}))
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "NO_JUDGMENT"
