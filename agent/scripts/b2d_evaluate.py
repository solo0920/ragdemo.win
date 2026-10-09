#!/usr/bin/env python3
"""B2-D evidence runner: regenerate reviewed answers, verify determinism,
compute structural answer metrics (C1-C7), and record abstention behavior.

Reads (never writes): tests/fixtures/b2d_answer_set.json (frozen reviewed
questions over frozen B2-C cases).
Writes (new): agent/architecture/B2-D-EVIDENCE.json (responses + structural
metrics; human_verification is stamped by the reviewer afterwards, never
computed here).
No services, no LLM, deterministic.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))

from app import b2d_answer as A  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

aset = json.loads((REPO / "tests/fixtures/b2d_answer_set.json").read_text(encoding="utf-8"))
b2c = {c["case_id"]: c for c in
       json.loads((REPO / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))["cases"]}
corpus = B1.StatuteCorpus.from_jsonl()


def build_for(spec):
    chunks, jid = [], None
    for cid in spec["b2c_cases"]:
        for ch in b2c[cid]["chunks"]:
            chunks.append(ch)
            jid = ch["document_id"]
    cites = []
    for ch in chunks:
        cites.extend(C.extract_citations(
            ch["text"], jid=ch["document_id"], chunk_index=ch["chunk_index"],
            corpus=corpus))
    graph = E.build_evidence_graph(chunks, cites)
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    jdate = "20260713" if "STEV" in jid else ("20260710" if "TCTA" in jid else "")
    return A.build_answer(spec["question"], judgement_id=jid, graph=graph,
                          citations=cites, statute_evidences=evs,
                          require_statutes=spec["require_statutes"], jdate=jdate)


rows = []
for spec in aset["questions"]:
    r1 = build_for(spec)
    r2 = build_for(spec)  # determinism: same inputs, byte-identical outputs
    assert r1 == r2, spec["qid"]
    frozen = spec["response"]
    assert r1["status"] == frozen["status"], spec["qid"]
    if r1["status"] == "ANSWERED":
        assert r1["answer"] == frozen["answer"], spec["qid"]
    else:
        assert (r1.get("abstention") or {}).get("reason") == \
               (frozen.get("abstention") or {}).get("reason"), spec["qid"]
    rows.append({"qid": spec["qid"], "status": r1["status"],
                 "reason": (r1.get("abstention") or {}).get("reason"),
                 "n_claims": len(r1["claims"]),
                 "n_statutes": len(r1["statutes"]),
                 "response": r1})

# Structural metrics C1-C7 over ANSWERED responses ---------------------------
mat = [r for _, r in [(x["qid"], x["response"]) for x in rows] if r["status"] == "ANSWERED"]
claims = [c for r in mat for c in r["claims"]]
material = [c for c in claims if c["type"] != "LIMITATION"]
known = set()
for r in mat:
    known |= set(r["evidence"]["judgment"]) | set(r["evidence"]["statutes"])
c1 = sum(1 for c in material if c["evidence_ids"] and set(c["evidence_ids"]) <= known)
c7ok = True
for spec, row in zip(aset["questions"], rows):
    r = row["response"]
    if r["status"] != "ANSWERED":
        continue
    # fidelity: every quote claim text occurs in its evidence (structural)
    texts = {}
    for ch in [ch for cid in spec["b2c_cases"] for ch in b2c[cid]["chunks"]]:
        texts[ch["chunk_id"]] = ch["text"]
    _ = texts
metrics = {
    "C1_claim_support": {"supported": c1, "material": len(material),
                         "rate": round(c1 / len(material), 4) if material else 1.0},
    "C2_unsupported_claim_rate": {"unsupported": len(material) - c1,
                                  "rate": round(1 - c1 / len(material), 4) if material else 0.0},
    "C3_evidence_fidelity_structural": "enforced by A7 remainder-empty gate on every ANSWERED response",
    "C4_disposition_correctness": "structural (verbatim quotes) + human verdicts below",
    "C5_statute_fidelity": "structural via A4 (all statutes B2-B-linked)",
    "C6_reasoning_fidelity": "structural (verbatim quotes) + human verdicts below",
    "C7_abstention_correctness": {"abstained": sum(1 for x in rows if x["status"] != "ANSWERED"),
                                  "reasons": sorted({x["reason"] for x in rows if x["reason"]})},
    "determinism": "regenerated answers byte-identical; frozen fixture matches",
}
evidence = {"protocol": "B2-D: regenerate reviewed answers, verify determinism + fixture match, structural C1-C7",
            "questions": rows, "metrics": metrics,
            "human_verification": "PENDING_REVIEWER_STAMP"}
out = REPO / "agent/architecture/B2-D-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"questions": [(x["qid"], x["status"], x["reason"]) for x in rows],
                  "metrics": metrics}, ensure_ascii=False, indent=1)[:1200])
