"""B4-A integration: reviewed-set equality, B2-D consumption, bypass rejection."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b4a_continuity as M  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b3a_attribution as A3  # noqa: E402
from app import b2d_answer as A  # noqa: E402

SET = json.loads((ROOT / "tests/fixtures/b4a_multichunk_set.json").read_text(encoding="utf-8"))


def _nodes_of(ch):
    from app import b2c_evidence as EE
    ds = EE.extract_disposition(ch)
    spans = [(d["source_span"]["start"], d["source_span"]["end"]) for d in ds]
    return ds + EE.extract_holding(ch, spans) + EE.extract_reasoning(ch)


def test_frozen_set_recomputes_exactly():
    # Rebuild every recorded assembly from frozen inputs. G-03 realizes its
    # labeled synthetic gap by dropping the middle fragment.
    from app import b4a_continuity as MM
    for case in SET["cases"]:
        nodes = list(case["nodes"])
        assert nodes, case["case_id"]
        amap = dict(case.get("attributions", {}))
        offs = dict(case.get("doc_offsets", {}))
        texts = dict(case.get("chunk_texts", {}))
        etype = nodes[0]["evidence_type"]
        if case.get("synthetic_gap"):
            mids = [n for n in nodes if "C005" in n["chunk_id"]]
            assert mids, case["case_id"]
            nodes = [n for n in nodes if "C005" not in n["chunk_id"]]
            assert len(nodes) >= 2, case["case_id"]
        rec = MM.assemble(nodes, evidence_type=etype, attributions=amap,
                          doc_offsets=offs, chunk_texts=texts)
        if case.get("synthetic_gap"):
            assert rec["status"] == "ORDERED_GAPPED"
            assert MM.GAP_MARKER in rec["text"]
            continue
        assert rec == case["assembly"], case["case_id"]


def test_g01_gapped_markers_and_g01b_contiguous_text():
    g01 = next(c for c in SET["cases"] if c["case_id"] == "G-01-contiguous-reasoning")
    assert g01["assembly"]["status"] == "ORDERED_GAPPED"
    assert M.GAP_MARKER in g01["assembly"]["text"]
    assert len(g01["assembly"]["gaps"]) == 1
    g01b = next(c for c in SET["cases"] if c["case_id"] == "G-01b-whitespace-continuous")
    assert g01b["assembly"]["status"] == "CONTIGUOUS"
    assert M.GAP_MARKER not in g01b["assembly"]["text"]


def test_b2d_answer_accepts_gated_multi_evidence():
    # A CONTIGUOUS assembly flows through the answer composer as ordinary
    # evidence; the continuity gate accepts its provenance.
    g01b = next(c for c in SET["cases"] if c["case_id"] == "G-01b-whitespace-continuous")
    asm = g01b["assembly"]
    node = {"evidence_id": asm["evidence_id"], "evidence_type": "REASONING",
            "judgement_id": g01b["judgement_id"], "document_id": g01b["judgement_id"],
            "chunk_id": asm["chunk_ids"][0], "text": asm["text"],
            "source_span": {"start": 0, "end": len(asm["text"]), "unit": "code_point",
                            "basis": "chunk_text"},
            "provenance": {}}
    graph = {"judgement_ids": [g01b["judgement_id"]], "nodes": [node],
             "edges": [{"from": node["evidence_id"],
                        "to": f"judgement:{g01b['judgement_id']}",
                        "relation": "same_document"}]}
    r = A.build_answer("分割方法为何？", judgement_id=g01b["judgement_id"],
                       graph=graph, citations=[], statute_evidences=[])
    # No disposition/holding in this chain: honestly insufficient, but the
    # continuity gate itself must pass on the multi item.
    assert M.continuity_gate([asm])["verdict"] == "PASS"
    assert r["status"] in ("ANSWERED", "INSUFFICIENT_EVIDENCE")


def test_hand_rolled_continuous_text_rejected_without_provenance():
    # M10: a multi-chunk item without assembly provenance fails the gate.
    bad = {"evidence_id": "m1", "continuity_status": "CONTIGUOUS",
           "status": "CONTIGUOUS", "chunk_ids": ["C1", "C2"],
           "evidence_type": "REASONING", "text": "aaaabbbb",
           "source_spans": [{"chunk_id": "C1", "start": 0, "end": 4}]}
    assert M.continuity_gate([bad])["verdict"] == "FAIL"
