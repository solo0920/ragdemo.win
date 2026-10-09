#!/usr/bin/env python3
"""B2-A evidence runner (read-only on frozen artifacts; writes one new file).

Reads (never writes): frozen Golden Set, frozen corpus snapshot, frozen
per-query result files (BM25 run1/run2, D-A, D-B), frozen three-way comparison.
Writes: agent/architecture/B2-A-EVIDENCE.json (new analysis artifact).

Produces: R1 identity audit, R2 run-agreement check, per-retriever Recall@1
failure tables, hard-negative reference rates, R5 provenance audit, R1-R5 gate.
No retrieval is executed, no baseline recomputed, no model called.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
ART = Path("/home/solo/artifacts")

from app import b2a_eval as E  # noqa: E402


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


golden = load(ART / "t007e05c/out/golden_queries.v1.json")["queries"]
corpus = load(ART / "t007e05cb/out/corpus_snapshot.json")["records"]
corpus_ids = {r["chunk_id"] for r in corpus}
doc_of = {r["chunk_id"]: r["document_id"] for r in corpus}

bm25_r1 = load(ART / "t007e05cb/out/baseline_results.json")
bm25_r2 = load(ART / "t007e05cb/out/baseline_results_run2.json")
da = load(ART / "t007e05d/out/embedding_results.json")
db = load(ART / "t007e05db/out/embedding2_results.json")
three = load(ART / "t007e05db/out/three_way_comparison.json")

# R1: identity audit ---------------------------------------------------------
identity = E.audit_target_identity(golden, corpus_ids)

# R2: run agreement (ranked-list equality, read from frozen files) ------------
def ranked_of(results):
    return [[e["chunk_id"] for e in r["top_k"]] for r in results]

runs_agree = ranked_of(bm25_r1["results"]) == ranked_of(bm25_r2["results"])

# Failure tables + HN reference ----------------------------------------------
retrievers = {
    "bm25": bm25_r1["results"],
    "bge_m3": da["results"],
    "embeddinggemma2": db["results"],
}
tables = {}
hn_ref = {}
for name, results in retrievers.items():
    rows = []
    for r, q in zip(results, golden):
        assert r["query_id"] == q["query_id"], (name, r["query_id"])
        ranked = [e["chunk_id"] for e in r["top_k"]]
        targets = {t["chunk_id"] for t in q.get("targets", [])}
        if E.recall_at_1(ranked, targets) == 1.0:
            continue
        rows.append(E.classify_recall1_failure(q, ranked, doc_of, top_k=len(ranked)))
    tables[name] = rows
    hn = sum(1 for w in rows if w["category"] == "hard_negative_outranks_target")
    hn_ref[name] = {"recall1_failures": len(rows),
                    "hn_outranks": hn,
                    "hn_share_of_failures": hn / len(rows) if rows else 0.0}

# Frozen aggregate citation (read, not recomputed) ----------------------------
ref_metrics = {}
for row in three["overall"]:
    ref_metrics[row["key"]] = {"bm25": row.get("bm25"), "bge_m3": row.get("bge_m3"),
                               "embedding2": row.get("embedding2")}
hn_rates = {}
for row in three.get("safety", []):
    hn_rates[row.get("metric", row.get("key", "?"))] = {
        "bm25": row.get("bm25"), "bge_m3": row.get("bge_m3"),
        "embedding2": row.get("embedding2")}

# R5: provenance audit over every frozen ranked record -----------------------
prov_problems = {}
prov_notes = set()
for name, results in retrievers.items():
    for r in results:
        for e in r["top_k"]:
            ok, notes = E.provenance_ok(e)
            if not ok:
                prov_problems.setdefault(name, []).append(
                    {"query_id": r["query_id"], "record": e, "notes": notes})
            prov_notes.update(n for n in notes if "null" in n)

# Gate -----------------------------------------------------------------------
total_fail = sum(len(v) for v in tables.values())
all_rows = [w for v in tables.values() for w in v]
gates = E.gate_r1_to_r5(
    identity_audit=identity, runs_agree=runs_agree,
    hn_threshold=None, hn_reference={k: v["hn_share_of_failures"] for k, v in hn_ref.items()},
    failure_table=all_rows, total_failures=total_fail,
    provenance_result={k: {"problems": v} for k, v in prov_problems.items()})

evidence = {
    "protocol": "B2-A: read frozen files only; no retrieval executed; no baseline recomputed",
    "inputs": {
        "golden_queries": 76, "corpus_records": 430,
        "golden_fp": "58e01c798c647b0ebbbf5f7b93f1c61c0aad9908be3cf7ff8ce5c94a339e7f2c",
        "corpus_fp": "893d40b68907e6221c548b721cd93991bd85e6f370c584eb662497a3f6071a8d",
    },
    "identity_audit": identity,
    "runs_agree_bm25": runs_agree,
    "reference_metrics_cited": ref_metrics,
    "hard_negative_reference": hn_ref,
    "hard_negative_rates_cited": hn_rates,
    "failure_tables": tables,
    "provenance_problems": prov_problems,
    "provenance_notes": sorted(prov_notes),
    "gates": gates,
}
out = REPO / "agent/architecture/B2-A-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {out}")
print("identity invalid:", sum(1 for a in identity.values() if not a["valid"]), "/76")
print("runs agree:", runs_agree)
for name, rows in tables.items():
    from collections import Counter
    print(name, "recall@1 failures:", len(rows), dict(Counter(w["category"] for w in rows)))
print("gates:", {k: v["verdict"] for k, v in gates.items()})
