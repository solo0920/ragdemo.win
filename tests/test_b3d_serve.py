"""B3-D integration: enforcement in build_answer, A4b gate, bypass protection."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import b2d_answer as A  # noqa: E402
from app import b3d_attribution as D  # noqa: E402
from b2d_helpers import _node, _stat, _cit, _graph  # noqa: E402

if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))


def _resp(**kw):
    base = dict(question="q", judgement_id="JX", graph=_graph([
        _node("disposition:JX#C1:0-6", "DISPOSITION", "原告之訴駁回。"),
        _node("reasoning:JX#C1:6-14", "REASONING", "查甲理由成立。")]),
        citations=[_cit()], statute_evidences=[_stat()])
    base.update(kw)
    return A.build_answer(**base)


def test_unenforced_legacy_keeps_all_resolved():
    r = _resp()
    assert r["status"] == "ANSWERED"
    assert r["attribution_enforced"] is False
    assert [s["article"] for s in r["statutes"]] == ["第184條"]
    assert len(r["statute_attributions"]) == 1
    assert r["statute_attributions"][0]["attribution"] in D.STATUSES


def test_enforced_filters_and_records():
    nodes, cites, evs, amap = _scenario()
    disp = [n for n in nodes if n["evidence_type"] == "DISPOSITION"]
    court = [n for n in nodes if "被告抗辯" not in n["text"]]
    r = A.build_answer("q", judgement_id="JX", graph=_graph(court[:2]),
                       citations=cites[:1], statute_evidences=evs[:1],
                       attribution={k: amap[k] for k in
                                    [n["evidence_id"] for n in court[:2]]})
    assert r["status"] == "ANSWERED"
    assert r["attribution_enforced"] is True
    assert [s["article"] for s in r["statutes"]] == ["第184條"]
    rel = r["statute_attributions"][0]
    assert rel["attribution"] == D.COURT_VOICE_EXPLICIT_CITATION
    assert rel["downstream_eligibility"]["court_used"] is True


def _scenario():
    # Realistic shapes: full-sentence evidence_texts, coherent spans.
    disp_text = "主文原告之訴駁回。"
    court_text = "查本案依民法第184條，應駁回原告之訴。"
    party_text = "被告抗辯原告依民法第195條應賠償。"
    s184 = court_text.find("民法第184條")
    s195 = party_text.find("民法第195條")
    nodes = [_node("disposition:JX#C1:0-10", "DISPOSITION", disp_text),
             _node("reasoning:JX#C1:10-28", "REASONING", court_text),
             _node("reasoning:JX#C1:28-44", "REASONING", party_text)]
    for n, t in zip(nodes, (disp_text, court_text, party_text)):
        n["text"] = t
        n["source_span"] = {"start": 0, "end": len(t), "unit": "code_point",
                            "basis": "chunk_text"}
    cit184 = {"citation_id": "cit:JX#c0:4-12", "judgement_id": "JX",
              "chunk_id": "JX#chunk0", "raw_text": "民法第184條",
              "normalized": {"law_name": "民法", "article": "第184條"},
              "source_span": {"start": s184, "end": s184 + 7,
                              "unit": "code_point", "basis": "chunk_text"},
              "resolution_status": "RESOLVED", "evidence_id": "st:P#1",
              "evidence_text": court_text, "statute_evidence": None}
    cit195 = {"citation_id": "cit:JX#c0:30-38", "judgement_id": "JX",
              "chunk_id": "JX#chunk0", "raw_text": "民法第195條",
              "normalized": {"law_name": "民法", "article": "第195條"},
              "source_span": {"start": s195, "end": s195 + 7,
                              "unit": "code_point", "basis": "chunk_text"},
              "resolution_status": "RESOLVED", "evidence_id": "st:P#2",
              "evidence_text": party_text, "statute_evidence": None}
    ev184 = _stat()
    ev195 = _stat(eid="st:P#2", art="第195條", seq=2)
    cit184["statute_evidence"] = ev184
    cit195["statute_evidence"] = ev195
    amap = {"disposition:JX#C1:0-10": "COURT_VOICE",
            "reasoning:JX#C1:10-28": "COURT_VOICE",
            "reasoning:JX#C1:28-44": "PARTY_VOICE"}
    return nodes, [cit184, cit195], [ev184, ev195], amap


def test_enforced_party_citation_excluded_with_record():
    nodes, cites, evs, amap = _scenario()
    r = A.build_answer("q", judgement_id="JX", graph=_graph(nodes),
                       citations=cites, statute_evidences=evs,
                       attribution=amap)
    assert r["status"] == "ANSWERED"
    assert [s["article"] for s in r["statutes"]] == ["第184條"]
    assert "被告抗辯" not in (r["answer"] or "")
    by_raw = {c["raw_text"]: c for c in r["statute_attributions"]}
    assert by_raw["民法第184條"]["attribution"] == D.COURT_VOICE_EXPLICIT_CITATION
    assert by_raw["民法第195條"]["attribution"] == D.PARTY_ATTRIBUTION
    assert by_raw["民法第195條"]["downstream_eligibility"]["court_used"] is False


def test_a4b_rejects_non_court_statute_in_enforced_response():
    r = _resp(attribution={"disposition:JX#C1:0-6": "COURT_VOICE",
                           "reasoning:JX#C1:6-14": "COURT_VOICE"})
    assert r["status"] == "ANSWERED"
    bad = copy.deepcopy(r)
    bad["evidence"]["statutes"] = ["st:FORGED#9"]
    bad["claims"].append({"claim_id": "CX", "type": "STATUTE",
                          "text": "法條本文", "evidence_ids": ["st:FORGED#9"]})
    ok, failures = A.answer_gate(bad, selected_judgement_id="JX",
                                 citations=[_cit()])
    assert not ok and any("court-used relation" in f for f in failures)


def test_a4b_skipped_for_legacy_responses():
    r = _resp()
    assert "attribution_enforced" in r and r["attribution_enforced"] is False
    ok, failures = A.answer_gate(r, selected_judgement_id="JX",
                                 citations=[_cit()])
    assert ok, failures


def test_temporal_status_propagates_into_relation():
    nodes, cites, evs, amap = _scenario()
    r = A.build_answer("q", judgement_id="JX", graph=_graph(nodes),
                       citations=cites, statute_evidences=evs,
                       attribution=amap, temporal={"st:P#1": "CURRENT_ONLY"})
    rel = [x for x in r["statute_attributions"]
           if x["statute_evidence_id"] == "st:P#1"][0]
    assert rel["temporal_status"] == "CURRENT_ONLY"
    assert rel["attribution"] == D.COURT_VOICE_EXPLICIT_CITATION
