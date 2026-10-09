"""B4-B composition tests: synthesis links, decomposition gate, omissions."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402
from app import b4b_compose as B  # noqa: E402
from b2d_helpers import _node, _stat, _cit, _graph  # noqa: E402


def _rich():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
             _node("reasoning:JX#C1:6-14", "REASONING", "查甲理由成立。"),
             _node("reasoning:JX#C1:14-22", "REASONING", "查乙理由亦成立。")]
    return B.compose_sectioned(
        "准了嗎", judgement_id="JX", graph=_graph(nodes),
        citations=[_cit()], statute_evidences=[_stat()])


def _texts():
    return {"disposition:JX#C1:0-6": "原告之訴駁回。",
            "reasoning:JX#C1:6-14": "查甲理由成立。",
            "reasoning:JX#C1:14-22": "查乙理由亦成立。",
            "st:P#1": "法條本文"}


def test_synthesis_claim_links_multiple_evidence():
    r = _rich()
    assert r["status"] == "ANSWERED"
    synth = [c for c in r["claims"] if c["type"] == "ANSWER_SYNTHESIS"]
    assert len(synth) == 1
    assert synth[0]["evidence_ids"] == ["reasoning:JX#C1:6-14",
                                        "reasoning:JX#C1:14-22"]
    assert synth[0]["text"] == "查甲理由成立。\n查乙理由亦成立。"
    sec = {s["kind"]: s for s in r["sections"]}
    assert sec["REASONING"]["synthesis_claim_id"] == synth[0]["claim_id"]
    assert sec["DISPOSITION"]["synthesis_claim_id"] is None
    assert r["omitted_sections"] == [{"kind": "HOLDING",
                                      "reason": "no eligible evidence"}]


def test_forged_synthesis_rejected():
    r = _rich()
    bad = copy.deepcopy(r)
    for c in bad["claims"]:
        if c["type"] == "ANSWER_SYNTHESIS":
            c["text"] += "法院認為被告有罪。"
    ok, failures = A.answer_gate(bad, selected_judgement_id="JX",
                                 citations=[_cit()], evidence_texts=_texts())
    assert not ok and any("not grounded" in f for f in failures)


def test_synthesis_without_map_rejected():
    r = _rich()
    ok, failures = A.answer_gate(r, selected_judgement_id="JX",
                                 citations=[_cit()], evidence_texts=None)
    assert not ok and any("not grounded" in f for f in failures)


def test_unknown_evidence_id_in_synthesis_rejected():
    r = _rich()
    bad = copy.deepcopy(r)
    for c in bad["claims"]:
        if c["type"] == "ANSWER_SYNTHESIS":
            c["evidence_ids"] = c["evidence_ids"] + ["reasoning:JX#C9:0-1"]
    ok, failures = A.answer_gate(bad, selected_judgement_id="JX",
                                 citations=[_cit()], evidence_texts=_texts())
    assert not ok and any(f.startswith("A2") for f in failures)


def test_applied_decisive_types_rejected():
    r = _rich()
    bad = copy.deepcopy(r)
    bad["claims"].append({"claim_id": "CX", "type": "APPLIED_STATUTE",
                          "text": "x", "evidence_ids": ["st:P#1"]})
    ok, failures = A.answer_gate(bad, selected_judgement_id="JX",
                                 citations=[_cit()], evidence_texts=_texts())
    assert not ok and any(f.startswith("A6") for f in failures)
    bad["claims"][-1]["type"] = "DECISIVE_STATUTE"
    ok, failures = A.answer_gate(bad, selected_judgement_id="JX",
                                 citations=[_cit()], evidence_texts=_texts())
    assert not ok and any(f.startswith("A6") for f in failures)


def test_current_only_limitation_preserved():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
             _node("reasoning:JX#C1:6-14", "REASONING", "查甲理由成立。")]
    from app import b3b_temporal as T
    r = B.compose_sectioned(
        "准了嗎", judgement_id="JX", graph=_graph(nodes),
        citations=[_cit()], statute_evidences=[_stat()],
        temporal={"st:P#1": "CURRENT_ONLY"})
    assert r["status"] == "ANSWERED"
    assert T.LIMIT_CURRENT in (r["answer"] or "")


def test_omitted_sections_explicit_and_subset_scope():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
             _node("reasoning:JX#C1:6-14", "REASONING", "查甲理由成立。")]
    r = B.compose_sectioned(
        "准了嗎", judgement_id="JX", graph=_graph(nodes),
        citations=[_cit()], statute_evidences=[_stat()],
        sections=["DISPOSITION", "REASONING"])
    assert r["status"] == "ANSWERED"
    assert {s["kind"] for s in r["sections"]} == {"DISPOSITION", "REASONING"}
    assert "STATUTES" not in [s["kind"] for s in r["sections"]]
    assert r["statutes"] == [] and r["citations"] == []


def test_required_section_missing_abstains():
    nodes = [_node("reasoning:JX#C1:6-14", "REASONING", "查甲理由成立。")]
    r = B.compose_sectioned(
        "為何", judgement_id="JX", graph=_graph(nodes),
        citations=[], statute_evidences=[])
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "INSUFFICIENT_JUDGMENT_EVIDENCE"
    assert r["sections"] == []
    assert {o["kind"] for o in r["omitted_sections"]} == {
        "DISPOSITION", "HOLDING", "REASONING", "STATUTES"}


def test_unknown_section_kind_rejected():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。")]
    try:
        B.compose_sectioned("q", judgement_id="JX", graph=_graph(nodes),
                            citations=[], statute_evidences=[],
                            sections=["NOPE"])
    except ValueError:
        pass
    else:
        raise AssertionError("unknown section kind must raise")


def test_party_voice_nodes_excluded_from_synthesis():
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
             _node("reasoning:JX#C1:6-14", "REASONING", "查甲理由成立。"),
             _node("reasoning:JX#C1:14-24", "REASONING", "被告抗辯原告之請求為無理由。")]
    amap = {"disposition:JX#C1:0-6": "COURT_VOICE",
            "reasoning:JX#C1:6-14": "COURT_VOICE",
            "reasoning:JX#C1:14-24": "PARTY_VOICE"}
    r = B.compose_sectioned(
        "准了嗎", judgement_id="JX", graph=_graph(nodes),
        citations=[], statute_evidences=[], attribution=amap)
    assert r["status"] == "ANSWERED"
    assert "被告抗辯" not in (r["answer"] or "")
    for c in r["claims"]:
        assert "reasoning:JX#C1:14-24" not in c.get("evidence_ids", [])
