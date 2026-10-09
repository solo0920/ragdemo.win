"""B2-C provenance + safety tests: spans, verbatim, negatives, gate failures."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import b2c_evidence as E  # noqa: E402

SET = __import__("json").loads(
    (ROOT / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))


def _texts(case):
    return {ch["chunk_id"]: ch["text"] for ch in case["chunks"]}


def test_every_node_resolves_into_source():
    for case in SET["cases"]:
        texts = _texts(case)
        for n in case["graph"]["nodes"]:
            sp = n["source_span"]
            assert texts[n["chunk_id"]][sp["start"]:sp["end"]] == n["text"], \
                (case["case_id"], n["evidence_id"])


def test_provenance_fields_complete():
    for case in SET["cases"]:
        for n in case["graph"]["nodes"]:
            assert n["judgement_id"] and n["chunk_id"] and n["evidence_id"]
            assert n["evidence_type"] in ("DISPOSITION", "HOLDING", "REASONING")
            assert "structural_role" in n["provenance"]


def test_tampered_text_fails_g4():
    case = next(c for c in SET["cases"] if c["case_id"] == "R-01-dismissal-header")
    g = copy.deepcopy({"nodes": case["graph"]["nodes"], "edges": case["graph"]["edges"]})
    g["nodes"][0]["text"] += "（偽造）"
    ok, failures = E.reasoning_gate(g, _texts(case))
    assert not ok and any("G4" in f for f in failures)


def test_malformed_span_fails():
    case = next(c for c in SET["cases"] if c["case_id"] == "R-01-dismissal-header")
    g = copy.deepcopy({"nodes": case["graph"]["nodes"], "edges": case["graph"]["edges"]})
    g["nodes"][0]["source_span"] = {"start": 50, "end": 10}
    ok, failures = E.reasoning_gate(g, _texts(case))
    assert not ok


def test_generated_and_forbidden_labels_rejected():
    node = {"evidence_id": "x", "evidence_type": "REASONING", "judgement_id": "J",
            "chunk_id": "J#C1", "text": "t",
            "source_span": {"start": 0, "end": 1, "unit": "code_point", "basis": "chunk_text"},
            "provenance": {"generated": True}}
    ok, failures = E.reasoning_gate({"nodes": [node], "edges": []}, {"J#C1": "t"})
    assert not ok and any("G6" in f for f in failures)
    bad = dict(node, evidence_type="APPLIED_STATUTE", provenance={})
    ok, failures = E.reasoning_gate({"nodes": [bad], "edges": []}, {"J#C1": "t"})
    assert not ok and any("G8" in f or "G7" in f for f in failures)
    ok, _ = E.reasoning_gate(
        {"nodes": [], "edges": [{"from": "a", "to": "b", "relation": "decides"}]}, {})
    assert not ok


def test_empty_graph_passes_gate():
    ok, failures = E.reasoning_gate({"nodes": [], "edges": []}, {})
    assert ok, failures  # nothing claimed: upstream abstains, gate does not invent


def test_cross_chunk_continuation_flagged():
    ch1 = {"chunk_id": "D#C1", "document_id": "D", "structural_role": "HEADER",
           "structural_labels": ["DISPOSITION", "HEADER"],
           "carries_declared_disposition_unit": True,
           "text": "法院判決\r\n　　主　文\r\n被告應給付原告", "span": {"start": 0}}
    ch2 = {"chunk_id": "D#C2", "document_id": "D", "structural_role": "FACTS",
           "structural_labels": ["FACTS"],
           "text": "新臺幣十萬元。\r\n理由要領如下：", "span": {"start": 100}}
    g = E.build_evidence_graph([ch1, ch2], [])
    disp = [n for n in g["nodes"] if n["evidence_type"] == "DISPOSITION"]
    assert len(disp) == 1
    assert disp[0]["provenance"].get("incomplete") is True
    assert disp[0]["provenance"].get("continued_chunk_id") == "D#C2"
    ok, _ = E.reasoning_gate(g, {c["chunk_id"]: c["text"] for c in (ch1, ch2)})
    assert ok  # flagged, not failed: downstream must handle incomplete
