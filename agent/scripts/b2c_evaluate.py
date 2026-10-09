#!/usr/bin/env python3
"""B2-C evidence runner: metrics over the frozen verified set (no services).

Reads (never writes): tests/fixtures/b2c_reasoning_set.json.
Writes (new): agent/architecture/B2-C-EVIDENCE.json.
Recomputes every recorded graph + gate verdict independently, then reports
type-level precision/recall (node identity = type + span + text), span
validity, truncation/incomplete rate, and provenance completeness.
Deterministic: reruns byte-identical.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))

from app import b2c_evidence as E  # noqa: E402

vset = json.loads((REPO / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))


def key(n):
    return (n["evidence_type"], n["chunk_id"], n["source_span"]["start"],
            n["source_span"]["end"], n["text"])


tp = fp = fn = 0
span_ok = span_total = 0
incomplete = 0
prov_ok = prov_total = 0
by_type: dict[str, dict[str, int]] = {}
gate_fails = []
for case in vset["cases"]:
    texts = {ch["chunk_id"]: ch["text"] for ch in case["chunks"]}
    got_graph = E.build_evidence_graph(case["chunks"], [])
    got = [key(n) for n in got_graph["nodes"]]
    want = [key(n) for n in case["graph"]["nodes"]]
    for k in got:
        if k in want:
            tp += 1
        else:
            fp += 1
    for k in want:
        if k not in got:
            fn += 1
    for n in got_graph["nodes"]:
        span_total += 1
        sp = n["source_span"]
        if texts[n["chunk_id"]][sp["start"]:sp["end"]] == n["text"]:
            span_ok += 1
        prov_total += 1
        if n["judgement_id"] and n["chunk_id"] and n["text"]:
            prov_ok += 1
        if n["provenance"].get("incomplete"):
            incomplete += 1
        t = by_type.setdefault(n["evidence_type"], {"tp": 0, "fp": 0, "fn": 0})
        t["tp"] += 0  # per-type split below
    for n in case["graph"]["nodes"]:
        t = by_type.setdefault(n["evidence_type"], {"tp": 0, "fp": 0, "fn": 0})
        t["fn"] += 0
    ok, failures = E.reasoning_gate(got_graph, texts)
    want_verdict = case["gate"]["verdict"]
    if ("PASS" if ok else "FAIL") != want_verdict or failures != case["gate"]["failures"]:
        gate_fails.append(case["case_id"])

# per-type precision/recall from recorded-vs-recomputed node sets
for case in vset["cases"]:
    texts = {ch["chunk_id"]: ch["text"] for ch in case["chunks"]}
    got_graph = E.build_evidence_graph(case["chunks"], [])
    for n in got_graph["nodes"]:
        if key(n) in [key(w) for w in case["graph"]["nodes"]]:
            by_type[n["evidence_type"]]["tp"] += 1
        else:
            by_type[n["evidence_type"]]["fp"] += 1
    for n in case["graph"]["nodes"]:
        if key(n) not in [key(g) for g in got_graph["nodes"]]:
            by_type[n["evidence_type"]]["fn"] += 1

prec = tp / (tp + fp) if tp + fp else 1.0
rec = tp / (tp + fn) if tp + fn else 1.0
evidence = {
    "protocol": "B2-C: recompute frozen verified graphs + gate verdicts; no services, no LLM, deterministic",
    "verified_set": {"cases": len(vset["cases"]),
                     "extraction": {"precision": round(prec, 4), "recall": round(rec, 4),
                                    "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 1.0,
                                    "tp": tp, "fp": fp, "fn": fn},
                     "by_type": {t: {"precision": round(v["tp"] / (v["tp"] + v["fp"]), 4) if v["tp"] + v["fp"] else 1.0,
                                      "recall": round(v["tp"] / (v["tp"] + v["fn"]), 4) if v["tp"] + v["fn"] else 1.0,
                                      **v} for t, v in by_type.items()},
                     "span_validity": {"valid": span_ok, "total": span_total,
                                       "rate": round(span_ok / span_total, 4) if span_total else 1.0},
                     "truncation": {"incomplete_flags": incomplete},
                     "provenance": {"complete": prov_ok, "total": prov_total,
                                    "rate": round(prov_ok / prov_total, 4) if prov_total else 1.0}},
    "gate_recomputation_mismatches": gate_fails,
}
out = REPO / "agent/architecture/B2-C-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False, indent=1)[:1200])
