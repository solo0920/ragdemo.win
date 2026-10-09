"""B3-A attribution tests: frozen verified set + rule units."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3a_attribution as A3  # noqa: E402

SET = json.loads((ROOT / "tests/fixtures/b3a_attribution_set.json").read_text(encoding="utf-8"))


def test_frozen_verified_set_matches_exactly():
    assert len(SET["cases"]) == 12
    for case in SET["cases"]:
        rec = A3.classify_attribution(case["passage"])
        assert rec["attribution"] == case["expected_attribution"], case["passage_id"]
        assert rec["eligible_for_court_claim"] == case["expected_eligible"], case["passage_id"]
        assert rec["rule_id"]


def test_party_voice_markers():
    for text in ["被告抗辯原告之請求為無理由", "原告主張被告應賠償",
                 "被告上訴意旨：原判決不當", "原告聲明被告應給付"]:
        assert A3.classify_attribution(text)["attribution"] == "PARTY_VOICE", text


def test_prior_judgment_markers():
    assert A3.classify_attribution("原審認為被告有罪")["attribution"] == "QUOTED_OTHER_JUDGMENT"
    r = A3.classify_attribution("自應認為得請求分割（最高法院81年台上字第2688號判決可參）")
    assert r["attribution"] == "COURT_VOICE"  # own conclusion + parenthetical cite


def test_court_voice_markers():
    assert A3.classify_attribution("查被告與告訴人為叔嫂關係")["attribution"] == "COURT_VOICE"
    assert A3.classify_attribution("本院認為原告之訴無理由")["attribution"] == "COURT_VOICE"


def test_procedural_markers():
    assert A3.classify_attribution("如不服本判決，得於20日內提出上訴")["attribution"] == "PROCEDURAL_DESCRIPTION"


def test_ambiguous_fails_closed():
    r = A3.classify_attribution("被告於昨晚前往台北")
    assert r["attribution"] == "UNRESOLVED_ATTRIBUTION"
    assert not r["eligible_for_court_claim"]
    assert A3.classify_attribution("")["attribution"] == "UNRESOLVED_ATTRIBUTION"


def test_deterministic():
    t = "查被告與告訴人為叔嫂關係"
    assert A3.classify_attribution(t) == A3.classify_attribution(t)
