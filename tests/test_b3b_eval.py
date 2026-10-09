"""B3-B eval tests: reviewed fixture frozen, gate holds, integration wording."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3b_temporal as T  # noqa: E402
from app import b1_serve as B1  # noqa: E402

FX = json.loads((ROOT / "tests/fixtures/b3b_temporal_set.json").read_text(encoding="utf-8"))
CORPUS = B1.StatuteCorpus.from_jsonl()
META = T.load_law_meta()
SYNC = T.corpus_sync_record()


def test_fixture_recomputes_exactly():
    assert len(FX["cases"]) == 5
    for case in FX["cases"]:
        found = None
        for r in CORPUS.rows.values():
            if r["law_name"] == case["record"]["law_name"] and \
               r["article_no"].replace(" ", "") == case["record"]["article"]:
                found = r
                break
        rec = T.validate_temporal(
            judgement_id=case["judgement_id"], jdate=case["jdate"],
            citation={"citation_id": "x",
                      "normalized": {"law_name": case["record"]["law_name"],
                                     "article": case["record"]["article"]}},
            statute_row=found,
            law_meta=META.get(case["record"]["law_name"], {}),
            sync_record=SYNC)
        assert rec["status"] == case["expected_status"], case["case_id"]
        assert rec["status"] == case["record"]["status"], case["case_id"]


def test_zero_false_historical_claims_on_fixture():
    for case in FX["cases"]:
        assert case["record"]["status"] != "HISTORICAL_VERIFIED", case["case_id"]
        if case["record"]["status"] == "CURRENT_ONLY":
            assert case["record"]["validity"] == "UNKNOWN"
            assert case["record"]["text_version"] == "CURRENT_TEXT_ONLY"


def test_gate_holds_on_fixture_records():
    recs = []
    for case in FX["cases"]:
        if case["case_id"] == "T-04":
            continue  # malformed-input probe: must FLAG, asserted below
        r = dict(case["record"])
        recs.append(r)
    ok, failures = T.temporal_gate(recs)
    assert ok, failures
    t04 = next(c for c in FX["cases"] if c["case_id"] == "T-04")
    ok, failures = T.temporal_gate([dict(t04["record"])])
    assert not ok and any(f.startswith("T1") for f in failures)


def test_law_level_dates_never_upgrade_article():
    # 民法 amended 20260817, AFTER demo judgment 20260713: still CURRENT_ONLY,
    # never INVALID (amendment may not touch the cited article) nor VERIFIED.
    c = next(x for x in FX["cases"] if x["case_id"] == "T-01")
    assert c["record"]["provenance"]["law_modified_date"] == "20260817"
    assert c["record"]["status"] == "CURRENT_ONLY"
