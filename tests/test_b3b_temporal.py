"""B3-B unit: dates, T1-T4 rules (synthetic intervals labeled as such), gate."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3b_temporal as T  # noqa: E402


def _cit(law="民法", article="第184條"):
    return {"citation_id": "cit:J#c0:0-7", "citation_type": "EXPLICIT_CITATION",
            "judgement_id": "J", "raw_text": f"{law}{article}",
            "normalized": {"law_name": law, "article": article},
            "resolution_status": "RESOLVED"}


def _row(pcode="B0000001"):
    return {"pcode": pcode, "law_name": "民法", "article_no": "第 184 條"}


def test_date_validation():
    assert T.parse_compact_date("20260713") == "20260713"
    assert T.parse_compact_date("2026-13-45") is None
    assert T.parse_compact_date("20260230") is None
    assert T.parse_compact_date("") is None
    assert T.parse_compact_date(None) is None


def test_t4_current_only_real_path():
    r = T.validate_temporal(judgement_id="J", jdate="20260713",
                            citation=_cit(), statute_row=_row(),
                            law_meta={"law_modified_date": "20240101"})
    assert r["status"] == "CURRENT_ONLY"
    assert r["validity"] == "UNKNOWN" and r["text_version"] == "CURRENT_TEXT_ONLY"
    assert r["provenance"]["law_modified_date"] == "20240101"
    assert r["provenance"]["validation_rule"] == "T4-no-history"


def test_missing_date_and_record():
    r = T.validate_temporal(judgement_id="J", jdate="", citation=_cit(),
                            statute_row=_row())
    assert r["status"] == "TEMPORALLY_UNKNOWN"
    r = T.validate_temporal(judgement_id="J", jdate="20260713",
                            citation=_cit(), statute_row=None)
    assert r["status"] == "TEMPORALLY_UNKNOWN"


def _synth(jdate, start, end, exact_text=False):
    # Clearly-labeled SYNTHETIC version interval: exercises rule logic only,
    # never a legal claim (no such intervals exist in the real corpus).
    return T.validate_temporal(
        judgement_id="J", jdate=jdate, citation=_cit(), statute_row=_row(),
        version_interval={"effective_from": start, "effective_to": end,
                          "exact_text": exact_text})


def test_t1_exact_interval_verified():
    r = _synth("20260713", "20200101", "20300101", exact_text=True)
    assert r["status"] == "HISTORICAL_VERIFIED"
    assert r["validity"] == "VALID" and r["text_version"] == "EXACT_TEXT"


def test_t1_validity_without_text_stays_not_available():
    r = _synth("20260713", "20200101", "20300101", exact_text=False)
    assert r["status"] == "HISTORICAL_NOT_AVAILABLE"
    assert r["validity"] == "VALID" and r["text_version"] == "UNAVAILABLE"


def test_t2_open_ended_never_verified():
    r = _synth("20260713", "20200101", None, exact_text=True)
    assert r["status"] == "TEMPORALLY_UNKNOWN"


def test_t3_mismatch_and_boundary():
    r = _synth("20350101", "20200101", "20300101")
    assert r["status"] == "TEMPORALLY_INVALID" and r["validity"] == "INVALID"
    r = _synth("20200101", "20200101", "20300101")
    assert r["status"] == "TEMPORALLY_UNKNOWN"  # boundary equality: ambiguous
    r = _synth("20300101", "20200101", "20300101")
    assert r["status"] == "TEMPORALLY_UNKNOWN"


def test_gate_t1_t8():
    ok, failures = T.temporal_gate([{
        "citation_id": "c", "judgement_id": "J", "judgement_date": "20260713",
        "law_id": "B0000001", "status": "CURRENT_ONLY", "validity": "UNKNOWN",
        "text_version": "CURRENT_TEXT_ONLY",
        "provenance": {"statute_source": "data/laws/laws_flat.jsonl"}}])
    assert ok, failures
    bad = {"citation_id": "c", "judgement_id": "J", "judgement_date": "not-a-date",
           "law_id": "", "status": "HISTORICAL_VERIFIED", "validity": "VALID",
           "text_version": "UNAVAILABLE", "provenance": {}}
    ok, failures = T.temporal_gate([bad])
    assert not ok
    codes = {f.split(":")[0] for f in failures}
    assert {"T1", "T2", "T6", "T7"} <= codes  # VERIFIED w/o text proof also trips T6
