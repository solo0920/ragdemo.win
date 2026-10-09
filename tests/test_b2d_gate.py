"""B2-D gate tests: A1-A10 negatives + allowlist pins."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402
from b2d_helpers import good_response  # noqa: E402

if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))


def gate(r, **kw):
    from b2d_helpers import _cit
    return A.answer_gate(r, selected_judgement_id="JX",
                         citations=[_cit()], **kw)


def test_gate_passes_composed_answer():
    ok, failures = gate(good_response())
    assert ok, failures


def test_a1_claim_without_evidence_and_bad_limitation():
    r = good_response()
    r["claims"].append({"claim_id": "CX", "type": "HOLDING", "text": "x",
                        "evidence_ids": []})
    ok, failures = gate(r)
    assert not ok and any(f.startswith("A1") for f in failures)
    r = good_response()
    r["claims"].append({"claim_id": "CL", "type": "LIMITATION",
                        "text": "本院認為被告有罪。", "evidence_ids": []})
    ok, failures = gate(r)
    assert not ok and any(f.startswith("A1") for f in failures)


def test_a2_unknown_evidence_id():
    r = good_response()
    r["claims"][0]["evidence_ids"] = ["disposition:NOPE#c0:0-1"]
    ok, failures = gate(r)
    assert not ok and any(f.startswith("A2") for f in failures)


def test_a3_wrong_judgement_evidence():
    r = good_response()
    other = dict(r["evidence"])
    other["judgment"] = other["judgment"] + ["disposition:JY#C1:0-6"]
    r["evidence"] = other
    ok, failures = gate(r)
    assert not ok and any("A3/A5" in f for f in failures)


def test_a4_unresolved_statute_claim():
    r = good_response()
    ok, failures = A.answer_gate(r, selected_judgement_id="JX", citations=[])
    assert not ok and any(f.startswith("A4") for f in failures)


def test_a6_forbidden_claim_type_and_decisive_allowlist():
    r = good_response()
    r["claims"].append({"claim_id": "CX", "type": "APPLIED_STATUTE",
                        "text": "x", "evidence_ids": ["st:P#1"]})
    ok, failures = gate(r)
    assert not ok and any(f.startswith("A6") for f in failures)
    # The fixed strings themselves must never carry decisive phrasing.
    blob = "".join(A.HEADERS) + "".join(A.LIMITATIONS)
    for phrase in A._DECIDED_PHRASES:
        assert phrase not in blob, phrase


def test_a7_injected_prose_rejected():
    r = good_response()
    r["answer"] += "被告顯然有罪。"
    ok, failures = gate(r)
    assert not ok and any(f.startswith("A7") for f in failures)


def test_a9_display_mismatch_and_a10_missing_mandatory():
    r = good_response()
    r["citations"][0]["display"] = "民法第999條（P#1）"
    ok, failures = gate(r)
    assert not ok and any(f.startswith("A9") for f in failures)
    r = good_response()
    r["claims"] = [c for c in r["claims"] if c["type"] != "DISPOSITION"]
    r["evidence"]["judgment"] = [e for e in r["evidence"]["judgment"]
                                 if not e.startswith("disposition:")]
    ok, failures = gate(r)
    assert not ok and any(f.startswith("A10") for f in failures)
