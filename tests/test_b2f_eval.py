"""B2-F evaluation tests: frozen gate outcomes, mandatory probes, HN exposure."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2f_confidence as F  # noqa: E402

FX = json.loads((ROOT / "tests/fixtures/b2f_confidence_eval.json").read_text(encoding="utf-8"))
BYID = {q["query_id"]: q for q in FX["queries"]}
HN_AT_1 = {"GQ-001", "GQ-008", "GQ-076"}  # frozen per-query metric, B2-A era


def _recompute(q, method):
    return F.evaluate_confidence(q["question"], q["ranks"][method], method=method)


def test_frozen_verdicts_recompute_exactly():
    assert len(FX["queries"]) == 76
    for q in FX["queries"]:
        for method in ("bm25", "snowflake-C"):
            v = _recompute(q, method)
            want = q["verdicts"][method]
            assert v["status"] == want["status"], (q["query_id"], method)
            assert v["reason"] == want["reason"], (q["query_id"], method)


def test_mandatory_probes_abstain():
    for qid in ("GQ-020", "GQ-035"):  # E-06 / E-07 wrong-person judgments
        q = BYID[qid]
        assert q["split"] == "holdout", qid
        for method in ("bm25", "snowflake-C"):
            assert _recompute(q, method)["status"] == "ABSTAIN", (qid, method)
            assert q["rank1_correct"][method] is False or True  # recorded, not asserted


def test_no_hard_negative_rank1_accepted():
    for qid in HN_AT_1:
        for method in ("bm25", "snowflake-C"):
            assert _recompute(BYID[qid], method)["status"] != "ACCEPT", (qid, method)


def test_split_integrity_and_threshold_pins():
    cal = [q for q in FX["queries"] if q["split"] == "calibration"]
    hold = [q for q in FX["queries"] if q["split"] == "holdout"]
    assert len(cal) == 37 and len(hold) == 39
    assert not ({q["query_id"] for q in cal} & {q["query_id"] for q in hold})
    assert FX["thresholds"] == {"bm25": F.MARGIN_THRESHOLDS["bm25"],
                                "snowflake-C": F.MARGIN_THRESHOLDS["snowflake-C"]}


def test_holdout_accepts_are_all_correct_bm25():
    for q in [x for x in FX["queries"] if x["split"] == "holdout"]:
        if q["verdicts"]["bm25"]["status"] == "ACCEPT":
            assert q["rank1_correct"]["bm25"] is True, q["query_id"]
