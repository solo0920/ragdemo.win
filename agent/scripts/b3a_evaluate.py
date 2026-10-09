#!/usr/bin/env python3
"""B3-A evidence runner: verified-set metrics + corpus-wide attribution profile.

Reads (never writes): tests/fixtures/b3a_attribution_set.json, frozen corpus
records (texts only, for the unlabeled behavior profile).
Writes (new): agent/architecture/B3-A-EVIDENCE.json.
No services, no LLM, deterministic.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))

from app import b3a_attribution as A3  # noqa: E402
from app import b2c_evidence as E  # noqa: E402

vset = json.loads((REPO / "tests/fixtures/b3a_attribution_set.json").read_text(encoding="utf-8"))
records = json.load(open("/home/solo/artifacts/t007e05cb/out/corpus_snapshot.json",
                         encoding="utf-8"))["records"]

# 1. verified-set metrics ------------------------------------------------------
tp = fp = fn = 0
court_tp = court_fn = 0
contam = {"party": 0, "quoted_judgment": 0, "quoted_statute": 0}
unres = 0
gate_items, gate_fails = [], []
for case in vset["cases"]:
    rec = A3.classify_attribution(case["passage"])
    want = case["expected_attribution"]
    if rec["attribution"] == want:
        tp += 1
    else:
        fp += 1
    if want == "COURT_VOICE" and rec["attribution"] != want:
        court_fn += 1
    if want == "COURT_VOICE" and rec["attribution"] == want:
        court_tp += 1
    if want != "COURT_VOICE" and rec["eligible_for_court_claim"]:
        if want == "PARTY_VOICE":
            contam["party"] += 1
        elif want == "QUOTED_OTHER_JUDGMENT":
            contam["quoted_judgment"] += 1
        elif want == "QUOTED_STATUTE":
            contam["quoted_statute"] += 1
    if rec["attribution"] == "UNRESOLVED_ATTRIBUTION":
        unres += 1
    gate_items.append({"evidence_id": f"att:{case['passage_id']}",
                       "chunk_id": case["chunk_id"],
                       "source_span": {"start": 0, "end": len(case["passage"]),
                                       "unit": "code_point", "basis": "chunk_text"},
                       "attribution": rec["attribution"], "rule_id": rec["rule_id"],
                       "used_for_court_claim": rec["eligible_for_court_claim"]})
fn = len(vset["cases"]) - tp
ok, failures = A3.attribution_gate(gate_items)

# 2. B2-C evidence nodes re-attribution (integration behavior, real nodes) -----
reattr = {}
node_total = 0
b2c = json.loads((REPO / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))
texts = {ch["chunk_id"]: ch["text"] for c in b2c["cases"] for ch in c["chunks"]}
for c in b2c["cases"]:
    for n in c["graph"]["nodes"]:
        node_total += 1
        t = texts[n["chunk_id"]]
        s, e = n["source_span"]["start"], n["source_span"]["end"]
        rec = A3.classify_attribution(
            n["text"], context_before=t[max(0, s - 400):s], context_after=t[e:e + 400])
        reattr[rec["attribution"]] = reattr.get(rec["attribution"], 0) + 1

# 3. corpus-wide profile (sentence-level, unlabeled counts only) ----------------
import re as _re
prof = {}
nsent = 0
for r in records:
    for m in _re.finditer(r"[^。！？；]+[。！？；]?", r["text"]):
        s = m.group(0)
        if len(s.strip()) < 12:
            continue
        nsent += 1
        rec = A3.classify_attribution(s)
        prof[rec["attribution"]] = prof.get(rec["attribution"], 0) + 1

evidence = {
    "protocol": "B3-A: verified-set metrics + B2-C node re-attribution + corpus sentence profile; no services, no LLM, deterministic",
    "verified_set": {"cases": len(vset["cases"]),
                     "accuracy": {"correct": tp, "wrong": fp, "rate": round(tp / (tp + fp), 4)},
                     "court_recall": {"found": court_tp, "missed": court_fn},
                     "contamination": {**contam, "target": "0 material party contamination"},
                     "unresolved": unres},
    "gate_Q1_Q8_verified_items": {"verdict": "PASS" if ok else "FAIL", "failures": failures},
    "b2c_node_reattribution": {"nodes": node_total, "by_attribution": reattr},
    "corpus_sentence_profile": {"sentences": nsent, "by_attribution": prof},
}
out = REPO / "agent/architecture/B3-A-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False, indent=1)[:1400])
