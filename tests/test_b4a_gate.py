"""B4-A gate tests: M1-M10 over assembly records and answer-bound items."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b4a_continuity as M  # noqa: E402


def item(eid="m1", status="CONTIGUOUS", chunks=("C1", "C2"), etype="REASONING"):
    return {"evidence_id": eid, "continuity_status": status, "status": status,
            "chunk_ids": list(chunks), "evidence_type": etype,
            "text": "aaaabbbb" if len(chunks) > 1 else "aaaa",
            "source_spans": [{"chunk_id": c, "start": 0, "end": 4} for c in chunks],
            "attribution": "COURT_VOICE"}


def test_gate_passes_proven_items():
    assert M.continuity_gate([item()])["verdict"] == "PASS"
    assert M.continuity_gate([])["verdict"] == "PASS"
    gapped = item(status="ORDERED_GAPPED")
    gapped["text"] = f"aaaa\n{M.GAP_MARKER}\nbbbb"
    assert M.continuity_gate([gapped])["verdict"] == "PASS"


def test_m10_rejects_flattened_multi_items():
    bad = {"evidence_id": "m9", "continuity_status": "CONTIGUOUS",
           "chunk_ids": ["C1", "C2"], "evidence_type": "REASONING",
           "text": "aaaabbbb"}  # no source_spans: provenance stripped
    ok, failures = M.continuity_gate([bad])["verdict"], None
    assert ok == "FAIL"
    bad2 = dict(item(), source_spans=[{"chunk_id": "C1", "start": 0, "end": 4}])
    assert M.continuity_gate([bad2])["verdict"] == "FAIL"


def test_m4_unknown_joined_text_rejected():
    bad = {"evidence_id": "m9", "continuity_status": "CONTINUITY_UNKNOWN",
           "status": "CONTINUITY_UNKNOWN", "chunk_ids": ["C1", "C2"],
           "evidence_type": "REASONING", "text": "aaaabbbb",
           "source_spans": [{"chunk_id": "C1", "start": 0, "end": 4},
                            {"chunk_id": "C2", "start": 0, "end": 4}]}
    assert M.continuity_gate([bad])["verdict"] == "FAIL"


def test_m5_mixed_attribution_and_m8_disposition_rejected():
    bad = dict(item(), attribution="PARTY_VOICE")
    assert M.continuity_gate([bad])["verdict"] == "FAIL"
    disp = dict(item(eid="d1"), evidence_type="DISPOSITION",
                continuity_status="ORDERED_GAPPED", status="ORDERED_GAPPED")
    assert M.continuity_gate([disp])["verdict"] == "FAIL"


def test_m2_bad_status_rejected():
    bad = dict(item(), continuity_status="MAYBE", status="MAYBE")
    assert M.continuity_gate([bad])["verdict"] == "FAIL"
