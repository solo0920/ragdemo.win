"""B2-D contract tests: answered/abstention shapes, claim mapping, judgment block."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402


if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from b2d_helpers import good_response  # noqa: E402

def test_answered_contract_shape():
    r = good_response()
    assert r["status"] == "ANSWERED"
    assert r["answer"] and r["abstention"] is None
    assert r["judgment"] == {"judgement_id": "JX", "judgement_number": "JX",
                             "date": "20260713"}
    assert "court" not in r["judgment"]  # FR-016: never inferred
    assert r["judgements"][0]["judgement_id"] == "JX"
    assert r["statutes"] == [{"law_name": "民法", "article": "第184條",
                              "paragraph": None}]
    assert r["citations"] == [{"evidence_id": "st:P#1",
                               "display": "民法第184條（P#1）"}]
    kinds = [c["type"] for c in r["claims"]]
    assert kinds[:2] == ["DISPOSITION", "REASONING"]
    assert set(kinds) <= {"DISPOSITION", "HOLDING", "REASONING", "STATUTE",
                          "LIMITATION"}


def test_claims_point_at_existing_evidence():
    r = good_response()
    known = set(r["evidence"]["judgment"]) | set(r["evidence"]["statutes"])
    for c in r["claims"]:
        if c["type"] == "LIMITATION":
            assert c["evidence_ids"] == []
        else:
            assert c["evidence_ids"] and set(c["evidence_ids"]) <= known


def test_abstention_contract_shape():
    r = A.build_answer("q", judgement_id="JX",
                       graph={"judgement_ids": [], "nodes": [], "edges": []},
                       citations=[], statute_evidences=[])
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["answer"] is None and r["claims"] == [] and r["citations"] == []
    assert r["abstention"]["reason"] == "NO_JUDGMENT"
    assert r["judgment"] is None


def test_no_court_inference_anywhere():
    r = good_response()
    rest = r["answer"]
    for q in [c["text"] for c in r["claims"] if c["type"] != "LIMITATION"]:
        rest = rest.replace(q, "", 1)
    for fixed in list(A.HEADERS) + list(A.LIMITATIONS):
        rest = rest.replace(fixed, "")
    for disp in [c["display"] for c in r["citations"]]:
        rest = rest.replace(disp, "", 1)
    # JID/date line carries no court name either.
    rest = rest.replace("JX", "").replace("20260713", "")
    assert "法院" not in rest and "簡易" not in rest
    assert "court" not in r["judgment"]
