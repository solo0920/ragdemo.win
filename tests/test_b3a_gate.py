"""B3-A gate tests: Q1-Q8 negatives."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3a_attribution as A3  # noqa: E402


def item(eid="e1", att="COURT_VOICE", used=True, **kw):
    d = {"evidence_id": eid, "chunk_id": "J#C1",
         "source_span": {"start": 0, "end": 5, "unit": "code_point", "basis": "chunk_text"},
         "attribution": att, "rule_id": "R-X", "used_for_court_claim": used}
    d.update(kw)
    return d


def test_gate_passes_clean_court_items():
    ok, failures = A3.attribution_gate([item(), item("e2")])
    assert ok, failures


def test_q1_missing_attribution_and_q6_provenance():
    ok, failures = A3.attribution_gate(
        [{"evidence_id": "e1", "used_for_court_claim": True}])
    assert not ok and any(f.startswith("Q1") for f in failures)
    bad = item()
    bad["source_span"] = {"start": 9, "end": 2}
    ok, failures = A3.attribution_gate([bad])
    assert not ok and any(f.startswith("Q6") for f in failures)
    bad = dict(item(), rule_id="")
    ok, failures = A3.attribution_gate([bad])
    assert not ok and any("rule_id" in f for f in failures)


def test_q2_party_q3_quoted_q4_statute_q5_unresolved():
    cases = [("Q2", "PARTY_VOICE"), ("Q3", "QUOTED_OTHER_JUDGMENT"),
             ("Q4", "QUOTED_STATUTE"), ("Q5", "UNRESOLVED_ATTRIBUTION"),
             ("Q5", "PROCEDURAL_DESCRIPTION")]
    for code, att in cases:
        ok, failures = A3.attribution_gate([item(att=att)])
        assert not ok and any(f.startswith(code) for f in failures), (code, failures)


def test_q3_adoption_evidence_exception_and_q7_labels():
    ok, failures = A3.attribution_gate(
        [item(att="QUOTED_OTHER_JUDGMENT", adoption_evidence="prior-judgment X, adopted at span Y")])
    assert ok, failures
    ok, failures = A3.attribution_gate([item(att="MADE_UP")])
    assert not ok and any(f.startswith("Q7") for f in failures)


def test_non_claim_use_not_gated():
    # Display-only use (used_for_court_claim=False) passes attribution checks.
    ok, failures = A3.attribution_gate([item(att="PARTY_VOICE", used=False)])
    assert ok, failures
