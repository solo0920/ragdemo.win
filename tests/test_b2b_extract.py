"""B2-B extraction tests: frozen verified-set equality + rule units."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

SET = json.loads((ROOT / "tests/fixtures/b2b_citation_set.json").read_text(encoding="utf-8"))
CORPUS = B1.StatuteCorpus.from_jsonl()


def _run(case):
    xd = {"usable": case["xiachen_usable"], "raw": []}
    return C.extract_citations(case["chunk_text"], jid=case["judgement_id"],
                               chunk_index=case["chunk_index"], corpus=CORPUS,
                               xiachen=xd)


def _slim(cites):
    return [{k: c[k] for k in ("citation_id", "citation_type", "raw_text", "normalized",
                               "source_span", "law_source", "scoped", "scoped_from",
                               "resolution_status", "evidence_id", "surface_collapsed")}
            for c in cites]


def test_frozen_verified_set_matches_exactly():
    for case in SET["cases"]:
        got = _slim(_run(case))
        want = case["expected_citations"]
        assert got == want, f"{case['case_id']}: extraction drifted from verified expectations"


def test_spans_resolve_into_chunk_text():
    for case in SET["cases"]:
        for c in _run(case):
            assert case["chunk_text"][c["source_span"]["start"]:c["source_span"]["end"]].replace(
                " ", "").replace("\r", "").replace("\n", "").replace("　", "") != ""


def test_only_explicit_citation_type_emitted():
    for case in SET["cases"]:
        for c in _run(case):
            assert c["citation_type"] == "EXPLICIT_CITATION"


def test_multiline_wrap_and_branch_and_scoping_shapes():
    by_id = {c["case_id"]: c for c in SET["cases"]}
    v08 = {x["raw_text"]: x for x in by_id["V-08-demo-procedure"]["expected_citations"]}
    assert v08["民事訴訟法第255條第1項"]["resolution_status"] == "RESOLVED"
    assert v08["第3款"]["normalized"]["article"] == "第255條"  # nearest, not 之23
    v10 = by_id["V-10-demo-zhi18"]["expected_citations"][0]
    assert v10["normalized"]["article"] == "第436條之18"
    v04 = {x["raw_text"]: x for x in by_id["V-04-fraud-appendix"]["expected_citations"]}
    assert v04["中華民國刑法第339條之4"]["resolution_status"] == "RESOLVED"


def test_xiachen_definition_without_corpus_full_name_stays_unusable():
    v11 = [c for c in SET["cases"] if c["case_id"] == "V-11-demo-xiachen-unusable"][0]
    assert v11["xiachen_usable"] == {}
    assert v11["expected_citations"] == []
    defs = C.extract_xiachen_definitions(v11["chunk_text"], CORPUS)
    assert any(d["short"] == "選罷法" and not d["usable"] for d in defs["raw"])
