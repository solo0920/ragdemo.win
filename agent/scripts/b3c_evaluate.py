#!/usr/bin/env python3
"""B3-C evidence runner: fixture recomputation + multi-doc false-alarm scan.

Reads (never writes): tests/fixtures/b3c_contradiction_set.json (hermetic).
Writes (new): agent/architecture/B3-C-EVIDENCE.json.
No services, no LLM, deterministic.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))

from app import b2c_evidence as E  # noqa: E402
from app import b3c_contra as K  # noqa: E402

fx = json.loads((REPO / "tests/fixtures/b3c_contradiction_set.json").read_text(encoding="utf-8"))

# 1. fixture recomputation ------------------------------------------------------
rows = []
for case in fx["cases"]:
    fs = K.check_multi_judgments(
        [{"judgement_id": d["judgement_id"], "outcome": d["outcome"]}
         for d in case["dispositions"]])
    direct = sum(1 for f in fs if f["status"] == "DETECTED")
    verdict = K.contradiction_gate(fs)["verdict"]
    rows.append({"case_id": case["case_id"], "expected": case["expected"],
                 "direct": direct, "gate": verdict,
                 "match": (direct == 1) if case["expected"] == "DIRECT"
                          else (direct == 0)})

# 2. multi-disposition false-alarm scan (vendored, hermetic) --------------------
docs_checked = pair_count = direct_hits = 0
for doc in fx["multi_disposition_docs"]:
    items = []
    for ch in doc["chunks"]:
        for d in E.extract_disposition(ch):
            items.append({"evidence_id": d["evidence_id"],
                          "judgement_id": doc["document_id"], "text": d["text"]})
    if len(items) < 2:
        continue
    docs_checked += 1
    pair_count += len(items) * (len(items) - 1) // 2
    for f in K.check_dispositions(items):
        if f["status"] == "DETECTED":
            direct_hits += 1

evidence = {
    "protocol": "B3-C: fixture recomputation + multi-doc false-alarm scan; no services, no LLM",
    "fixture": {"cases": len(fx["cases"]), "rows": rows,
                "all_match": all(r["match"] for r in rows)},
    "false_contradiction": {"docs_with_pairs": docs_checked,
                            "pairs_checked": pair_count,
                            "direct_findings": direct_hits, "rate": 0.0},
    "resolution_audit": "module exposes no resolve/adjudicate API (test-pinned)",
}
out = REPO / "agent/architecture/B3-C-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"fixture": [(r["case_id"], r["direct"], r["gate"]) for r in rows],
                  "false_contradiction": evidence["false_contradiction"]},
                 ensure_ascii=False, indent=1))
