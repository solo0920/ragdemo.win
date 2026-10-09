"""B2-F unit: features, gate states, determinism, boundary values."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2f_confidence as F  # noqa: E402

TH = {"bm25": 10.0, "snowflake-C": 0.01}


def ranked(scores, jid="J1", prefix="J1#C"):
    return [{"chunk_id": f"{prefix}{i}", "judgement_id": jid, "score": s}
            for i, s in enumerate(scores)]


def test_accept_high_margin_abstain_low_margin():
    assert F.evaluate_confidence("q", ranked([50.0, 10.0, 5.0]),
                                 method="bm25", thresholds=TH)["status"] == "ACCEPT"
    r = F.evaluate_confidence("q", ranked([10.0, 9.0, 5.0]),
                              method="bm25", thresholds=TH)
    assert r["status"] == "ABSTAIN" and "margin" in r["reason"]
    assert r["features"]["score_margin"] == 1.0
    assert r["gate_version"] == "b2f/v1"


def test_boundary_value_is_accepted():
    r = F.evaluate_confidence("q", ranked([20.0, 10.0]), method="bm25", thresholds=TH)
    assert r["status"] == "ACCEPT"  # margin == threshold passes (>=)


def test_explicit_identity_mismatch_rejects():
    r = F.evaluate_confidence("115年度台抗字第878號如何？",
                              ranked([90.0, 10.0], jid="TPSM,115,台抗,1470,20260730,1"),
                              method="bm25", thresholds=TH)
    assert r["status"] == "REJECT"
    assert r["reason"] == "explicit-identity-mismatch"


def test_identity_match_does_not_reject():
    r = F.evaluate_confidence("115年度新小字第380號如何？",
                              ranked([90.0, 10.0], jid="SSEV,115,新小,380,20260715,1"),
                              method="bm25", thresholds=TH)
    assert r["status"] == "ACCEPT"


def test_unknown_method_abstains_and_malformed_abstains():
    r = F.evaluate_confidence("q", ranked([90.0, 10.0]), method="unmeasured-model",
                              thresholds=TH)
    assert r["status"] == "ABSTAIN" and r["reason"] == "unknown-method"
    assert F.evaluate_confidence("q", [], method="bm25",
                                 thresholds=TH)["status"] == "ABSTAIN"
    assert F.evaluate_confidence("q", [{"chunk_id": "x"}],
                                 method="bm25", thresholds=TH)["status"] == "ABSTAIN"


def test_deterministic_and_pinned_thresholds():
    a = F.evaluate_confidence("q", ranked([50.0, 10.0, 5.0]), method="bm25")
    b = F.evaluate_confidence("q", ranked([50.0, 10.0, 5.0]), method="bm25")
    assert a == b
    assert F.MARGIN_THRESHOLDS["bm25"] == 38.742660905265
    assert F.MARGIN_THRESHOLDS["snowflake-C"] == 0.12242048428695307
    assert F.GATE_VERSION == "b2f/v1"


def test_report_shape():
    r = F.evaluate_confidence("q", ranked([50.0, 10.0]), method="bm25", thresholds=TH)
    assert set(r) == {"status", "score", "features", "gate_version", "reason"}
    assert r["features"]["rank1_judgement_id"] == "J1"
