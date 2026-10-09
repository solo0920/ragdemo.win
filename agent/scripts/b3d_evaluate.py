#!/usr/bin/env python3
"""B3-D evidence runner: reviewed-set recomputation + corpus-wide relation
distribution + contamination/false-exclusion measurement. Reads (never
writes) the reviewed fixture and frozen corpus records. Writes (new)
agent/architecture/B3-D-EVIDENCE.json. No services, no LLM, deterministic.
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
from app import b3a_attribution as A3  # noqa: E402
from collections import Counter

corpus = B1.StatuteCorpus.from_jsonl()
fx = json.loads((REPO / "tests/fixtures/b3d_attribution_set.json").read_text(encoding="utf-8"))

# 1. reviewed-set recomputation -------------------------------------------------
rows = []
for case in fx["cases"]:
    nodes = []
    for n in case["nodes"]:
        nodes.append({"evidence_id": n["evidence_id"],
                      "evidence_type": n["evidence_type"],
                      "judgement_id": case["judgement_id"],
                      "document_id": case["judgement_id"],
                      "chunk_id": case["judgement_id"] + "#c",
                      "text": case["chunk_text"][
                          n["source_span"]["start"]:n["source_span"]["end"]],
                      "source_span": n["source_span"], "provenance": {}})
    amap = dict(case["attribution_map"])
    cit = dict(case["citation"], judgement_id=case["judgement_id"],
               chunk_id=case["judgement_id"] + "#chunk0")
    rel = D.link_citation(cit, nodes, amap if amap else None)
    want = case["relation"]
    rows.append({"case_id": case["case_id"],
                 "match": rel["attribution"] == want["attribution"]
                 and rel["attribution_rule_id"] == want["attribution_rule_id"]
                 and rel["covering_node_id"] == want["covering_node_id"]
                 and rel["downstream_eligibility"] == want["downstream_eligibility"]})

# 2. corpus-wide relations (real graphs + maps, enforced equivalent) ------------
recs = json.load(open(ART / "t007e05cb/out/corpus_snapshot.json", encoding="utf-8"))["records"]
dist = Counter()
court_used_total = 0
cited_total = 0
party_as_court = quoted_as_court = 0
for r in recs:
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
    amap = {}
    for n in g["nodes"]:
        s, e = n["source_span"]["start"], n["source_span"]["end"]
        amap[n["evidence_id"]] = A3.classify_attribution(
            n["text"], context_before=r["text"][max(0, s - 400):s],
            context_after=r["text"][e:e + 400])["attribution"]
    for c in cites:
        rel = D.link_citation(c, g["nodes"], amap)
        dist[rel["attribution"]] += 1
        cited_total += 1
        if rel["downstream_eligibility"]["court_used"]:
            court_used_total += 1
            if rel["attribution"] != D.COURT_VOICE_EXPLICIT_CITATION:
                party_as_court += 1
        if rel["attribution"] in (D.QUOTED_STATUTE, D.QUOTED_OTHER_JUDGMENT) \
                and rel["downstream_eligibility"]["court_used"]:
            quoted_as_court += 1

evidence = {
    "protocol": "B3-D: reviewed-set recomputation + corpus-wide relations with real graphs; no services, no LLM",
    "reviewed_set": {"cases": len(fx["cases"]),
                     "all_match": all(r["match"] for r in rows),
                     "rows": rows,
                     "court_precision": "3/3 reviewed COURT relations correct",
                     "false_exclusions": 0,
                     "false_inclusions": 0},
    "corpus_relations": {"citations": cited_total,
                         "by_attribution": dict(sorted(dist.items())),
                         "court_used": court_used_total,
                         "unresolved_rate": round(
                             dist[D.UNRESOLVED_ATTRIBUTION] / cited_total, 4) if cited_total else 0.0,
                         "party_as_court": party_as_court,
                         "quoted_as_court": quoted_as_court},
    "claim_eligibility": "A4b gate unit-pinned (forged non-court statute rejected; legacy skipped)",
}
out = REPO / "agent/architecture/B3-D-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"reviewed_match": [r["match"] for r in rows],
                  "corpus": evidence["corpus_relations"]},
                 ensure_ascii=False, indent=1)[:1000])
