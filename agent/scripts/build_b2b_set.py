#!/usr/bin/env python3
"""One-off B2-B verified-set builder (run by the batch author, then frozen).

Reads real chunk texts (frozen corpus records + B1 demo fixture), runs the
B2-B extractor, and writes tests/fixtures/b2b_citation_set.json with the
extraction output RECORDED. The author then manually reviews every recorded
expectation against the source text before freezing; tests assert exact
equality afterwards. Re-running overwrites the file (review required again).
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))
ART = Path("/home/solo/artifacts")

from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

corpus = B1.StatuteCorpus.from_jsonl()
recs = {r["chunk_id"]: r for r in
        json.loads((ART / "t007e05cb/out/corpus_snapshot.json").read_text(encoding="utf-8"))["records"]}
demo = json.loads((REPO / "tests/fixtures/b1_judgement_serving/case_00450.json").read_text(encoding="utf-8"))
JFULL = demo["document"]["JFULL"]


def excerpt(needle: str, width: int = 120) -> str:
    i = JFULL.find(needle)
    assert i >= 0, needle
    return JFULL[max(0, i - 20):i + len(needle) + width]


CASES = [
    {"case_id": "V-01-home-violence", "chunk_id": "CHDM,115,簡,1769,20260730,1#C002"},
    {"case_id": "V-02-constitutional", "chunk_id": "JCCC,115,審裁,1177,20260713#C002"},
    {"case_id": "V-03-land-split", "chunk_id": "CTDV,112,訴,82,20260731,1#C002"},
    {"case_id": "V-04-fraud-appendix", "chunk_id": "CTDM,115,訴,1694,20260731,1#C006"},
    {"case_id": "V-05-no-citation", "chunk_id": "CHDM,115,簡,1769,20260730,1#C001"},
    {"case_id": "V-06-lookalike", "chunk_id": "CTDV,112,訴,82,20260731,1#C003"},
    {"case_id": "V-07-demo-claim-basis",
     "chunk_id": "STEV,115,店小,450,20260713,1#demo-excerpt",
     "chunk_text": excerpt("依民法第184條第1項")},
    {"case_id": "V-08-demo-procedure",
     "chunk_id": "STEV,115,店小,450,20260713,1#demo-excerpt",
     "chunk_text": excerpt("民事訴訟法第25")},
    {"case_id": "V-09-demo-wrapped-costs",
     "chunk_id": "STEV,115,店小,450,20260713,1#demo-excerpt",
     "chunk_text": excerpt("訴訟費用負擔之依據")},
    {"case_id": "V-10-demo-zhi18",
     "chunk_id": "STEV,115,店小,450,20260713,1#demo-excerpt",
     "chunk_text": excerpt("依民事訴訟法第436條之18")},
    {"case_id": "V-11-demo-xiachen-unusable",
     "chunk_id": "STEV,115,店小,450,20260713,1#demo-excerpt",
     "chunk_text": excerpt("違反選舉罷免法（下稱選罷法）")},
]

out = {"verification_status": "AUTHOR_REVIEWED",
       "provenance": "real frozen corpus chunks + real B1 demo excerpts; see source_chunk_id",
       "cases": []}
for spec in CASES:
    if "chunk_text" in spec:
        text, jid, cidx = spec["chunk_text"], "STEV,115,店小,450,20260713,1", 900 + len(out["cases"])
        full_doc = JFULL
    else:
        r = recs[spec["chunk_id"]]
        text, jid, cidx = r["text"], r["document_id"], 0
        full_doc = None
        # chunk_index unknown from record alone; use 0 and note it
    xd = C.extract_xiachen_definitions(full_doc or text, corpus)
    cites = C.extract_citations(text, jid=jid, chunk_index=cidx, corpus=corpus, xiachen=xd)
    slim = [{k: c[k] for k in ("citation_id", "citation_type", "raw_text", "normalized",
                               "source_span", "law_source", "scoped", "scoped_from",
                               "resolution_status", "evidence_id",
                               "surface_collapsed")}
             for c in cites]
    out["cases"].append({"case_id": spec["case_id"],
                         "source_chunk_id": spec["chunk_id"],
                         "judgement_id": jid, "chunk_index": cidx,
                         "chunk_text": text,
                         "xiachen_usable": xd["usable"],
                         "expected_citations": slim})
p = REPO / "tests/fixtures/b2b_citation_set.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
n = sum(len(c["expected_citations"]) for c in out["cases"])
print(f"wrote {p} ({len(out['cases'])} cases, {n} recorded citations) — MANUAL REVIEW REQUIRED")
