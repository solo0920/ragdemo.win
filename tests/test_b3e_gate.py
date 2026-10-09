"""B3-E gate tests: A11 applied-statute consistency (forgery must fail)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402
from b2d_helpers import _cit, good_response  # noqa: E402

if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))


def gate(r, **kw):
    return A.answer_gate(r, selected_judgement_id="JX",
                         citations=[_cit()], **kw)


def _applied(cid="cit:JX#c0:0-7", eid="disposition:JX#C1:0-6", att="COURT_VOICE",
             rtype="COURT_EXPLICIT_APPLICATION"):
    return {"relation_id": "applied:abc123", "citation_id": cid,
            "judgement_id": "JX", "relation_type": rtype,
            "citation_evidence": {"chunk_id": "JX#C1"},
            "application_evidence": {"evidence_id": eid, "attribution": att},
            "rule_id": "holding-containment-v1"}


def test_a11_unresolved_entries_pass_through():
    r = good_response()
    r["applied_statutes"] = [{**_applied(), "relation_type": "APPLICATION_UNRESOLVED",
                              "application_evidence": None}]
    ok, failures = gate(r)
    assert ok, failures


def test_a11_valid_applied_passes():
    r = good_response()
    r["applied_statutes"] = [_applied()]
    ok, failures = gate(r)
    assert ok, failures


def test_a11_forged_citation_id_fails():
    r = good_response()
    r["applied_statutes"] = [_applied(cid="cit:JX#c9:99-100")]
    ok, failures = gate(r)
    assert not ok and any("A11" in f and "not in response" in f for f in failures)


def test_a11_unknown_evidence_id_fails():
    r = good_response()
    r["applied_statutes"] = [_applied(eid="holding:JX#C9:0-5")]
    ok, failures = gate(r)
    assert not ok and any("A11" in f and "unknown" in f for f in failures)


def test_a11_non_court_attribution_fails():
    r = good_response()
    for att in ("PARTY_VOICE", "UNRESOLVED_ATTRIBUTION", ""):
        r["applied_statutes"] = [_applied(att=att)]
        ok, failures = gate(r)
        assert not ok and any("A11" in f and "court voice" in f for f in failures), att


def test_a11_bad_relation_type_fails():
    r = good_response()
    r["applied_statutes"] = [_applied(rtype="DECISIVE_STATUTE")]
    ok, failures = gate(r)
    assert not ok and any("A11" in f and "bad applied relation type" in f for f in failures)


def test_a11_never_authorizes_new_claim_wording():
    # Even with a verified applied relation, the answer carries no
    # APPLIED_STATUTE/decisive claim kind (A6) and no decisive phrasing (A7).
    r = good_response()
    r["applied_statutes"] = [_applied()]
    kinds = {c["type"] for c in r["claims"]}
    assert "APPLIED_STATUTE" not in kinds
    assert "DECISIVE_STATUTE" not in kinds
    ok, failures = gate(r)
    assert ok, failures


# ── S4 binding integrity (real answer_gate / build_answer, no mocks) ──

HOLD_TEXT = "㈡綜上所述，原告依民法第184條之規定請求，為無理由，應予駁回。"
DISP_TEXT = "原告之訴駁回。"
REA_TEXT = "查原告未受損害。"
HOLD = {"evidence_id": "holding:JX#C1:0-32", "evidence_type": "HOLDING",
        "judgement_id": "JX", "document_id": "JX", "chunk_id": "JX#C1",
        "text": HOLD_TEXT,
        "source_span": {"start": 0, "end": 32, "unit": "code_point",
                        "basis": "chunk_text"},
        "provenance": {"method": "conclusory_pattern", "marker": "為無理由",
                       "heuristic": True}}
CIT_SPAN = {"start": HOLD_TEXT.find("民法第184條"),
            "end": HOLD_TEXT.find("民法第184條") + len("民法第184條")}


def _live_cit():
    from b2d_helpers import _stat
    return {"citation_id": "cit:JX#C1:4-11", "citation_type": "EXPLICIT_CITATION",
            "judgement_id": "JX", "document_id": "JX", "chunk_id": "JX#C1",
            "raw_text": "民法第184條", "source_span": dict(CIT_SPAN),
            "evidence_text": HOLD_TEXT, "resolution_status": "RESOLVED",
            "evidence_id": "st:P#1", "statute_evidence": _stat()}


def _live_world():
    from b2d_helpers import _node, _stat
    nodes = [_node("disposition:JX#C1:0-6", "DISPOSITION", DISP_TEXT),
             dict(HOLD),
             _node("reasoning:JX#C1:6-12", "REASONING", REA_TEXT)]
    graph = {"judgement_ids": ["JX"], "nodes": nodes, "edges": []}
    amap = {n["evidence_id"]: "COURT_VOICE" for n in nodes}
    return graph, [_live_cit()], [_stat()], amap


def _live_answered():
    graph, cites, evs, amap = _live_world()
    r = A.build_answer("准了嗎", judgement_id="JX", graph=graph,
                       citations=cites, statute_evidences=evs,
                       attribution=amap)
    assert r["status"] == "ANSWERED", r.get("abstention")
    applied = [a for a in r["applied_statutes"]
               if a["relation_type"] == "COURT_EXPLICIT_APPLICATION"]
    assert len(applied) == 1
    return r, graph, cites


def _reg(a, cites, nodes):
    return A.answer_gate(a, selected_judgement_id="JX", citations=cites,
                         nodes=nodes)


def test_s4_live_valid_binding_accepted():
    # (1) valid binding + (10) real path: build_answer computes the APPLIED
    # relation itself and its own A11 (with nodes) accepts it.
    r, _, _ = _live_answered()


def test_s4_mispaired_valid_ids_rejected():
    # (2) both IDs individually valid, paired incorrectly.
    r, graph, cites = _live_answered()
    bad = dict(r["applied_statutes"][0])
    bad["application_evidence"] = {"evidence_id": "reasoning:JX#C1:6-12",
                                   "attribution": "COURT_VOICE"}
    r["applied_statutes"] = [bad]
    ok, failures = _reg(r, cites, graph["nodes"])
    assert not ok and any("A11" in f and "not covered" in f for f in failures)


def test_s4_cross_case_binding_rejected():
    # (3) citation and evidence from different cases.
    r, graph, cites = _live_answered()
    bad = dict(r["applied_statutes"][0])
    bad["judgement_id"] = "JY"
    r["applied_statutes"] = [bad]
    ok, failures = _reg(r, cites, graph["nodes"])
    assert not ok and any("A11" in f and "mismatch" in f for f in failures)


def test_s4_span_mismatch_rejected():
    # (4)(5) spans do not correspond / citation outside the holding span.
    r, graph, cites = _live_answered()
    moved = dict(cites[0])
    moved["source_span"] = {"start": 100, "end": 107}
    ok, failures = _reg(r, [moved], graph["nodes"])
    assert not ok and any("A11" in f and "not covered" in f for f in failures)


def test_s4_sentence_not_in_node_rejected():
    r, graph, cites = _live_answered()
    moved = dict(cites[0])
    moved["evidence_text"] = "原告主張依民法第184條請求賠償。"
    ok, failures = _reg(r, [moved], graph["nodes"])
    assert not ok and any("A11" in f and "not in node text" in f for f in failures)


def test_s4_malformed_binding_fails_closed():
    # (7) missing / null / malformed fields never yield APPLIED.
    r, graph, cites = _live_answered()
    base = r["applied_statutes"][0]
    variants = [
        {**base, "application_evidence": None},
        {**base, "application_evidence": {}},
        {**base, "citation_id": "cit:JX#c9:0-1"},
        {},
    ]
    for v in variants:
        r["applied_statutes"] = [v]
        ok, failures = _reg(r, cites, graph["nodes"])
        assert not ok and any("A11" in f for f in failures), v
    # Malformed (non-integer) node span also fails closed, not through.
    odd_nodes = [dict(n, source_span={"start": "x", "end": "y"})
                 if n["evidence_id"] == HOLD["evidence_id"] else n
                 for n in graph["nodes"]]
    r["applied_statutes"] = [base]
    ok, failures = _reg(r, cites, odd_nodes)
    assert not ok and any("A11" in f and "not covered" in f for f in failures)


def test_s4_fixture_unresolved_pass_through():
    # (9) the eight stored UNRESOLVED relations stay green through the gate.
    import json
    fx = json.loads((ROOT / "tests/fixtures/b3e_applied_set.json").read_text(encoding="utf-8"))
    stored = [c["relation"] for c in fx["cases"]
              if c["relation"]["relation_type"] == "APPLICATION_UNRESOLVED"]
    assert len(stored) == 8
    r = good_response()
    r["applied_statutes"] = stored
    cites = [_cit()] + [{"citation_id": s["citation_id"], "judgement_id": "JX"}
                        for s in stored]
    ok, failures = A.answer_gate(r, selected_judgement_id="JX", citations=cites)
    assert ok, failures


# ── S2a P1: multi-chunk provenance (no promotion; pairing enforced) ──

MS1 = "原告依民法第184條請求給付。"
MS2 = "為無理由，應予駁回。"
MH1 = {"evidence_id": "holding:JX#C1:0-15", "evidence_type": "HOLDING",
       "judgement_id": "JX", "document_id": "JX", "chunk_id": "JX#C1",
       "text": MS1,
       "source_span": {"start": 0, "end": 15, "unit": "code_point",
                       "basis": "chunk_text"},
       "provenance": {"method": "conclusory_pattern", "marker": "為無理由",
                      "heuristic": True}}
MH2 = {"evidence_id": "holding:JX#C2:0-10", "evidence_type": "HOLDING",
       "judgement_id": "JX", "document_id": "JX", "chunk_id": "JX#C2",
       "text": MS2,
       "source_span": {"start": 0, "end": 10, "unit": "code_point",
                       "basis": "chunk_text"},
       "provenance": {"method": "conclusory_pattern", "marker": "為無理由",
                      "heuristic": True}}
MCIT = {"citation_id": "cit:JX#C1:3-9", "citation_type": "EXPLICIT_CITATION",
        "judgement_id": "JX", "document_id": "JX", "chunk_id": "JX#chunk0",
        "raw_text": "民法第184條",
        "source_span": {"start": 3, "end": 9, "unit": "code_point",
                        "basis": "chunk_text"},
        "evidence_text": MS1, "resolution_status": "RESOLVED",
        "evidence_id": "st:P#1"}


def _multi_app(**kw):
    from app import b3e_applied as E3
    base = {"relation_id": "applied-multi:xxx", "citation_id": "cit:JX#C1:3-9",
            "judgement_id": "JX", "relation_type": "COURT_EXPLICIT_APPLICATION",
            "citation_evidence": {"chunk_id": "JX#C1"},
            "application_evidence": {
                "evidence_id": "holding:JX#C1:0-15",
                "attribution": "COURT_VOICE",
                "chunk_ids": ["JX#C1", "JX#C2"],
                "source_spans": [{"start": 0, "end": 15},
                                 {"start": 0, "end": 10}],
                "fragment_node_ids": ["holding:JX#C1:0-15",
                                      "holding:JX#C2:0-10"],
                "assembly_id": E3.multi_assembly_id(
                    "cit:JX#C1:3-9", "JX", ["JX#C1", "JX#C2"],
                    [{"start": 0, "end": 15}, {"start": 0, "end": 10}]),
                "continuity_status": "CONTIGUOUS"},
            "rule_id": "holding-containment-v1"}
    base["application_evidence"] = {**base["application_evidence"],
                                    **kw.pop("app", {})}
    base.update(kw)
    return base


_NO_NODES = object()


def _multi_reg(resp_app, cites=None, nodes=_NO_NODES):
    from b2d_helpers import _stat
    r = good_response()
    r["applied_statutes"] = [resp_app]
    ev = r["evidence"]
    for eid in ("holding:JX#C1:0-15", "holding:JX#C2:0-10", "st:P#1"):
        if eid not in ev["judgment"] and eid != "st:P#1":
            ev["judgment"] = ev["judgment"] + [eid]
    return A.answer_gate(r, selected_judgement_id="JX",
                         citations=[_cit(), MCIT] if cites is None else cites,
                         **({"nodes": None} if nodes is None
                            else {"nodes": [dict(MH1), dict(MH2)]
                                  if nodes is _NO_NODES else nodes}))


def test_s4_multi_valid_provenance_passes():
    ok, failures = _multi_reg(_multi_app())
    assert ok, failures


def test_s4_multi_missing_or_ragged_provenance_fails():
    bad = _multi_app(app={"chunk_ids": ["JX#C1", "JX#C2"]})
    del bad["application_evidence"]["source_spans"]
    ok, failures = _multi_reg(bad)
    assert not ok and any("A11" in f and "malformed" in f for f in failures)
    bad = _multi_app(app={"source_spans": [{"start": 0, "end": 15}]})
    ok, failures = _multi_reg(bad)
    assert not ok and any("A11" in f and "malformed" in f for f in failures)
    bad = _multi_app(app={"continuity_status": "ORDERED_GAPPED"})
    ok, failures = _multi_reg(bad)
    assert not ok and any("A11" in f and "not contiguous" in f for f in failures)
    bad = _multi_app(app={"assembly_id": "applied-multi:deadbeefdeadbeef"})
    ok, failures = _multi_reg(bad)
    assert not ok and any("A11" in f and "mismatch" in f for f in failures)


def test_s4_multi_judgement_and_bridge_fail():
    bad = _multi_app(judgement_id="JY")
    ok, failures = _multi_reg(bad)
    assert not ok and any("A11" in f and "mismatch" in f for f in failures)
    nodes = [dict(MH1), dict(MH2, judgement_id="JY")]
    ok, failures = _multi_reg(_multi_app(), nodes=nodes)
    assert not ok and any("A11" in f and "fragment nodes mismatch" in f for f in failures)
    moved = dict(MCIT, source_span={"start": 50, "end": 56})
    ok, failures = _multi_reg(_multi_app(), cites=[moved])
    assert not ok and any("A11" in f and "bridge failed" in f for f in failures)
    moved = dict(MCIT, evidence_text="原告主張依民法第184條請求賠償。")
    ok, failures = _multi_reg(_multi_app(), cites=[moved])
    assert not ok and any("A11" in f and "bridge failed" in f for f in failures)


def test_s4_multi_needs_nodes_single_unchanged():
    # Multi without nodes fails closed; single without nodes keeps legacy.
    ok, failures = _multi_reg(_multi_app(), nodes=None)
    assert not ok and any("A11" in f and "needs nodes" in f for f in failures)
    r = good_response()
    r["applied_statutes"] = [_multi_app()]
    ok, _ = A.answer_gate(r, selected_judgement_id="JX", citations=[_cit(), MCIT])
    assert not ok  # same reason: pairing unverifiable without nodes
    ok, failures = gate(good_response())
    assert ok, failures


def test_s4_multi_outer_regate_still_catches_forgery():
    # b4b outer re-gate (nodes=None) still enforces membership: a forged
    # citation never survives, so sectioned/synthesis cannot launder it.
    bad = _multi_app(citation_id="cit:JX#c9:0-1")
    ok, failures = _multi_reg(bad, nodes=None)
    assert not ok and any("A11" in f and "not in response" in f for f in failures)
