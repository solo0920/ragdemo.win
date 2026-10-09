#!/usr/bin/env python3
"""One-off B3-E reviewed-set builder (run by the batch author, then frozen).

Hand-picked real cases only: chunk + citation needle. Records application
relations. The author reads every recorded row against source text before
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

from app import b3e_applied as E3  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b1_serve as B1  # noqa: E402
from app import b3a_attribution as A3  # noqa: E402
from app import b3d_attribution as D  # noqa: E402

corpus = B1.StatuteCorpus.from_jsonl()
recs = {r["chunk_id"]: r for r in
        json.loads((ART / "t007e05cb/out/corpus_snapshot.json").read_text(encoding="utf-8"))["records"]}
DEMO = json.loads((REPO / "tests/fixtures/b1_judgement_serving/case_00450.json")
                  .read_text(encoding="utf-8"))["document"]["JFULL"]


def demo_excerpt(needle, before=40, after=260):
    i = DEMO.find(needle)
    assert i >= 0, needle
    return DEMO[max(0, i - before):i + len(needle) + after]


def real_chunk(cid):
    r = recs[cid]
    return {"chunk_id": r["chunk_id"], "document_id": r["document_id"],
            "structural_role": r["structural_role"],
            "structural_labels": r.get("structural_labels", []),
            "carries_declared_disposition_unit": r.get(
                "carries_declared_disposition_unit", False),
            "text": r["text"], "span": r.get("span", {})}


def demo_chunk(label, text):
    return {"chunk_id": f"STEV,115,店小,450,20260713,1#demo-{label}",
            "document_id": "STEV,115,店小,450,20260713,1",
            "structural_role": "FACTS", "structural_labels": ["FACTS"],
            "carries_declared_disposition_unit": False,
            "text": text, "span": {}}


# (case_id, chunk-spec, citation needle, note)
SPECS = [
    ("A-01-applied-184", ("demo", "原告並未受有實際損害"), "民法第184條第1項",
     "holding conclusion on cited articles"),
    ("A-02-applied-195", ("demo", "原告並未受有實際損害"), "第195條第1項",
     "scoped citation inside conclusory holding"),
    ("A-03-mention-not-applied", ("real", "CHDM,115,簡,1769,20260730,1#C002"),
     "家庭暴力防治法第2條第1款", "definitional mention (定有明文), no conclusion"),
    ("A-04-party-17-1", ("real", "KSHM,115,上訴,322,20260723,1#C004"),
     "毒品危害防制條例第17條第1項", "party-framed citation"),
    ("A-05-prosecutor-21-1", ("real", "TPHM,115,上訴,2702,20260716,1#C011"),
     "洗錢防制法第21條第1項", "indictment charge theory, rejected by court"),
    ("A-06-appendix-339-4", ("real", "CTDM,115,訴,1694,20260731,1#C006"),
     "中華民國刑法第339條之4", "appendix reproduction"),
    ("A-07-unresolved-436-18", ("demo", "理由要領"), "民事訴訟法第436條之18第1項",
     "no holding cover (audited exclusion)"),
    ("A-08-costs-78", ("demo", "訴訟費用負擔之依據"), "民事訴訟法第78條",
     "costs-basis mention, no conclusory holding"),
    ("A-09-cross-chunk", ("cross", None), "民事訴訟法第436條之18第1項",
     "citation and conclusion in different chunks"),
    ("A-10-detention-conclusion", ("real", "TCTA,115,續收,3347,20260710,1#C002"),
     "入出國及移民法第38條第1項", "checkbox conclusion without holding pattern"),
]

out = {"verification_status": "AUTHOR_REVIEWED",
       "provenance": "real frozen corpus chunks + real B1 demo excerpts; expectations reviewed by author",
       "cases": []}
for case_id, spec, needle, note in SPECS:
    if spec[0] == "demo":
        ch = demo_chunk(case_id, demo_excerpt(spec[1]))
    elif spec[0] == "real":
        ch = real_chunk(spec[1])
    else:
        ch = None
    if spec[0] == "cross":
        # Citation text from R-06, conclusory holding from R-07: real chunks,
        # deliberately unpaired (no shared text) to prove cross-chunk refusal.
        r06 = demo_excerpt("理由要領")
        cites = C.extract_citations(r06, jid="STEV,115,店小,450,20260713,1",
                                    chunk_index=900, corpus=corpus)
        c = next(x for x in cites if x["raw_text"] == needle)
        other = demo_excerpt("原告並未受有實際損害")
        g = E.build_evidence_graph(
            [demo_chunk("x", other)], cites)
        nodes = [n for n in g["nodes"] if n["evidence_id"].endswith(":49-101")
                 or "holding" in n["evidence_id"]]
        amap = {n["evidence_id"]: "COURT_VOICE" for n in nodes}
        rel = E3.link_application(
            c, {n["evidence_id"]: n for n in nodes}, amap,
            {c["citation_id"]: {"attribution": "UNRESOLVED_ATTRIBUTION"}})
        out["cases"].append({
            "case_id": case_id, "note": note + " (deliberately unpaired texts)",
            "judgement_id": "STEV,115,店小,450,20260713,1",
            "citation": {k: c[k] for k in ("citation_id", "raw_text", "normalized",
                                          "source_span", "resolution_status")},
            "nodes": [], "attribution_map": amap, "relation": rel})
        print(case_id, repr(needle[:24]), "->", rel["relation_type"], rel.get("reason"))
        continue
    cites = C.extract_citations(ch["text"], jid=ch["document_id"],
                                chunk_index=0, corpus=corpus)
    matches = [c for c in cites if c["raw_text"] == needle]
    assert matches, (case_id, needle)
    c = matches[0]
    g = E.build_evidence_graph([ch], cites)
    amap = {}
    for n in g["nodes"]:
        t = ch["text"]
        s, e = n["source_span"]["start"], n["source_span"]["end"]
        amap[n["evidence_id"]] = A3.classify_attribution(
            n["text"], context_before=t[max(0, s - 400):s],
            context_after=t[e:e + 400])["attribution"]
    b3d = D.link_all(cites, g["nodes"], amap)
    rel = E3.link_application(
        c, {n["evidence_id"]: n for n in g["nodes"]}, amap,
        {r["citation_id"]: r for r in b3d})
    out["cases"].append({
        "case_id": case_id, "note": note,
        "chunk_text": ch["text"] if spec[0] == "demo" else None,
        "source_chunk_id": None if spec[0] == "demo" else ch["chunk_id"],
        "judgement_id": ch["document_id"],
        "citation": {k: c[k] for k in ("citation_id", "raw_text", "normalized",
                                      "source_span", "resolution_status",
                                      "evidence_text")},
        "nodes": [{"evidence_id": n["evidence_id"], "evidence_type": n["evidence_type"],
                   "source_span": n["source_span"],
                   "provenance": n.get("provenance", {})} for n in g["nodes"]],
        "attribution_map": amap, "relation": rel})
    print(case_id, repr(needle[:26]), "->", rel["relation_type"], rel.get("reason"))
p = REPO / "tests/fixtures/b3e_applied_set.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {p} — MANUAL REVIEW REQUIRED")
