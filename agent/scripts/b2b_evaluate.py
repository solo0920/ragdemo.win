#!/usr/bin/env python3
"""B2-B evidence runner: metrics over the frozen verified set + corpus profile.

Reads (never writes): tests/fixtures/b2b_citation_set.json,
tests/fixtures/b2b_chain.json, frozen corpus records (texts only, for the
corpus-wide behavior profile — no labels invented; unresolved stays unresolved).
Writes (new): agent/architecture/B2-B-EVIDENCE.json.
No services, no downloads, no frozen writes. Deterministic: reruns byte-identical.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))
ART = Path("/home/solo/artifacts")

from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

corpus = B1.StatuteCorpus.from_jsonl()
vset = json.loads((REPO / "tests/fixtures/b2b_citation_set.json").read_text(encoding="utf-8"))
chain = json.loads((REPO / "tests/fixtures/b2b_chain.json").read_text(encoding="utf-8"))
records = json.loads((ART / "t007e05cb/out/corpus_snapshot.json").read_text(encoding="utf-8"))["records"]


def key(c):
    return (c["raw_text"], c["normalized"]["law_name"], c["normalized"]["article"],
            c["source_span"]["start"], c["source_span"]["end"],
            c["resolution_status"], c["evidence_id"])


# 1. verified-set metrics (independent re-check of the frozen expectations) ---
tp = fp = fn = 0
res_ok = res_total = unres = false_res = 0
prov_ok = prov_total = 0
gate_all_cites, gate_all_evs = [], []
for case in vset["cases"]:
    xd = {"usable": case["xiachen_usable"], "raw": []}
    got = C.extract_citations(case["chunk_text"], jid=case["judgement_id"],
                              chunk_index=case["chunk_index"], corpus=corpus,
                              xiachen=xd)
    want = case["expected_citations"]
    got_keys = [key(c) for c in got]
    want_keys = [key(c) for c in want]
    for k in got_keys:
        if k in want_keys:
            tp += 1
        else:
            fp += 1
    for k in want_keys:
        if k not in got_keys:
            fn += 1
    for c in got:
        prov_total += 1
        sp = c["source_span"]
        if c["chunk_id"] and isinstance(sp["start"], int) and sp["start"] < sp["end"]:
            prov_ok += 1
    for c in want:
        if c["resolution_status"] == "RESOLVED":
            res_total += 1
            if (c["raw_text"], c["evidence_id"]) in [
                    (g["raw_text"], g["evidence_id"]) for g in got
                    if g["resolution_status"] == "RESOLVED"]:
                res_ok += 1
            else:
                false_res += 1  # expected-resolved but got otherwise (incl. missed)
        else:
            unres += 1
            if not any(g["raw_text"] == c["raw_text"] and
                       g["resolution_status"] == c["resolution_status"] for g in got):
                false_res += 1
    gate_all_cites.extend(got)
    gate_all_evs.extend([c["statute_evidence"] for c in got if c["statute_evidence"]])
prec = tp / (tp + fp) if tp + fp else 1.0
rec = tp / (tp + fn) if tp + fn else 1.0

gate_ok, gate_failures = C.statute_gate(gate_all_cites, gate_all_evs, corpus)

# 2. corpus-wide behavior profile (unlabeled real data; counts only) ---------
prof = {"chunks": 0, "with_citations": 0, "resolved": 0, "unresolved": 0,
        "unresolved_reasons": {}, "by_law_source": {}}
for r in records:
    prof["chunks"] += 1
    cs = C.extract_citations(r["text"], jid=r["document_id"], chunk_index=0, corpus=corpus)
    if cs:
        prof["with_citations"] += 1
    for c in cs:
        if c["resolution_status"] == "RESOLVED":
            prof["resolved"] += 1
        else:
            prof["unresolved"] += 1
            reason = c["resolution_status"].split(":", 1)[1]
            prof["unresolved_reasons"][reason] = prof["unresolved_reasons"].get(reason, 0) + 1
        ls = c["law_source"]
        prof["by_law_source"][ls] = prof["by_law_source"].get(ls, 0) + 1

# 3. chain rollup --------------------------------------------------------------
chain_rows = []
for entry in chain["queries"]:
    cites = []
    for ch in entry["chunks"]:
        cites.extend(C.extract_citations(
            ch["text"], jid=entry["judgement_id"],
            chunk_index=ch["chunk_index"], corpus=corpus))
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    ok, failures = C.statute_gate(cites, evs, corpus)
    chain_rows.append({"query_id": entry["query_id"],
                       "judgement_id": entry["judgement_id"],
                       "n_citations": len(cites),
                       "n_resolved": sum(1 for c in cites if c["resolution_status"] == "RESOLVED"),
                       "n_unresolved": sum(1 for c in cites if c["resolution_status"] != "RESOLVED"),
                       "gate": "PASS" if ok else "FAIL", "failures": failures})

evidence = {
    "protocol": "B2-B: verified-set metrics + corpus profile + chain rollup; no services, no LLM, deterministic",
    "verified_set": {"cases": len(vset["cases"]),
                     "extraction": {"precision": round(prec, 4), "recall": round(rec, 4),
                                    "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 1.0,
                                    "tp": tp, "fp": fp, "fn": fn},
                     "resolution": {"exact_resolution_rate": round(res_ok / res_total, 4) if res_total else 1.0,
                                    "resolved_correct": res_ok, "expected_resolved": res_total,
                                    "unresolved_count": unres, "false_resolutions": false_res},
                     "provenance": {"with_span_and_chunk": prov_ok, "total": prov_total,
                                    "rate": round(prov_ok / prov_total, 4) if prov_total else 1.0}},
    "gate_S1_S8_full_set": {"verdict": "PASS" if gate_ok else "FAIL", "failures": gate_failures},
    "corpus_profile_430": prof,
    "chain": chain_rows,
    "no_applied_decisive_labels": True,
}
out = REPO / "agent/architecture/B2-B-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in evidence.items()
                  if k in ("verified_set", "gate_S1_S8_full_set", "chain")}, ensure_ascii=False, indent=1)[:1500])
print("corpus profile:", {k: v for k, v in prof.items() if not isinstance(v, dict)})
print("unresolved reasons:", prof["unresolved_reasons"], "law sources:", prof["by_law_source"])
