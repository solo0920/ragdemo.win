#!/usr/bin/env python3
"""One-off B2-C verified-set builder (run by the batch author, then frozen).

Records extraction output over real chunk texts; the author manually reviews
every recorded expectation against the source text before freezing; tests
assert exact equality afterwards. Re-running overwrites (review required again).
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))
ART = Path("/home/solo/artifacts")

from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

corpus = B1.StatuteCorpus.from_jsonl()
recs = {r["chunk_id"]: r for r in
        json.loads((ART / "t007e05cb/out/corpus_snapshot.json").read_text(encoding="utf-8"))["records"]}
demo = json.loads((REPO / "tests/fixtures/b1_judgement_serving/case_00450.json").read_text(encoding="utf-8"))["document"]["JFULL"]


def chunk_dict(chunk_id, text=None, role=None, labels=None):
    r = recs[chunk_id]
    m = re.search(r"#C(\d+)$", chunk_id)
    return {"chunk_id": chunk_id, "document_id": r["document_id"],
            "structural_role": role or r["structural_role"],
            "structural_labels": labels if labels is not None else r.get("structural_labels", []),
            "carries_declared_disposition_unit": r.get("carries_declared_disposition_unit", False),
            "text": text if text is not None else r["text"],
            "span": r.get("span", {}), "chunk_index": int(m.group(1)) if m else 0}


def demo_chunk(label, needle, width=200):
    i = demo.find(needle)
    assert i >= 0, needle
    return {"chunk_id": f"STEV,115,店小,450,20260713,1#demo-{label}",
            "document_id": "STEV,115,店小,450,20260713,1",
            "structural_role": "FACTS", "structural_labels": ["FACTS"],
            "text": demo[max(0, i - 60):i + len(needle) + width],
            "span": {}, "chunk_index": 900}


CASES = [
    ("R-01-dismissal-header", ["CHDM,115,簡,1769,20260730,1#C001"]),
    ("R-02-home-violence-reasoning", ["CHDM,115,簡,1769,20260730,1#C002"]),
    ("R-03-payment-order-flagged", ["PCDV,115,司促,17332,20260713,1#C001"]),
    ("R-04-detention-full-doc", ["TCTA,115,續收,3347,20260710,1#C001",
                                 "TCTA,115,續收,3347,20260710,1#C002",
                                 "TCTA,115,續收,3347,20260710,1#C003"]),
    ("R-05-constitutional-review", ["JCCC,115,審裁,1177,20260713#C002"]),
    ("R-06-demo-disposition", [("demo", "原告之訴駁回")]),
    ("R-07-demo-holding", [("demo", "原告並未受有實際損害")]),
    ("R-08-appendix-empty", ["CTDM,115,訴,1694,20260731,1#C006"]),
]

out = {"verification_status": "AUTHOR_REVIEWED",
       "provenance": "real frozen corpus chunks + real B1 demo excerpts",
       "cases": []}
for case_id, spec in CASES:
    chunks = []
    for s in spec:
        if isinstance(s, tuple):
            chunks.append(demo_chunk(s[0], s[1]))
        else:
            chunks.append(chunk_dict(s))
    cites = []
    for ch in chunks:
        cites.extend(C.extract_citations(
            ch["text"], jid=ch["document_id"], chunk_index=ch["chunk_index"],
            corpus=corpus))
    graph = E.build_evidence_graph(chunks, cites)
    ok, failures = E.reasoning_gate(
        graph, {ch["chunk_id"]: ch["text"] for ch in chunks})
    out["cases"].append({
        "case_id": case_id, "judgement_id": chunks[0]["document_id"],
        "chunks": chunks,
        "graph": {"nodes": graph["nodes"], "edges": graph["edges"]},
        "gate": {"verdict": "PASS" if ok else "FAIL", "failures": failures}})
p = REPO / "tests/fixtures/b2c_reasoning_set.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
n = sum(len(c["graph"]["nodes"]) for c in out["cases"])
print(f"wrote {p} ({len(out['cases'])} cases, {n} nodes) — MANUAL REVIEW REQUIRED")
