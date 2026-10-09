"""B1 unit: evidence objects reuse the authority model and backtrace to source."""
from __future__ import annotations

import sys
from pathlib import Path

import b1_helpers as H
from app import b1_serve as B1
from app import retrieve

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))


def test_judgement_evidence_backtraces_to_jfull():
    doc, chunks = H.real_chunks()
    pt_hits = H.real_hits(doc, chunks)
    fx = H.load_case()
    jfull = fx["document"]["JFULL"]
    for h in pt_hits[:3]:
        view = retrieve.judgment_hit_view(h)
        quote = jfull[view["start_offset"]:view["end_offset"]]
        assert quote  # non-empty by construction
        ev = B1.make_judgement_evidence(view, quote, "CIT", h["score"])
        assert ev["evidence_type"] == "judgement"
        assert ev["judgement_number"] == fx["document"]["JID"]
        assert ev["judgement_date"] == fx["document"]["JDATE"]
        assert ev["source_id"] == fx["entry_path"]
        assert ev["text"] == jfull[ev["provenance"]["start_offset"]:ev["provenance"]["end_offset"]]
        assert ev["court"] is None  # FR-016: never inferred


def test_statute_evidence_from_real_corpus_row():
    corpus = H.real_corpus()
    row = corpus.find("民法", "第184條")
    assert row is not None
    m = B1.StatuteMention(law_name="民法", article="第184條", detail="第1項", surface="民法第184條第1項")
    ev = B1.make_statute_evidence(row, m)
    assert ev["evidence_type"] == "statute"
    assert ev["law_name"] == "民法" and ev["article"] == "第184條"
    assert ev["paragraph"] == "第1項"
    assert ev["text"].startswith("因故意或過失")
    assert ev["citation"] == "民法第184條"


def test_evidence_ids_deterministic():
    doc, chunks = H.real_chunks()
    hits = H.real_hits(doc, chunks)
    view = retrieve.judgment_hit_view(hits[0])
    e1 = B1.make_judgement_evidence(view, "x", "c", 0.9)
    e2 = B1.make_judgement_evidence(view, "x", "c", 0.9)
    assert e1["evidence_id"] == e2["evidence_id"]
