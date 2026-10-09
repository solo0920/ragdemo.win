"""B2-A unit: provenance preservation + R1-R5 gate logic (no invented thresholds)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2a_eval as E  # noqa: E402


def test_provenance_requires_chunk_id_and_score():
    ok, _ = E.provenance_ok({"chunk_id": "D1#C001", "score": 0.5, "document_id": None})
    assert ok  # null document_id is a noted observation, not loss
    ok, problems = E.provenance_ok({"chunk_id": "", "score": None})
    assert not ok
    assert any("missing chunk_id" in p for p in problems)
    assert any("non-numeric score" in p for p in problems)


def _gate(**kw):
    base = dict(identity_audit={"Q": {"valid": True}}, runs_agree=True,
                hn_threshold=None, hn_reference={"bm25": 0.05},
                failure_table=[{"query_id": "Q"}], total_failures=1,
                provenance_result={})
    base.update(kw)
    return E.gate_r1_to_r5(**base)


def test_r1_fails_on_invalid_identity_and_r2_stops_without_evidence():
    g = _gate(identity_audit={"Q": {"valid": False, "issues": ["x"]}})
    assert g["R1"]["verdict"] == "FAIL"
    g = _gate(runs_agree=None)
    assert g["R2"]["verdict"] == "STOP"


def test_r3_stops_without_declared_threshold():
    g = _gate()
    assert g["R3"]["verdict"] == "STOP"
    assert "threshold" in g["R3"]["evidence"]
    g = _gate(hn_threshold=0.10)
    assert g["R3"]["verdict"] == "PASS"
    g = _gate(hn_threshold=0.01)
    assert g["R3"]["verdict"] == "FAIL"


def test_r4_requires_every_failure_rowed_and_r5_requires_provenance():
    g = _gate(total_failures=2)
    assert g["R4"]["verdict"] == "FAIL"
    g = _gate(provenance_result={"bm25": {"problems": ["missing chunk_id"]}})
    assert g["R5"]["verdict"] == "FAIL"


def test_ranking_preserves_provenance_end_to_end():
    records = [{"chunk_id": f"D1#C{i:03d}", "score": 1.0 / (i + 1),
                "document_id": None} for i in range(5)]
    ranked = sorted(records, key=lambda e: -e["score"])
    assert [e["chunk_id"] for e in ranked] == [f"D1#C{i:03d}" for i in range(5)]
    assert all(E.provenance_ok(e)[0] for e in ranked)
