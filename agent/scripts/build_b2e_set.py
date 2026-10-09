#!/usr/bin/env python3
"""One-off B2-E evaluation-set builder (run by the batch author, then frozen).

Assembles reviewed cases from real sources only:
- B2-D reviewed answers (A-01..A-04, by reference — re-run from B2-C cases),
- frozen BM25 rank-1 chains (GQ-001/002/005 from b2b_chain pattern),
- frozen BM25 wrong-judgment chains (GQ-020/GQ-035 rank-1 docs, chunks vendored),
- refused-only excerpt + require_statutes (real PCDM chunks).
Writes tests/fixtures/b2e_answer_eval.json. Manual review of every recorded
expectation follows; tests assert exact equality afterwards.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))
ART = Path("/home/solo/artifacts")

from app import b2d_answer as A  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

corpus = B1.StatuteCorpus.from_jsonl()
recs = {r["chunk_id"]: r for r in
        json.loads((ART / "t007e05cb/out/corpus_snapshot.json").read_text(encoding="utf-8"))["records"]}
bydoc: dict[str, list[dict]] = {}
for r in recs.values():
    bydoc.setdefault(r["document_id"], []).append(r)
gold = {q["query_id"]: q for q in
        json.loads((ART / "t007e05c/out/golden_queries.v1.json").read_text(encoding="utf-8"))["queries"]}
bm25 = {r["query_id"]: r for r in
        json.loads((ART / "t007e05cb/out/baseline_results.json").read_text(encoding="utf-8"))["results"]}
b2c = {c["case_id"]: c for c in
       json.loads((REPO / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))["cases"]}
b2d = {q["qid"]: q for q in
       json.loads((REPO / "tests/fixtures/b2d_answer_set.json").read_text(encoding="utf-8"))["questions"]}


def vendored_chunk(r, idx):
    return {"chunk_id": r["chunk_id"], "chunk_index": idx, "text": r["text"],
            "document_id": r["document_id"],
            "structural_role": r["structural_role"],
            "structural_labels": r.get("structural_labels", []),
            "carries_declared_disposition_unit": r.get("carries_declared_disposition_unit", False),
            "span": r.get("span", {})}


def run_chain(jid, chunks, question, require_statutes=False):
    cites = []
    for i, ch in enumerate(chunks):
        cites.extend(C.extract_citations(
            ch["text"], jid=jid, chunk_index=ch.get("chunk_index", i), corpus=corpus))
    graph = E.build_evidence_graph(chunks, cites)
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    return A.build_answer(question, judgement_id=jid, graph=graph,
                          citations=cites, statute_evidences=evs,
                          require_statutes=require_statutes)


def doc_chunks(doc):
    return [vendored_chunk(r, i) for i, r in
            enumerate(sorted(bydoc[doc], key=lambda x: x["chunk_id"]))]


CASES = [
    # Reuse B2-D reviewed answers (rebuilt below for independence).
    {"qid": "E-01", "category": "SUPPORTED_WITH_STATUTES", "kind": "b2d",
     "b2d_qid": "A-01-demo-dismissal"},
    {"qid": "E-02", "category": "SUPPORTED_WITH_STATUTES", "kind": "b2d",
     "b2d_qid": "A-02-detention-cause"},
    {"qid": "E-03", "category": "SUPPORTED_WITH_STATUTES", "kind": "chain",
     "golden_qid": "GQ-001"},
    {"qid": "E-04", "category": "SUPPORTED_WITH_STATUTES", "kind": "chain",
     "golden_qid": "GQ-002"},
    {"qid": "E-05", "category": "SUPPORTED_WITH_STATUTES", "kind": "chain",
     "golden_qid": "GQ-005"},
    {"qid": "E-06", "category": "OUTSIDE_SUPPORTED_SCOPE", "kind": "chain",
     "golden_qid": "GQ-020"},
    {"qid": "E-07", "category": "OUTSIDE_SUPPORTED_SCOPE", "kind": "chain",
     "golden_qid": "GQ-035"},
    {"qid": "E-08", "category": "INSUFFICIENT_JUDGMENT", "kind": "b2d",
     "b2d_qid": "A-03-violence-why"},
    {"qid": "E-09", "category": "INSUFFICIENT_REASONING", "kind": "b2d",
     "b2d_qid": "A-04-payment-require-statutes"},
]
# E-10: refused-only excerpt + require_statutes (real PCDM chunks).
_pcdm = [r for r in recs.values() if r["document_id"] == "PCDM,115,金訴,1889,20260729,1"]
_pcdm_refused = []
for r in _pcdm:
    cs = C.extract_citations(r["text"], jid=r["document_id"], chunk_index=0, corpus=corpus)
    if cs and all(c["resolution_status"] != "RESOLVED" for c in cs):
        _pcdm_refused.append(r["chunk_id"])
print("PCDM refused-only chunks:", _pcdm_refused)
refused_chunks = [vendored_chunk(recs[c], i) for i, c in enumerate(_pcdm_refused[:3])]

out = {"verification_status": "AUTHOR_REVIEWED",
       "provenance": "B2-D reviewed answers (rebuilt) + frozen BM25 rank-1 chains (vendored chunks) + refused-only excerpt",
       "cases": []}
for spec in CASES:
    if spec["kind"] == "b2d":
        src = b2d[spec["b2d_qid"]]
        chunks, jid, require, question = [], None, src["require_statutes"], src["question"]
        for cid in src["b2c_cases"]:
            for ch in b2c[cid]["chunks"]:
                chunks.append({**ch, "carries_declared_disposition_unit":
                               ch.get("carries_declared_disposition_unit", False)})
                jid = ch["document_id"]
        expected_jids = None
        frozen_rank1 = None
    else:
        gq = gold[spec["golden_qid"]]
        top1 = bm25[spec["golden_qid"]]["top_k"][0]["chunk_id"]
        jid = top1.split("#")[0]
        question = gq["query_text"]
        chunks = doc_chunks(jid)
        require = False
        expected_jids = gq["judgement_ids"]
        frozen_rank1 = {"chunk_id": top1, "judgement_id": jid}
    r = run_chain(jid, chunks, question, require)
    out["cases"].append({
        "qid": spec["qid"], "category": spec["category"], "question": question,
        "selected_judgement_id": jid, "expected_judgement_ids": expected_jids,
        "frozen_rank1": frozen_rank1, "require_statutes": require,
        "chunks": chunks, "response": r})
# E-10 appended separately (excerpt scope, honestly labeled).
r10 = run_chain("PCDM,115,金訴,1889,20260729,1", refused_chunks,
                "被告在本案中的量刑為何？")
out["cases"].append({
    "qid": "E-10", "category": "INSUFFICIENT_JUDGMENT",
    "question": "被告在本案中的量刑為何？",
    "selected_judgement_id": "PCDM,115,金訴,1889,20260729,1",
    "expected_judgement_ids": None, "frozen_rank1": None,
    "require_statutes": False, "excerpt_scoped": True,
    "note": "refused-only evidence zone (刑法 citations unresolvable): cannot anchor claims. "
            "A holding+reasoning UNRESOLVED_STATUTE E2E case does not exist naturally in the corpus "
            "(searched); that path is unit-covered only (B2-D require_statutes test).",
    "chunks": refused_chunks, "response": r10})
p = REPO / "tests/fixtures/b2e_answer_eval.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for c in out["cases"]:
    r = c["response"]
    print(c["qid"], c["category"], r["status"],
          (r.get("abstention") or {}).get("reason", ""), f"{len(r['claims'])} claims")
print(f"wrote {p} — MANUAL REVIEW REQUIRED")
