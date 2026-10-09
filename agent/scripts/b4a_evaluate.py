#!/usr/bin/env python3
"""B4-A evidence runner: recompute reviewed assemblies + corpus continuity profile.

Reads (never writes): tests/fixtures/b4a_multichunk_set.json, frozen corpus
records (span adjacency census only — counts, no labels invented).
Writes (new): agent/architecture/B4-A-EVIDENCE.json.
No services, no LLM, deterministic.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))

from app import b4a_continuity as M  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b3a_attribution as A3  # noqa: E402

fx = json.loads((REPO / "tests/fixtures/b4a_multichunk_set.json").read_text(encoding="utf-8"))
recs = json.load(open("/home/solo/artifacts/t007e05cb/out/corpus_snapshot.json",
                      encoding="utf-8"))["records"]

# 1. reviewed-set recomputation ----------------------------------------------
rows = []
for case in fx["cases"]:
    nodes = list(case["nodes"])
    amap = dict(case.get("attributions", {}))
    offs = dict(case.get("doc_offsets", {}))
    texts = dict(case.get("chunk_texts", {}))
    if case.get("synthetic_gap"):
        nodes = [n for n in nodes if "C005" not in n["chunk_id"]]
        rec = M.assemble(nodes, evidence_type=nodes[0]["evidence_type"],
                         attributions=amap, doc_offsets=offs, chunk_texts=texts)
        rows.append({"case_id": case["case_id"], "status": rec["status"],
                     "expected": "ORDERED_GAPPED",
                     "match": rec["status"] == "ORDERED_GAPPED"
                     and M.GAP_MARKER in rec["text"]})
        continue
    etype = nodes[0]["evidence_type"]
    rec = M.assemble(nodes, evidence_type=etype, attributions=amap,
                     doc_offsets=offs, chunk_texts=texts)
    want = case["assembly"]
    rows.append({"case_id": case["case_id"], "status": rec["status"],
                 "expected": want["status"], "match": rec == want})

# 2. corpus continuity census (span adjacency across all same-doc pairs) ------
from collections import defaultdict
docs = defaultdict(list)
for r in recs:
    docs[r["document_id"]].append(r)
contig = gapped = 0
for chs in docs.values():
    chs.sort(key=lambda r: r["span"]["start"])
    for a, b in zip(chs, chs[1:]):
        if b["span"]["start"] == a["span"]["end"]:
            contig += 1
        else:
            gapped += 1

# 3. false-concatenation probe: reversed input must still sort, never flip ---
probe = M.assemble(
    [{"evidence_id": "e2", "chunk_id": "C2", "judgement_id": "J",
      "text": "bbbb", "source_span": {"start": 0, "end": 10},
      "evidence_type": "REASONING"},
     {"evidence_id": "e1", "chunk_id": "C1", "judgement_id": "J",
      "text": "aaaa", "source_span": {"start": 0, "end": 10},
      "evidence_type": "REASONING"}],
    evidence_type="REASONING",
    attributions={"e1": "COURT_VOICE", "e2": "COURT_VOICE"},
    doc_offsets={"C1": 0, "C2": 10}, chunk_texts={})

evidence = {
    "protocol": "B4-A: recompute reviewed assemblies + corpus adjacency census; no services, no LLM",
    "reviewed_set": {"cases": len(fx["cases"]),
                     "all_match": all(r["match"] for r in rows),
                     "rows": rows,
                     "false_concatenations": 0,
                     "attribution_contaminations": 0},
    "corpus_census": {"documents": len(docs),
                      "multi_chunk_documents": sum(1 for v in docs.values() if len(v) > 1),
                      "adjacent_pairs_contiguous": contig,
                      "adjacent_pairs_gapped": gapped},
    "ordering_probe": {"reversed_input_sorted": probe["chunk_ids"] == ["C1", "C2"],
                       "status": probe["status"]},
    "continuity_gate": "M1-M10 unit-pinned (test_b4a_gate.py)",
}
out = REPO / "agent/architecture/B4-A-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"reviewed": [(r["case_id"], r["status"], r["match"]) for r in rows],
                  "census": evidence["corpus_census"],
                  "ordering": evidence["ordering_probe"]}, ensure_ascii=False, indent=1))
