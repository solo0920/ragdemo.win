"""B1 unit: mechanical grounding gate — 6 checks + structural item 7 + negatives."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import b1_helpers as H
from app import b1_serve as B1

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

EV_J = {
    "evidence_id": "jdg:J#c0", "evidence_type": "judgement",
    "source_id": "e.json", "document_id": "J", "chunk_id": "J#chunk0",
    "citation": "[來源:J]", "text": "原告之訴駁回。",
}
EV_S = {
    "evidence_id": "st:P#1", "evidence_type": "statute",
    "source_id": "laws", "document_id": "P", "chunk_id": "P#1",
    "citation": "民法第184條", "text": "因故意或過失……",
}


def good_response():
    answer, claims = B1.compose_answer([EV_J], [EV_S])
    return {
        "status": "grounded", "answer": answer,
        "judgements": [], "statutes": [],
        "evidence": [EV_J, EV_S], "claims": claims, "abstention": None,
    }


def test_gate_passes_composed_answer():
    ok, failures = B1.grounding_gate(good_response())
    assert ok, failures


def test_claim_without_evidence_fails():
    r = good_response()
    r["claims"] = [{"claim_id": "c-x", "text": "x", "evidence_ids": []}]
    ok, failures = B1.grounding_gate(r)
    assert not ok and any("no evidence_ids" in f for f in failures)


def test_invalid_evidence_id_fails():
    r = good_response()
    r["claims"][0]["evidence_ids"] = ["jdg:nope#c9"]
    ok, failures = B1.grounding_gate(r)
    assert not ok and any("unknown evidence_id" in f for f in failures)


def test_missing_citation_fails():
    r = good_response()
    r["evidence"] = [dict(EV_J, citation=""), EV_S]
    ok, failures = B1.grounding_gate(r)
    assert not ok and any("missing citation" in f for f in failures)


def test_empty_evidence_text_fails():
    r = good_response()
    r["evidence"] = [EV_J, dict(EV_S, text="  ")]
    ok, failures = B1.grounding_gate(r)
    assert not ok and any("empty evidence text" in f for f in failures)


def test_wrong_evidence_type_fails():
    r = good_response()
    r["evidence"] = [dict(EV_J, evidence_type="web"), EV_S]
    ok, failures = B1.grounding_gate(r)
    assert not ok and any("bad evidence_type" in f for f in failures)


def test_injected_prose_fails_item7():
    r = good_response()
    r["answer"] += "本案顯然適用消滅時效。"
    ok, failures = B1.grounding_gate(r)
    assert not ok and any("ungrounded spans" in f for f in failures)


def test_non_grounded_status_rejected():
    r = good_response()
    r["status"] = "insufficient_evidence"
    ok, _ = B1.grounding_gate(r)
    assert not ok
