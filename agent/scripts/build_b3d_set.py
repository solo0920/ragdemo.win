#!/usr/bin/env python3
"""One-off B3-D reviewed-set builder (run by the batch author, then frozen).

Hand-picked real cases only: chunk + citation selector + node source. Records
link outputs. The author reads every recorded row against source text before
freezing; tests assert exact equality afterwards. Re-running overwrites
(review required again). No synthetic legal citations.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))
ART = Path("/home/solo/artifacts")

from app import b3d_attribution as D  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b1_serve as B1  # noqa: E402

corpus = B1.StatuteCorpus.from_jsonl()
recs = {r["chunk_id"]: r for r in
        json.loads((ART / "t007e05cb/out/corpus_snapshot.json").read_text(encoding="utf-8"))["records"]}


def chunk_dict(cid):
    r = recs[cid]
    return {"chunk_id": cid, "document_id": r["document_id"],
            "structural_role": r["structural_role"],
            "structural_labels": r.get("structural_labels", []),
            "carries_declared_disposition_unit": r.get(
                "carries_declared_disposition_unit", False),
            "text": r["text"], "span": r.get("span", {})}


def graph_for(cid):
    ch = chunk_dict(cid)
    cites = C.extract_citations(ch["text"], jid=ch["document_id"],
                                chunk_index=0, corpus=corpus)
    g = E.build_evidence_graph([ch], cites)
    return ch, cites, g["nodes"]


def amap_for(nodes, text, cid):
    from app import b3a_attribution as A3
    out = {}
    for n in nodes:
        s, e = n["source_span"]["start"], n["source_span"]["end"]
        out[n["evidence_id"]] = A3.classify_attribution(
            n["text"], context_before=text[max(0, s - 400):s],
            context_after=text[e:e + 400])["attribution"]
    return out


# (case_id, chunk_id, raw_text needle, note)
SPECS = [
    ("D-01-unresolved-436-18", "STEV?", "民事訴訟法第436條之18第1項",
     "the audited exclusion; B2-C demo excerpt path"),
    ("D-02-court-184", "R07", "民法第184條第1項", "covered court citation"),
    ("D-03-court-195-scoped", "R07", "第195條第1項", "scoped court citation"),
    ("D-04-party-17-1", "KSHM,115,上訴,322,20260723,1#C004",
     "毒品危害防制條例第17條第1項", "party-framed (上訴意旨主張)"),
    ("D-05-prosecutor-21-1", "TPHM,115,上訴,2702,20260716,1#C011",
     "洗錢防制法第21條第1項", "prosecutorial voice"),
    ("D-06-quoted-statute", "CTDM,115,訴,1694,20260731,1#C006",
     "中華民國刑法第339條之4", "appendix reproduction"),
    ("D-07-boundary-80-1", "CTDV,112,訴,82,20260731,1#C011",
     "民事訴訟法第80條之1", "3 chars from chunk end"),
    ("D-08-override-pcdv", "PCDV,115,司促,17332,20260713,1#C001",
     "公司法第208條第3項",
     "sentence override beats flagged full-chunk node"),
    ("D-08b-override-pcdv-508", "PCDV,115,司促,17332,20260713,1#C001",
     "民事訴訟法第五百零八條",
     "sentence override, second citation same chunk"),
]

DEMO_FIX = json.loads((REPO / "tests/fixtures/b1_judgement_serving/case_00450.json")
                      .read_text(encoding="utf-8"))["document"]["JFULL"]
R06 = DEMO_FIX[DEMO_FIX.find("理由要領") - 60:DEMO_FIX.find("理由要領") + 340]
R07A = DEMO_FIX[DEMO_FIX.find("原告並未受有實際損害") - 40:DEMO_FIX.find("原告並未受有實際損害") + 260]
R07B = DEMO_FIX[DEMO_FIX.find("綜上所述，原告依民法") - 30:DEMO_FIX.find("綜上所述，原告依民法") + 300]

out = {"verification_status": "AUTHOR_REVIEWED",
       "provenance": "real frozen corpus chunks + real B1 demo excerpts; expectations reviewed by author",
       "cases": []}
for case_id, cid, needle, note in SPECS:
    if cid == "STEV?":
        text, jid, cidx = R06, "STEV,115,店小,450,20260713,1", 900
    elif cid == "R07":
        text, jid, cidx = (R07A if "184" in needle else R07B), \
            "STEV,115,店小,450,20260713,1", 901
    else:
        r = recs[cid]
        text, jid, cidx = r["text"], r["document_id"], 1
    cites = C.extract_citations(text, jid=jid, chunk_index=cidx, corpus=corpus)
    matches = [c for c in cites if c["raw_text"] == needle]
    assert matches, (case_id, needle)
    c = matches[0]
    # nodes: real B2-C graph. Real corpus chunks keep their true dict
    # (role/labels/flag/span); demo excerpts use a neutral FACTS dict.
    if cid in ("STEV?", "R07"):
        ch = {"chunk_id": f"{jid}#b3d", "document_id": jid,
              "structural_role": "FACTS", "structural_labels": ["FACTS"],
              "carries_declared_disposition_unit": False,
              "text": text, "span": {}}
    else:
        r = recs[cid]
        ch = {"chunk_id": r["chunk_id"], "document_id": r["document_id"],
              "structural_role": r["structural_role"],
              "structural_labels": r.get("structural_labels", []),
              "carries_declared_disposition_unit": r.get(
                  "carries_declared_disposition_unit", False),
              "text": r["text"], "span": r.get("span", {})}
    g = E.build_evidence_graph([ch], cites)
    amap = amap_for(g["nodes"], text, ch["chunk_id"])
    rel = D.link_citation(c, g["nodes"], amap if amap else None)
    out["cases"].append({
        "case_id": case_id, "note": note, "chunk_text": text,
        "judgement_id": jid,
        "citation": {k: c[k] for k in ("citation_id", "raw_text", "normalized",
                                      "source_span", "resolution_status",
                                      "evidence_text")},
        "nodes": [{"evidence_id": n["evidence_id"], "evidence_type": n["evidence_type"],
                   "source_span": n["source_span"]} for n in g["nodes"]],
        "attribution_map": amap, "relation": rel})
p = REPO / "tests/fixtures/b3d_attribution_set.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for c in out["cases"]:
    r = c["relation"]
    print(c["case_id"], repr(c["citation"]["raw_text"][:28]),
          c["citation"]["resolution_status"].split(":")[0],
          "->", r["attribution"], r["attribution_rule_id"])
print(f"wrote {p} — MANUAL REVIEW REQUIRED")
