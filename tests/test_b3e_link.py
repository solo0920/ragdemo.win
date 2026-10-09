"""B3-E link tests: holding-containment-v1 rule + frozen reviewed labels.

Hermetic: demo-text cases recompute against a tiny tmp corpus (never the
gitignored data/laws); real-chunk cases pin their reviewed labels only
(their source passages were human-audited against the frozen corpus).
No LLM, no similarity, no synthetic gold labels for law.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3e_applied as E3  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b3a_attribution as A3  # noqa: E402
from app import b3d_attribution as D  # noqa: E402
from app import b1_serve as B1  # noqa: E402

FIX = json.loads((ROOT / "tests/fixtures/b3e_applied_set.json").read_text(encoding="utf-8"))

# Reviewed gold: (relation_type, reason), audited against source passages.
EXPECTED = {
    "A-01-applied-184": ("COURT_EXPLICIT_APPLICATION", "citation-inside-conclusory-holding"),
    "A-02-applied-195": ("COURT_EXPLICIT_APPLICATION", "citation-inside-conclusory-holding"),
    "A-03-mention-not-applied": ("APPLICATION_UNRESOLVED", "covering-not-holding"),
    "A-04-party-17-1": ("APPLICATION_UNRESOLVED", "not-court-used"),
    "A-05-prosecutor-21-1": ("APPLICATION_UNRESOLVED", "not-court-used"),
    "A-06-appendix-339-4": ("APPLICATION_UNRESOLVED", "not-court-used"),
    "A-07-unresolved-436-18": ("APPLICATION_UNRESOLVED", "not-court-used"),
    "A-08-costs-78": ("APPLICATION_UNRESOLVED", "not-court-used"),
    "A-09-cross-chunk": ("APPLICATION_UNRESOLVED", "not-court-used"),
    "A-10-detention-conclusion": ("APPLICATION_UNRESOLVED", "not-court-used"),
}

JID = "STEV,115,店小,450,20260713,1"

MINI_ROWS = [
    {"law_name": "民法", "article_no": "第 184 條", "pcode": "TEST-MIN",
     "article_seq": 1, "article_content": "因故意或過失，不法侵害他人之權利者，負損害賠償責任。"},
    {"law_name": "民法", "article_no": "第 195 條", "pcode": "TEST-MIN",
     "article_seq": 2, "article_content": "不法侵害他人之身體、健康、名譽、自由者，被害人得請求賠償。"},
    {"law_name": "民事訴訟法", "article_no": "第 436-18 條", "pcode": "TEST-CIVIL",
     "article_seq": 1, "article_content": "小額訴訟程序，僅記載主文及理由要領。"},
    {"law_name": "民事訴訟法", "article_no": "第 78 條", "pcode": "TEST-CIVIL",
     "article_seq": 2, "article_content": "訴訟費用，由敗訴之當事人負擔。"},
]


def _mini_corpus(tmp_path):
    p = tmp_path / "mini_laws.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in MINI_ROWS),
                 encoding="utf-8")
    return B1.StatuteCorpus.from_jsonl(p)


def _recompute(text, needle, corpus, label):
    chunk_id = f"{JID}#demo-{label}"
    ch = {"chunk_id": chunk_id, "document_id": JID, "structural_role": "FACTS",
          "structural_labels": ["FACTS"],
          "carries_declared_disposition_unit": False, "text": text, "span": {}}
    cites = C.extract_citations(text, jid=JID, chunk_index=0, corpus=corpus)
    c = next(x for x in cites if x["raw_text"] == needle)
    g = E.build_evidence_graph([ch], cites)
    amap = {}
    for n in g["nodes"]:
        s, e = n["source_span"]["start"], n["source_span"]["end"]
        amap[n["evidence_id"]] = A3.classify_attribution(
            n["text"], context_before=text[max(0, s - 400):s],
            context_after=text[e:e + 400])["attribution"]
    b3d = D.link_all(cites, g["nodes"], amap)
    rel = E3.link_application(c, {n["evidence_id"]: n for n in g["nodes"]},
                              amap, {r["citation_id"]: r for r in b3d})
    return rel


def _by_id(case_id):
    return next(c for c in FIX["cases"] if c["case_id"] == case_id)


def test_fixture_labels_pinned():
    assert FIX["verification_status"] == "AUTHOR_REVIEWED"
    assert set(EXPECTED) == {c["case_id"] for c in FIX["cases"]}
    for c in FIX["cases"]:
        want_type, want_reason = EXPECTED[c["case_id"]]
        assert c["relation"]["relation_type"] == want_type, c["case_id"]
        assert c["relation"]["reason"] == want_reason, c["case_id"]
        assert c["relation"]["rule_id"] == E3.RULE_ID, c["case_id"]


def test_false_application_count_is_zero():
    applied = [c["case_id"] for c in FIX["cases"]
               if c["relation"]["relation_type"] == "COURT_EXPLICIT_APPLICATION"]
    assert sorted(applied) == ["A-01-applied-184", "A-02-applied-195"]


def test_recompute_demo_applied(tmp_path):
    corpus = _mini_corpus(tmp_path)
    for case_id, needle in (("A-01-applied-184", "民法第184條第1項"),
                            ("A-02-applied-195", "第195條第1項")):
        stored = _by_id(case_id)
        rel = _recompute(stored["chunk_text"], needle, corpus, case_id)
        assert rel["relation_type"] == "COURT_EXPLICIT_APPLICATION", case_id
        assert rel["reason"] == "citation-inside-conclusory-holding", case_id
        ev = rel["application_evidence"]
        assert ev["attribution"] == "COURT_VOICE"
        assert ev["evidence_type"] == "HOLDING"
        assert needle[:6] in ev["verbatim_text"] or "民法" in ev["verbatim_text"]
        assert ev["verbatim_text"] in stored["chunk_text"]


def test_recompute_demo_unresolved(tmp_path):
    corpus = _mini_corpus(tmp_path)
    for case_id, needle in (("A-07-unresolved-436-18", "民事訴訟法第436條之18第1項"),
                            ("A-08-costs-78", "民事訴訟法第78條")):
        stored = _by_id(case_id)
        rel = _recompute(stored["chunk_text"], needle, corpus, case_id)
        assert rel["relation_type"] == "APPLICATION_UNRESOLVED", case_id
        assert rel["application_evidence"] is None, case_id


def _holding_node(evid="holding:J#c0:10-40", method="conclusory_pattern",
                  etype="HOLDING", chunk="J#c0", multi=False):
    prov = {"method": method, "marker": "為無理由", "heuristic": True}
    if multi:
        prov["multi_chunk"] = True
    return {"evidence_id": evid, "evidence_type": etype, "judgement_id": "J",
            "chunk_id": chunk, "text": "原告依民法第184條之規定請求，為無理由，應予駁回。",
            "source_span": {"start": 10, "end": 40, "unit": "code_point",
                            "basis": "chunk_text"},
            "provenance": prov}


def _cit(cid="cit:J#c0:12-19"):
    return {"citation_id": cid, "judgement_id": "J", "chunk_id": "J#c0",
            "source_span": {"start": 12, "end": 19}, "raw_text": "民法第184條"}


def _court_used(cid="cit:J#c0:12-19", node="holding:J#c0:10-40"):
    return {cid: {"citation_id": cid, "attribution": "COURT_VOICE_EXPLICIT_CITATION",
                  "covering_node_id": node}}


def test_unit_applied_needs_all_four():
    node = _holding_node()
    by_id = {node["evidence_id"]: node}
    amap = {node["evidence_id"]: "COURT_VOICE"}
    rel = E3.link_application(_cit(), by_id, amap, _court_used())
    assert rel["relation_type"] == "COURT_EXPLICIT_APPLICATION"
    assert rel["relation_id"].startswith("applied:")
    # Deterministic: same inputs reproduce the same relation_id.
    rel2 = E3.link_application(_cit(), by_id, amap, _court_used())
    assert rel2["relation_id"] == rel["relation_id"]
    assert rel["application_evidence"]["evidence_id"] == node["evidence_id"]
    assert rel["application_evidence"]["chunk_ids"] == ["J#c0"]
    assert len(rel["application_evidence"]["source_spans"]) == 1


def test_unit_non_court_speakers_rejected():
    node = _holding_node()
    by_id = {node["evidence_id"]: node}
    amap = {node["evidence_id"]: "COURT_VOICE"}
    for status in ("PARTY_ATTRIBUTION", "PROSECUTOR_OR_INDICTMENT_ATTRIBUTION",
                   "QUOTED_STATUTE", "QUOTED_OTHER_JUDGMENT",
                   "UNRESOLVED_ATTRIBUTION"):
        rels = {"cit:J#c0:12-19": {"citation_id": "cit:J#c0:12-19",
                                   "attribution": status,
                                   "covering_node_id": node["evidence_id"]}}
        rel = E3.link_application(_cit(), by_id, amap, rels)
        assert rel["relation_type"] == "APPLICATION_UNRESOLVED", status
        assert rel["reason"] == "not-court-used", status


def test_unit_missing_relation_and_node_fail_closed():
    node = _holding_node()
    by_id = {node["evidence_id"]: node}
    amap = {node["evidence_id"]: "COURT_VOICE"}
    assert E3.link_application(_cit(), by_id, amap, {})["reason"] == "not-court-used"
    rels = _court_used(node="holding:MISSING")
    assert E3.link_application(_cit(), by_id, amap, rels)["reason"] == "no-covering-node"


def test_unit_covering_must_be_conclusory_holding():
    cit, rels = _cit(), _court_used()
    amap = {"holding:J#c0:10-40": "COURT_VOICE", "reasoning:J#c0:10-40": "COURT_VOICE",
            "disposition:J#c0:10-40": "COURT_VOICE"}
    rea = {"evidence_id": "reasoning:J#c0:10-40", "evidence_type": "REASONING",
           "judgement_id": "J", "chunk_id": "J#c0", "text": "查原告未受損害。",
           "source_span": {"start": 10, "end": 40}, "provenance": {"method": "court_voice_marker"}}
    rels_rea = {"cit:J#c0:12-19": {"citation_id": "cit:J#c0:12-19",
                                   "attribution": "COURT_VOICE_EXPLICIT_CITATION",
                                   "covering_node_id": "reasoning:J#c0:10-40"}}
    assert E3.link_application(cit, {"reasoning:J#c0:10-40": rea}, amap, rels_rea)["reason"] == "covering-not-holding"
    plain = _holding_node(method="court_voice_marker")
    assert E3.link_application(cit, {plain["evidence_id"]: plain}, amap, rels)["reason"] == "holding-not-conclusory"
    disp = _holding_node(evid="disposition:J#c0:10-40", method="zhu_wen_marker")
    disp["evidence_type"] = "DISPOSITION"
    rels_d = {"cit:J#c0:12-19": {"citation_id": "cit:J#c0:12-19",
                                 "attribution": "COURT_VOICE_EXPLICIT_CITATION",
                                 "covering_node_id": "disposition:J#c0:10-40"}}
    assert E3.link_application(cit, {"disposition:J#c0:10-40": disp}, amap, rels_d)["reason"] == "covering-not-holding"


def test_unit_multi_chunk_excluded_and_attribution_fail_closed():
    cit, rels = _cit(), _court_used()
    multi = _holding_node(multi=True)
    amap = {multi["evidence_id"]: "COURT_VOICE"}
    r = E3.link_application(cit, {multi["evidence_id"]: multi}, amap, rels)
    assert (r["relation_type"], r["reason"]) == (
        "APPLICATION_UNRESOLVED", "multi-chunk-application-unsupported")
    node = _holding_node()
    by_id = {node["evidence_id"]: node}
    assert E3.link_application(cit, by_id, {}, rels)["reason"] == "node-attribution-not-court"
    assert E3.link_application(
        cit, by_id, {node["evidence_id"]: "PARTY_VOICE"}, rels)["reason"] == "node-attribution-not-court"


def test_unit_never_raises_on_shape():
    assert E3.link_application({}, {}, None, {})["relation_type"] == "APPLICATION_UNRESOLVED"
    assert E3.link_application(_cit(), {}, None, {})["relation_type"] == "APPLICATION_UNRESOLVED"
    out = E3.link_all([_cit()], [], None, [])
    assert out[0]["relation_type"] == "APPLICATION_UNRESOLVED"


def test_unit_citations_independent_no_suppression():
    node = _holding_node()
    by_id = {node["evidence_id"]: node}
    amap = {node["evidence_id"]: "COURT_VOICE"}
    c1, c2 = _cit("cit:J#c0:12-19"), _cit("cit:J#c0:20-27")
    rels = [_court_used("cit:J#c0:12-19")[k] for k in _court_used("cit:J#c0:12-19")]
    rmap = {"cit:J#c0:12-19": {"citation_id": "cit:J#c0:12-19",
                               "attribution": "COURT_VOICE_EXPLICIT_CITATION",
                               "covering_node_id": node["evidence_id"]},
            "cit:J#c0:20-27": {"citation_id": "cit:J#c0:20-27",
                               "attribution": "COURT_VOICE_EXPLICIT_CITATION",
                               "covering_node_id": node["evidence_id"]}}
    out = E3.link_all([c1, c2], [node], amap,
                      [{"citation_id": k, **v} for k, v in rmap.items()])
    assert [r["relation_type"] for r in out] == ["COURT_EXPLICIT_APPLICATION"] * 2
    assert out[0]["relation_id"] != out[1]["relation_id"]


def test_unit_citation_identity_preserved_for_temporal():
    # Temporal keys on citation/statute evidence IDs: B3-E must not rename them.
    node = _holding_node()
    rel = E3.link_application(_cit(), {node["evidence_id"]: node},
                              {node["evidence_id"]: "COURT_VOICE"}, _court_used())
    assert rel["citation_id"] == "cit:J#c0:12-19"
    assert rel["citation_evidence"]["raw_text"] == "民法第184條"
    assert rel["citation_evidence"]["source_span"] == {"start": 12, "end": 19}
    assert rel["application_evidence"]["evidence_id"] == node["evidence_id"]
