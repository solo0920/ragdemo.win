#!/usr/bin/env python3
"""One-off B3-B reviewed-set builder (run by the batch author, then frozen).

Real cases only: B2-B verified citations x real-or-absent JDATEs. Expected
statuses are the author's verified judgments (CURRENT_ONLY where records
exist without history; UNKNOWN where dates are missing). HISTORICAL_VERIFIED
/ TEMPORALLY_INVALID appear ONLY in unit tests with clearly-labeled synthetic
intervals — never in this reviewed set (§17). Tests assert exact equality.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))

from app import b3b_temporal as T  # noqa: E402
from app import b1_serve as B1  # noqa: E402

corpus = B1.StatuteCorpus.from_jsonl()
meta = T.load_law_meta()
sync = T.corpus_sync_record()


def row_of(law, article):
    for r in corpus.rows.values():
        if r["law_name"] == law and r["article_no"].replace(" ", "") == article:
            return r
    return None


def cit(cid, law, article):
    return {"citation_id": cid, "citation_type": "EXPLICIT_CITATION",
            "judgement_id": "J", "raw_text": f"{law}{article}",
            "normalized": {"law_name": law, "article": article},
            "resolution_status": "RESOLVED"}


CASES = [
    # Real JDATE from the B1 demo fixture (verified 20260713).
    ("T-01", "STEV,115,店小,450,20260713,1", "20260713",
     cit("cit:demo#c0:1-2", "民法", "第184條"), "CURRENT_ONLY"),
    ("T-02", "STEV,115,店小,450,20260713,1", "20260713",
     cit("cit:demo#c0:3-4", "民法", "第195條"), "CURRENT_ONLY"),
    # Dateless (corpus records carry no JDATE): UNKNOWN by rule.
    ("T-03", "CHDM,115,簡,1769,20260730,1", "",
     cit("cit:x#c0:1-2", "家庭暴力防治法", "第3條"), "TEMPORALLY_UNKNOWN"),
    ("T-04", "CHDM,115,簡,1769,20260730,1", "2026-13-40",
     cit("cit:x#c0:1-2", "家庭暴力防治法", "第3條"), "TEMPORALLY_UNKNOWN"),
    # Citation without a resolvable row.
    ("T-05", "JX", "20260713",
     {**cit("cit:x#c0:1-2", "民法", "第9999條"), "resolution_status": "UNRESOLVED:no_corpus_match"},
     "TEMPORALLY_UNKNOWN"),
]
out = {"verification_status": "AUTHOR_REVIEWED",
       "provenance": "real B2-B citations x real/absent JDATEs; expected = reviewed rule outcomes",
       "cases": []}
for case_id, jid, jdate, c, expected in CASES:
    law = c["normalized"]["law_name"]
    art = c["normalized"]["article"]
    found = None
    for r in corpus.rows.values():
        if r["law_name"] == law and r["article_no"].replace(" ", "") == art:
            found = r
            break
    rec = T.validate_temporal(judgement_id=jid, jdate=jdate, citation=c,
                              statute_row=found, law_meta=meta.get(law, {}),
                              sync_record=sync)
    assert rec["status"] == expected, (case_id, rec["status"], expected)
    out["cases"].append({"case_id": case_id, "judgement_id": jid, "jdate": jdate,
                         "citation": {k: c[k] for k in ("citation_id", "raw_text", "resolution_status")},
                         "normalized": c["normalized"],
                         "row_ref": f"{found['pcode']}#{found['article_seq']}" if found else None,
                         "law_meta_present": law in meta,
                         "expected_status": expected, "record": rec})
p = REPO / "tests/fixtures/b3b_temporal_set.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {p} ({len(out['cases'])} cases) — REVIEWED")
