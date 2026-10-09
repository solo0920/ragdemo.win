"""B3-D link tests: reviewed fixture equality + status/eligibility contract."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import b3d_attribution as D  # noqa: E402

FX = json.loads((ROOT / "tests/fixtures/b3d_attribution_set.json").read_text(encoding="utf-8"))


def test_fixture_relations_recompute_exactly():
    assert len(FX["cases"]) == 9
    for case in FX["cases"]:
        nodes = [{"evidence_id": n["evidence_id"],
                  "evidence_type": n["evidence_type"],
                  "judgement_id": case["judgement_id"],
                  "document_id": case["judgement_id"],
                  "chunk_id": case["judgement_id"] + "#c",
                  "text": "",
                  "source_span": n["source_span"],
                  "provenance": {}} for n in case["nodes"]]
        # attribution map keyed by recorded evidence ids
        amap = dict(case["attribution_map"])
        by_id = {n["evidence_id"]: n for n in nodes}
        # rebuild node texts is unnecessary: link needs node text only via
        # spans, which are recorded; text containment uses evidence_text
        # which the fixture records inside citation. Recompute directly:
        nodes2 = []
        for n in case["nodes"]:
            nodes2.append({**by_id[n["evidence_id"]],
                           "text": case["chunk_text"][
                               n["source_span"]["start"]:n["source_span"]["end"]]})
        cit = dict(case["citation"], judgement_id=case["judgement_id"],
                   chunk_id=case["judgement_id"] + "#chunk0")
        rel = D.link_citation(cit, nodes2, amap if amap else None)
        want = case["relation"]
        assert rel["attribution"] == want["attribution"], case["case_id"]
        assert rel["attribution_rule_id"] == want["attribution_rule_id"], case["case_id"]
        assert rel["covering_node_id"] == want["covering_node_id"], case["case_id"]
        assert rel["downstream_eligibility"] == want["downstream_eligibility"], case["case_id"]


def test_status_eligibility_mapping():
    for status, used, display in [
            (D.COURT_VOICE_EXPLICIT_CITATION, True, "court_citation"),
            (D.PARTY_ATTRIBUTION, False, "party_citation"),
            (D.PROSECUTOR_OR_INDICTMENT_ATTRIBUTION, False, "prosecution_citation"),
            (D.QUOTED_STATUTE, False, "quoted_statute"),
            (D.QUOTED_OTHER_JUDGMENT, False, "quoted_judgment_citation"),
            (D.UNRESOLVED_ATTRIBUTION, False, "mention_only")]:
        assert status in D.STATUSES
        assert D._DISPLAY[status] == display
        assert (status == D.COURT_VOICE_EXPLICIT_CITATION) == used
    assert set(D.STATUSES) == {
        D.COURT_VOICE_EXPLICIT_CITATION, D.PARTY_ATTRIBUTION,
        D.PROSECUTOR_OR_INDICTMENT_ATTRIBUTION, D.QUOTED_STATUTE,
        D.QUOTED_OTHER_JUDGMENT, D.UNRESOLVED_ATTRIBUTION}


def test_no_applied_decisive_status_exists():
    assert "APPLIED" not in D.STATUSES and "DECISIVE" not in D.STATUSES
    assert set(D.STATUSES) == {
        D.COURT_VOICE_EXPLICIT_CITATION, D.PARTY_ATTRIBUTION,
        D.PROSECUTOR_OR_INDICTMENT_ATTRIBUTION, D.QUOTED_STATUTE,
        D.QUOTED_OTHER_JUDGMENT, D.UNRESOLVED_ATTRIBUTION}
    for case in FX["cases"]:
        assert "APPLIED" not in case["relation"]["attribution"]
        assert "DECISIVE" not in case["relation"]["attribution"]


def test_sentence_override_beats_covering_node():
    # Minimal mechanical fixture (shapes only, not real law): a quoted
    # statute sentence inside a covered COURT node must not be court-used.
    node = {"evidence_id": "d:J#C1:0-50", "evidence_type": "DISPOSITION",
            "judgement_id": "J", "document_id": "J", "chunk_id": "J#C1",
            "text": "依甲法第1條規定：「第一條條文內容在此」。",
            "source_span": {"start": 0, "end": 50, "unit": "code_point",
                            "basis": "chunk_text"},
            "provenance": {}}
    cit = {"citation_id": "cit:J#c0:2-8", "judgement_id": "J",
           "chunk_id": "J#chunk0", "raw_text": "甲法第1條",
           "normalized": {"law_name": "甲法", "article": "第1條"},
           "source_span": {"start": 2, "end": 8, "unit": "code_point",
                           "basis": "chunk_text"},
           "resolution_status": "RESOLVED", "evidence_id": "st:X#1",
           "evidence_text": "依甲法第1條規定：「第一條條文內容在此」。",
           "statute_evidence": None}
    rel = D.link_citation(cit, [node], {"d:J#C1:0-50": "COURT_VOICE"})
    assert rel["attribution"] == D.QUOTED_STATUTE
    assert rel["downstream_eligibility"]["court_used"] is False


def test_unknown_sentence_never_overrides_cover():
    node = {"evidence_id": "d:J#C1:0-30", "evidence_type": "REASONING",
            "judgement_id": "J", "document_id": "J", "chunk_id": "J#C1",
            "text": "查本案事實明確，依甲法第2條處理。",
            "source_span": {"start": 0, "end": 30, "unit": "code_point",
                            "basis": "chunk_text"},
            "provenance": {}}
    cit = {"citation_id": "cit:J#c0:14-20", "judgement_id": "J",
           "chunk_id": "J#chunk0", "raw_text": "甲法第2條",
           "normalized": {"law_name": "甲法", "article": "第2條"},
           "source_span": {"start": 14, "end": 20, "unit": "code_point",
                           "basis": "chunk_text"},
           "resolution_status": "RESOLVED", "evidence_id": "st:X#2",
           "evidence_text": "查本案事實明確，依甲法第2條處理。",
           "statute_evidence": None}
    rel = D.link_citation(cit, [node], {"d:J#C1:0-30": "COURT_VOICE"})
    assert rel["attribution"] == D.COURT_VOICE_EXPLICIT_CITATION


def test_span_mismatch_yields_unresolved():
    cit = {"citation_id": "cit:J#c0:90-99", "judgement_id": "J",
           "chunk_id": "J#chunk0", "raw_text": "甲法第3條",
           "normalized": {"law_name": "甲法", "article": "第3條"},
           "source_span": {"start": 90, "end": 99, "unit": "code_point",
                           "basis": "chunk_text"},
           "resolution_status": "RESOLVED", "evidence_id": None,
           "evidence_text": "查本案依甲法第3條處理。",
           "statute_evidence": None}
    node = {"evidence_id": "d:J#C1:0-10", "evidence_type": "REASONING",
            "judgement_id": "J", "document_id": "J", "chunk_id": "J#C1",
            "text": "查本案依甲法第3條處理。",
            "source_span": {"start": 0, "end": 10, "unit": "code_point",
                            "basis": "chunk_text"},
            "provenance": {}}
    rel = D.link_citation(cit, [node], {"d:J#C1:0-10": "COURT_VOICE"})
    assert rel["attribution"] == D.UNRESOLVED_ATTRIBUTION
    assert rel["covering_node_id"] is None
