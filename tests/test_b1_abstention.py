"""B1 unit: abstention Cases A-D — never a best-effort answer."""
from __future__ import annotations

import sys
from pathlib import Path

import b1_helpers as H
from app import b1_serve as B1

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

QUESTION = H.load_case()["question"]

def _ahits(hits):
    async def go(q, v, lim):
        return hits
    return go


def _avec(vec):
    async def go(texts):
        return vec
    return go




async def _run(**kw):
    return await B1.serve_question(QUESTION, **kw)


def test_case_a_no_hits_abstains():
    import asyncio
    r = asyncio.run(_run(
        search_fn=_ahits([]),
        embed_fn=_avec([[0.0]]),
        jfull_by_entry=H.jfull_map(),
    ))
    assert r["status"] == "insufficient_evidence"
    assert r["answer"] is None
    assert r["abstention"]["reason"] == "no_judgement_hits"


def test_case_a_retrieval_error_abstains_not_500():
    import asyncio

    async def boom(q, v, lim):
        raise ConnectionError("qdrant down")

    r = asyncio.run(_run(
        search_fn=boom, embed_fn=_avec([[0.0]]),
        jfull_by_entry=H.jfull_map(),
    ))
    assert r["status"] == "insufficient_evidence"
    assert r["abstention"]["reason"] == "retrieval_unavailable"


def test_case_b_unquotable_abstains():
    import asyncio
    hits = H.real_hits()
    r = asyncio.run(_run(
        search_fn=_ahits(hits),
        embed_fn=_avec([[0.0]]),
        jfull_by_entry={},  # document side unwired: nothing quotable
    ))
    assert r["status"] == "insufficient_evidence"
    assert r["abstention"]["reason"] == "no_quotable_evidence"


def test_case_c_no_statute_evidence_abstains():
    import asyncio
    hits = H.real_hits()
    r = asyncio.run(_run(
        search_fn=_ahits(hits),
        embed_fn=_avec([[0.0]]),
        jfull_by_entry=H.jfull_map(),
        statute_corpus=B1.StatuteCorpus(rows={}),  # empty corpus: nothing linkable
    ))
    assert r["status"] == "insufficient_evidence"
    assert r["abstention"]["reason"] == "no_statute_evidence"


def test_case_d_gate_failure_abstains(monkeypatch):
    import asyncio
    hits = H.real_hits()
    monkeypatch.setattr(B1, "grounding_gate", lambda resp: (False, ["injected"]))
    r = asyncio.run(_run(
        search_fn=_ahits(hits),
        embed_fn=_avec([[0.0]]),
        jfull_by_entry=H.jfull_map(),
    ))
    assert r["status"] == "insufficient_evidence"
    assert r["abstention"]["reason"] == "grounding_gate_fail"


def test_empty_question_abstains():
    import asyncio
    r = asyncio.run(B1.serve_question("   "))
    assert r["status"] == "insufficient_evidence"
    assert r["answer"] is None
