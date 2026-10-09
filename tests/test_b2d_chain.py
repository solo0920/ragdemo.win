"""B2-D end-to-end: reviewed chains through retrieval stub -> answer -> gate.

Hermetic: reviewed judgments come from the B2-C fixture (vendored texts +
roles); citations/roles are real pipeline outputs, not hand-written.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

SET = json.loads((ROOT / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))
CORPUS = B1.StatuteCorpus.from_jsonl()


def _answer_for(case_id, question):
    case = next(c for c in SET["cases"] if c["case_id"] == case_id)
    cites = []
    for ch in case["chunks"]:
        cites.extend(C.extract_citations(
            ch["text"], jid=ch["document_id"], chunk_index=ch["chunk_index"],
            corpus=CORPUS))
    graph = E.build_evidence_graph(case["chunks"], cites)
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    return A.build_answer(question, judgement_id=case["judgement_id"],
                          graph=graph, citations=cites, statute_evidences=evs,
                          jdate="20260713")


def test_demo_chain_answers_with_all_layers():
    r = _answer_for("R-07-demo-holding", "原告依民法第184條請求3萬元，法院准了嗎？")
    assert r["status"] == "ANSWERED"
    assert "難認有據" in r["answer"]
    assert any(s["article"] == "第184條" for s in r["statutes"])
    assert any(s["article"] == "第195條" for s in r["statutes"])
    kinds = {c["type"] for c in r["claims"]}
    assert {"HOLDING", "REASONING", "STATUTE", "LIMITATION"} <= kinds
    # ANSWERED implies the internal A1-A10 gate passed (else it would abstain).
    assert r["abstention"] is None


def test_detention_chain_answers_disposition():
    r = _answer_for("R-04-detention-full-doc", "受收容人本次續予收容的事由為何？")
    assert r["status"] == "ANSWERED"
    assert "續予收容" in r["answer"]
    assert any(c["type"] == "DISPOSITION" for c in r["claims"])


def test_reasoning_only_chain_abstains():
    r = _answer_for("R-02-home-violence-reasoning", "被告為何構成犯罪？")
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "INSUFFICIENT_JUDGMENT_EVIDENCE"


def test_answer_builder_takes_no_retrieval_params():
    sig = inspect.signature(A.build_answer)
    assert "search_fn" not in sig.parameters and "embed_fn" not in sig.parameters
    sig2 = inspect.signature(C.extract_citations)
    assert "question" not in sig2.parameters and "query" not in sig2.parameters
