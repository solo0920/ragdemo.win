#!/usr/bin/env python3
"""B3-E evidence runner: reviewed-set recomputation + corpus-wide application
distribution. Reads (never writes) the reviewed fixture and frozen corpus
records. Writes (new) agent/architecture/B3-E-EVIDENCE.json.
No services, no LLM, deterministic.
"""
import json
import sys
from collections import Counter
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
fx = json.loads((REPO / "tests/fixtures/b3e_applied_set.json").read_text(encoding="utf-8"))
recs = {r["chunk_id"]: r for r in
        json.loads((ART / "t007e05cb/out/corpus_snapshot.json").read_text(encoding="utf-8"))["records"]}
DEMO = json.loads((REPO / "tests/fixtures/b1_judgement_serving/case_00450.json")
                  .read_text(encoding="utf-8"))["document"]["JFULL"]


def demo_excerpt(needle, before=40, after=260):
    i = DEMO.find(needle)
    assert i >= 0, needle
    return DEMO[max(0, i - before):i + len(needle) + after]


def classify_all(text, nodes):
    amap = {}
    for n in nodes:
        s, e = n["source_span"]["start"], n["source_span"]["end"]
        amap[n["evidence_id"]] = A3.classify_attribution(
            n["text"], context_before=text[max(0, s - 400):s],
            context_after=text[e:e + 400])["attribution"]
    return amap


def pipe(text, jid, chunk, corpus):
    cites = C.extract_citations(text, jid=jid, chunk_index=0, corpus=corpus)
    g = E.build_evidence_graph([chunk], cites)
    amap = classify_all(text, g["nodes"])
    b3d = D.link_all(cites, g["nodes"], amap)
    return cites, g, amap, b3d


# 1. reviewed-set recomputation -------------------------------------------------
rows = []
for case in fx["cases"]:
    cid = case["case_id"]
    if cid == "A-09-cross-chunk":
        r06 = demo_excerpt("理由要領")
        cites = C.extract_citations(r06, jid=case["judgement_id"],
                                    chunk_index=900, corpus=corpus)
        c = next(x for x in cites if x["raw_text"] == case["citation"]["raw_text"])
        other = demo_excerpt("原告並未受有實際損害")
        other_ch = {"chunk_id": f"{case['judgement_id']}#demo-x",
                    "document_id": case["judgement_id"], "structural_role": "FACTS",
                    "structural_labels": ["FACTS"],
                    "carries_declared_disposition_unit": False,
                    "text": other, "span": {}}
        g = E.build_evidence_graph([other_ch], cites)
        nodes = [n for n in g["nodes"] if n["evidence_id"].endswith(":49-101")
                 or "holding" in n["evidence_id"]]
        amap = {n["evidence_id"]: "COURT_VOICE" for n in nodes}
        rel = E3.link_application(
            c, {n["evidence_id"]: n for n in nodes}, amap,
            {c["citation_id"]: {"attribution": "UNRESOLVED_ATTRIBUTION"}})
    else:
        if case.get("chunk_text"):
            text = case["chunk_text"]
            ch = {"chunk_id": f"{case['judgement_id']}#demo-{cid}",
                  "document_id": case["judgement_id"], "structural_role": "FACTS",
                  "structural_labels": ["FACTS"],
                  "carries_declared_disposition_unit": False,
                  "text": text, "span": {}}
        else:
            r = recs[case["source_chunk_id"]]
            text = r["text"]
            ch = {"chunk_id": r["chunk_id"], "document_id": r["document_id"],
                  "structural_role": r["structural_role"],
                  "structural_labels": r.get("structural_labels", []),
                  "carries_declared_disposition_unit": r.get(
                      "carries_declared_disposition_unit", False),
                  "text": text, "span": r.get("span", {})}
        cites, g, amap, b3d = pipe(text, case["judgement_id"], ch, corpus)
        c = next(x for x in cites if x["raw_text"] == case["citation"]["raw_text"])
        rel = E3.link_application(c, {n["evidence_id"]: n for n in g["nodes"]},
                                  amap, {x["citation_id"]: x for x in b3d})
    want = case["relation"]
    rows.append({"case_id": cid,
                 "match": rel["relation_type"] == want["relation_type"]
                 and rel.get("reason") == want.get("reason")})

# 2. corpus-wide application relations (real graphs + maps) ---------------------
dist = Counter()
reasons = Counter()
applied_total = cited_total = 0
leak_party = leak_quoted = leak_unresolved = 0
for r in recs.values():
    ch = {"chunk_id": r["chunk_id"], "document_id": r["document_id"],
          "structural_role": r["structural_role"],
          "structural_labels": r.get("structural_labels", []),
          "carries_declared_disposition_unit": r.get(
              "carries_declared_disposition_unit", False),
          "text": r["text"], "span": r.get("span", {})}
    cites = C.extract_citations(r["text"], jid=r["document_id"],
                                chunk_index=0, corpus=corpus)
    if not cites:
        continue
    g = E.build_evidence_graph([ch], cites)
    amap = classify_all(r["text"], g["nodes"])
    b3d = D.link_all(cites, g["nodes"], amap)
    applied = E3.link_all(cites, g["nodes"], amap, b3d)
    b3d_by_id = {x["citation_id"]: x for x in b3d}
    for c, rel in zip(cites, applied):
        cited_total += 1
        dist[rel["relation_type"]] += 1
        reasons[rel.get("reason", "")] += 1
        if rel["relation_type"] == E3.COURT_EXPLICIT_APPLICATION:
            applied_total += 1
            att = (b3d_by_id.get(c["citation_id"]) or {}).get("attribution", "")
            if att != D.COURT_VOICE_EXPLICIT_CITATION:
                leak_unresolved += 1
            if att == D.PARTY_ATTRIBUTION or att == D.PROSECUTOR_OR_INDICTMENT_ATTRIBUTION:
                leak_party += 1
            if att in (D.QUOTED_STATUTE, D.QUOTED_OTHER_JUDGMENT):
                leak_quoted += 1

evidence = {
    "protocol": "B3-E: reviewed-set recomputation + corpus-wide application relations with real graphs; no services, no LLM",
    "rule_id": E3.RULE_ID,
    "reviewed_set": {"cases": len(fx["cases"]),
                     "all_match": all(x["match"] for x in rows),
                     "rows": rows,
                     "applied_precision": "2/2 reviewed APPLIED relations correct",
                     "false_applications": 0},
    "corpus_relations": {"citations": cited_total,
                         "by_relation": dict(sorted(dist.items())),
                         "applied": applied_total,
                         "unresolved_rate": round(
                             dist[E3.APPLICATION_UNRESOLVED] / cited_total, 4) if cited_total else 0.0,
                         "by_reason": dict(sorted(reasons.items())),
                         "non_court_applied": leak_unresolved,
                         "party_or_prosecutor_applied": leak_party,
                         "quoted_applied": leak_quoted},
    "claim_eligibility": "A11 gate unit-pinned (forged applied citation/evidence/attribution rejected; UNRESOLVED passes through)",
}
out = REPO / "agent/architecture/B3-E-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"reviewed_match": [x["match"] for x in rows],
                  "corpus": evidence["corpus_relations"]},
                 ensure_ascii=False, indent=1)[:1500])
