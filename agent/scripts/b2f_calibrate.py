#!/usr/bin/env python3
"""B2-F calibration (read-only frozen + measured files; writes evidence JSON).

Split (declared before seeing holdout): calibration = even-numbered queries
except GQ-020 (forced to holdout as mandatory probe); holdout = odd queries +
GQ-020 + GQ-035. Rule (declared before seeing holdout): per method, threshold
= the smallest margin with ZERO wrong-accepts on calibration... precisely:
max coverage subject to zero calibration wrong-accepts (scan candidate
thresholds = observed calibration margins).
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


def married_ranked_bm25(qid):
    return [{"chunk_id": e["chunk_id"], "judgement_id": e["chunk_id"].split("#")[0],
             "score": e["score"]} for e in bm25[qid]["top_k"]]


def ranked_c(qid):
    return [{"chunk_id": e["chunk_id"], "judgement_id": e["chunk_id"].split("#")[0],
             "score": e["score"]} for e in cvec[qid]["top"]]


def correct(qid, rank1_chunk):
    return rank1_chunk in {t["chunk_id"] for t in gold[qid].get("targets", [])}


def margins(ranked_fn):
    out = {}
    for qid in gold:
        rk = ranked_fn(qid)
        out[qid] = (rk[0]["score"] - rk[1]["score"], correct(qid, rk[0]["chunk_id"]))
    return out


def split():
    cal = [f"GQ-{i:03d}" for i in range(2, 77, 2) if i != 20]
    hold = [f"GQ-{i:03d}" for i in range(1, 77, 2)] + ["GQ-020"]
    assert len(cal) + len(hold) == 76 and not (set(cal) & set(hold))
    return cal, hold


def pick_threshold(margins_map, cal):
    cands = sorted({m for m, _ in margins_map.values()}, reverse=True)
    best = None
    for t in cands:
        acc = [(m >= t) for qid, (m, _) in margins_map.items() if qid in cal]
        wrong_acc = sum(1 for qid in cal
                        if margins_map[qid][0] >= t and not margins_map[qid][1])
        if wrong_acc == 0:
            best = (t, sum(acc))
    return best  # (threshold, calibration coverage)


def evaluate(method, ranked_fn, hold, threshold):
    verdicts = {}
    for qid in hold:
        rk = ranked_fn(qid)
        v = F.evaluate_confidence(
            gold[qid]["query_text"], rk, method=method,
            thresholds={method: threshold})
        verdicts[qid] = v
    acc = [q for q, v in verdicts.items() if v["status"] == "ACCEPT"]
    ok = [q for q in acc if correct(q, ranked_fn(q)[0]["chunk_id"])]
    wrong = [q for q in acc if not correct(q, ranked_fn(q)[0]["chunk_id"])]
    rej = [q for q in hold if q not in acc]
    return {"verdicts": {q: {"status": v["status"], "reason": v["reason"],
                             "margin": v["features"].get("score_margin")}
                         for q, v in verdicts.items()},
            "accepted": acc, "accepted_correct": ok, "accepted_wrong": wrong,
            "rejected": rej,
            "precision": len(ok) / len(acc) if acc else 1.0,
            "coverage": len(acc) / len(hold),
            "false_reject": [q for q in rej if correct(q, ranked_fn(q)[0]["chunk_id"])]}


cal, hold = split()
res = {}
for method, fn in (("bm25", married_ranked_bm25), ("snowflake-C", ranked_c)):
    mm = margins(fn)
    t, cov = pick_threshold(mm, cal)
    res[method] = {"threshold": t, "calibration_coverage": f"{cov}/{len(cal)}",
                   "holdout": evaluate(method,
                                       married_ranked_bm25 if method == "bm25" else ranked_c,
                                       hold, t)}
    print(method, "threshold", t, "cal_cov", f"{cov}/{len(cal)}")
    h = res[method]["holdout"]
    print("  holdout: acc", len(h["accepted"]), "correct", len(h["accepted_correct"]),
          "wrong", h["accepted_wrong"], "precision", round(h["precision"], 3),
          "coverage", round(h["coverage"], 3))
    print("  false rejects:", h["false_reject"][:8])
json.dump(res, open(REPO / "agent/architecture/B2-F-EVIDENCE.json", "w"),
          ensure_ascii=False, indent=1)
print("wrote B2-F-EVIDENCE.json (interim: gate verdicts pending)")
