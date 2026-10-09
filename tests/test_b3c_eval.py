"""B3-C eval tests: reviewed fixture frozen + corpus-wide zero false alarms."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3c_contra as K  # noqa: E402
from app import b2c_evidence as E  # noqa: E402

FX = json.loads((ROOT / "tests/fixtures/b3c_contradiction_set.json").read_text(encoding="utf-8"))


def test_fixture_outcomes_frozen():
    assert len(FX["cases"]) == 3
    for case in FX["cases"]:
        fs = K.check_multi_judgments(
            [{"judgement_id": d["judgement_id"], "outcome": d["outcome"]}
             for d in case["dispositions"]])
        direct = sum(1 for f in fs if f["status"] == "DETECTED")
        if case["expected"] == "DIRECT":
            assert direct == 1, case["case_id"]
            assert K.contradiction_gate(fs)["verdict"] == "FAIL"
        else:
            assert direct == 0, case["case_id"]
            assert K.contradiction_gate(fs)["verdict"] == "PASS"


def test_no_false_direct_on_real_corpus_pairs():
    # Hermetic: the 5 multi-disposition docs, vendored whole in the fixture.
    checked = direct = 0
    for doc in FX["multi_disposition_docs"]:
        items = []
        for ch in doc["chunks"]:
            for d in E.extract_disposition(ch):
                items.append({"evidence_id": d["evidence_id"],
                              "judgement_id": doc["document_id"],
                              "text": d["text"]})
        assert len(items) >= 2, doc["document_id"]
        for f in K.check_dispositions(items):
            checked += 1
            if f["status"] == "DETECTED":
                direct += 1
    assert checked > 0 and direct == 0
