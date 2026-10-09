#!/usr/bin/env python3
"""One-off B2-F fixture builder (run by the batch author, then frozen).

Vendors per-query rank lists (top-5 chunk_id+score, both methods) + question
texts + correctness labels + split assignment into
tests/fixtures/b2f_confidence_eval.json. Manual review of gate outcomes
follows; tests assert exact equality afterwards.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
ART = Path("/home/solo/artifacts")
B2R1 = Path("/home/solo/projects/b2r1/out")

from app import b2f_confidence as F  # noqa: E402

gold = {q["query_id"]: q for q in
        json.loads((ART / "t007e05c/out/golden_queries.v1.json").read_text(encoding="utf-8"))["queries"]}
bm25 = {r["query_id"]: r for r in
        json.loads((ART / "t007e05cb/out/baseline_results.json").read_text(encoding="utf-8"))["results"]}
cvec = {r["query_id"]: r for r in
        json.loads((B2R1 / "b2r1_C_scores20.json").read_text(encoding="utf-8"))}
cal = {f"GQ-{i:03d}" for i in range(2, 77, 2) if i != 20}
out = {"verification_status": "AUTHOR_REVIEWED",
       "split_rule": "calibration=even except GQ-020; holdout=odd + GQ-020 (mandatory probes in holdout)",
       "thresholds": {"bm25": F.MARGIN_THRESHOLDS["bm25"],
                      "snowflake-C": F.MARGIN_THRESHOLDS["snowflake-C"]},
       "queries": []}
for qid in sorted(gold):
    ranks = {}
    for method, src, key in (("bm25", bm25, "top_k"), ("snowflake-C", cvec, "top")):
        ranks[method] = [{"chunk_id": e["chunk_id"],
                          "judgement_id": e["chunk_id"].split("#")[0],
                          "score": e["score"]} for e in src[qid][key][:5]]
    verdicts = {}
    for method in ("bm25", "snowflake-C"):
        v = F.evaluate_confidence(gold[qid]["query_text"], ranks[method], method=method)
        verdicts[method] = {"status": v["status"], "reason": v["reason"],
                            "margin": v["features"].get("score_margin")}
    out["queries"].append({
        "query_id": qid, "question": gold[qid]["query_text"],
        "split": "calibration" if qid in cal else "holdout",
        "expected_judgement_ids": gold[qid]["judgement_ids"],
        "rank1_correct": {m: (ranks[m][0]["chunk_id"] in
                              {t["chunk_id"] for t in gold[qid].get("targets", [])})
                          for m in ranks},
        "ranks": ranks, "verdicts": verdicts})
p = REPO / "tests/fixtures/b2f_confidence_eval.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
n = len(out["queries"])
print(f"wrote {p} ({n} queries) — MANUAL REVIEW REQUIRED")
