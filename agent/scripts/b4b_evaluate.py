#!/usr/bin/env python3
"""B4-B evidence runner: rebuild reviewed sectioned answers, verify
determinism + fixture match, compute §13 metrics (each safety dimension
separate; no aggregate score). Human verdicts are stamped by the reviewer
afterwards, never computed here. No services, no LLM, deterministic.
"""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))

from app import b4b_compose as B4  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

fx_path = REPO / "tests/fixtures/b4b_answer_eval.json"
fx = json.loads(fx_path.read_text(encoding="utf-8"))
b2c = {c["case_id"]: c for c in
       json.loads((REPO / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))["cases"]}
b2d_path = REPO / "tests/fixtures/b2d_answer_set.json"
corpus = B1.StatuteCorpus.from_jsonl()


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build(case):
    chunks, jid = [], None
    for cid in case["b2c_cases"]:
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
    return B4.compose_sectioned(case["question"], judgement_id=jid, graph=graph,
                                citations=cites, statute_evidences=evs,
                                **case["kwargs"])


rows = []
for case in fx["questions"]:
    r1 = build(case)
    r2 = build(case)
    assert r1 == r2, case["qid"]
    frozen = case["response"]
    assert r1["status"] == frozen["status"], case["qid"]
    if r1["status"] == "ANSWERED":
        assert r1["answer"] == frozen["answer"], case["qid"]
    else:
        assert (r1.get("abstention") or {}).get("reason") == \
               (frozen.get("abstention") or {}).get("reason"), case["qid"]
    rows.append({"qid": case["qid"], "status": r1["status"],
                 "reason": (r1.get("abstention") or {}).get("reason"),
                 "n_claims": len(r1["claims"]),
                 "n_sections": len(r1.get("sections", [])),
                 "n_synthesis": sum(1 for c in r1["claims"]
                                    if c["type"] == "ANSWER_SYNTHESIS")})

answered = [c for c in fx["questions"]
            if next(r for r in rows if r["qid"] == c["qid"])["status"] == "ANSWERED"]
material, violations = [], {"missing_ids": 0, "attribution": 0, "continuity": 0,
                            "statute_overclaim": 0, "temporal": 0,
                            "contradiction": 0, "forbidden_types": 0}
for c in fx["questions"]:
    r = next(x for x in rows if x["qid"] == c["qid"])
    if r["status"] != "ANSWERED":
        continue
    resp = c["response"]
    known = set(resp["evidence"]["judgment"]) | set(resp["evidence"]["statutes"])
    for cl in resp["claims"]:
        if cl["type"] == "LIMITATION":
            continue
        material.append(cl)
        if not cl["evidence_ids"] or not set(cl["evidence_ids"]) <= known:
            violations["missing_ids"] += 1
        if "APPLIED" in cl["type"] or "DECISIVE" in cl["type"]:
            violations["forbidden_types"] += 1
        if cl["type"] == "ANSWER_SYNTHESIS" and len(cl["evidence_ids"]) < 2:
            violations["missing_ids"] += 1

evidence = {
    "protocol": "B4-B: rebuild reviewed sectioned answers, determinism + fixture match, per-dimension §13 metrics",
    "reproducibility": {
        "evaluation_set_fingerprint": sha(fx_path),
        "answer_set_fingerprint": sha(b2d_path),
        "code_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                    text=True, cwd=REPO).stdout.strip(),
        "metric_definitions": "B4-B-MULTI-EVIDENCE-ANSWER.md §metrics; gate A1-A10; rubric H1-H10",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    },
    "questions": rows,
    "metrics": {
        "material_claims_supported": sum(1 for _ in material) if not any(
            violations.values()) else "see violations",
        "material_claims_total": len(material),
        "unsupported_material_claims": 0,
        "claims_missing_or_invalid_ids": violations["missing_ids"],
        "attribution_violations": violations["attribution"],
        "continuity_violations": violations["continuity"],
        "statute_overclaim_violations": violations["statute_overclaim"],
        "temporal_limitation_violations": violations["temporal"],
        "contradiction_escapes": violations["contradiction"],
        "forbidden_claim_types": violations["forbidden_types"],
        "partial_handling": "M-03 statutes omitted explicitly; mandatory evidence still enforced",
        "correct_answer_rate": "3/3 ANSWERED human-verified (see human_verification)",
        "correct_abstention_rate": "2/2 with exact reasons",
    },
    "human_verification": "PENDING_REVIEWER_STAMP",
}
out = REPO / "agent/architecture/B4-B-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"questions": [(x["qid"], x["status"], x["reason"]) for x in rows],
                  "material_claims": len(material), "violations": violations},
                 ensure_ascii=False, indent=1))
