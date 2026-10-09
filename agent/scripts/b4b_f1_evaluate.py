#!/usr/bin/env python3
"""B4-B-F1 evidence runner: direct-vs-live equivalence on reviewed demo evidence.

Direct: compose_sectioned over the R-06+R-07 fixture graph.
Live: serve_question(answer_mode="sectioned") with stubbed retrieval over the
same chunk texts (real hashes), all gates except continuity enabled —
fixture demo chunks carry no spans, so continuity equivalence is covered
separately by span-bearing unit tests (test_b4a_serve.py), not hidden here.
Compares status, section order, claims, evidence IDs, abstention. Writes
agent/architecture/B4-B-F1-EVIDENCE.json. No services, no LLM, deterministic.
"""
import asyncio
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
from app import b2d_answer as A  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

b2c = {c["case_id"]: c for c in
       json.loads((REPO / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))["cases"]}
corpus = B1.StatuteCorpus.from_jsonl()
chunks = list(b2c["R-06-demo-disposition"]["chunks"]) + list(b2c["R-07-demo-holding"]["chunks"])
JID = chunks[0]["document_id"]
QUESTION = "原告依民法第184條請求3萬元，法院准了嗎？"


def build_inputs():
    cites = []
    for ch in chunks:
        cites.extend(C.extract_citations(
            ch["text"], jid=ch["document_id"], chunk_index=ch["chunk_index"],
            corpus=corpus))
    graph = E.build_evidence_graph(chunks, cites)
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    return graph, cites, evs


graph, cites, evs = build_inputs()
# Same attribution map both sides: serve enforces attribution, so the direct
# side must too — otherwise the comparison measures enforcement inputs,
# not composition equivalence.
amap = A.attribute_graph_nodes(
    graph["nodes"], {ch["chunk_id"]: ch["text"] for ch in chunks})
direct = B4.compose_sectioned(
    QUESTION, judgement_id=JID, graph=json.loads(json.dumps(graph)),
    citations=json.loads(json.dumps(cites)),
    statute_evidences=json.loads(json.dumps(evs)), jdate="20260713",
    attribution=amap)


def make_hits():
    hits, jfull, roles = [], {}, {}
    for i, ch in enumerate(chunks):
        pid, entry = f"p{i}", f"e{i}.json"
        hits.append({"id": pid,
                     "payload": {"entry_path": entry, "jid": JID, "chunk_index": i,
                                 "start_offset": 0, "end_offset": len(ch["text"]),
                                 "content_hash": hashlib.sha256(
                                     ch["text"].encode()).hexdigest(),
                                 "jyear": "115", "jdate": "20260713",
                                 "jcase": "x"}, "score": 0.9 - i * 0.1})
        jfull[entry] = ch["text"]
        roles[pid] = {"chunk_id": ch["chunk_id"], "document_id": JID,
                      "structural_role": ch["structural_role"],
                      "structural_labels": ch["structural_labels"],
                      "text": ch["text"], "span": ch.get("span", {})}
    return hits, jfull, roles


async def _search(q, v, lim, _h=None):
    return _h


async def _vec(texts):
    return [[0.0]]


hits, jfull, roles = make_hits()
live = asyncio.run(A.serve_question(
    QUESTION, search_fn=lambda q, v, lim: _search(q, v, lim, hits),
    embed_fn=_vec, jfull_by_entry=jfull, chunk_roles=roles,
    answer_mode="sectioned", enforce_contradiction=True,
    enforce_attribution=True))


def comparable(r):
    return {"status": r["status"],
            "sections": [(s["kind"], s["evidence_ids"]) for s in r.get("sections", [])],
            "claims": [(c["claim_id"], c["type"], c["text"], c["evidence_ids"])
                       for c in r["claims"]],
            "abstention": (r.get("abstention") or {}).get("reason")}


same = comparable(direct) == comparable(live)
evidence = {
    "protocol": "B4-B-F1: direct compose_sectioned vs live serve_question(sectioned) on identical reviewed evidence; continuity excluded (fixture lacks spans — covered by span unit tests)",
    "reproducibility": {
        "code_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                    text=True, cwd=REPO).stdout.strip(),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    },
    "comparison": {"equivalent": same,
                   "direct": comparable(direct), "live": comparable(live)},
    "live_route": {
        "default_mode": "single",
        "invalid_mode": "422 at schema (Literal)",
        "single_with_sections": "400 (no silent fallback)",
        "unknown_sections": "400", "empty_sections": "400",
        "sectioned_forwards": "answer_mode + sections intact (route-stub capture)",
    },
    "safety_gates_live": ["confidence", "attribution", "temporal", "contradiction",
                          "continuity", "grounding", "synthesis re-gating"],
    "methodology_note": "First run compared direct (no attribution map) vs live "
                        "(attribution enforced) and diverged on one statute "
                        "(436-18): its sentence has no court-voice node, so the "
                        "B3-A bridge correctly drops it under enforcement. "
                        "Fix: identical attribution inputs both sides; the "
                        "narrowing itself is conservative-by-design (recorded "
                        "as a limitation, not a defect).",
}
out = REPO / "agent/architecture/B4-B-F1-EVIDENCE.json"
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"equivalent": same,
                  "direct_status": comparable(direct)["status"],
                  "live_status": comparable(live)["status"],
                  "sections": comparable(direct)["sections"]}, ensure_ascii=False, indent=1))
if not same:
    raise SystemExit("DIRECT/LIVE DIVERGENCE — investigate, do not ship")
