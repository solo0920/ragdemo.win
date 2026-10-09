"""B2-C integration: retrieval-selected judgement -> graph -> B2-B statutes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

SET = json.loads((ROOT / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))
CHAIN = json.loads((ROOT / "tests/fixtures/b2b_chain.json").read_text(encoding="utf-8"))
CORPUS = B1.StatuteCorpus.from_jsonl()
_EDGES = ("same_chunk", "contains_citation", "same_document")


def _full_chain(entry):
    chunks = []
    for i, ch in enumerate(entry["chunks"]):
        chunks.append({"chunk_id": ch["chunk_id"], "document_id": entry["judgement_id"],
                       "structural_role": "FACTS", "structural_labels": ["FACTS"],
                       "text": ch["text"], "span": {},
                       "carries_declared_disposition_unit": False,
                       "chunk_index": ch.get("chunk_index", i)})
    cites = []
    for ch in chunks:
        cites.extend(C.extract_citations(
            ch["text"], jid=entry["judgement_id"],
            chunk_index=ch["chunk_index"], corpus=CORPUS))
    graph = E.build_evidence_graph(chunks, cites)
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    ok_c, _ = C.statute_gate(cites, evs, CORPUS)
    ok_g, gfails = E.reasoning_gate(
        graph, {ch["chunk_id"]: ch["text"] for ch in chunks})
    return graph, cites, evs, ok_c, ok_g, gfails


def test_b2r1_judgement_to_statutes_chain():
    # Judgements selected by the frozen BM25 rank-1 path (vendored in
    # b2b_chain.json) produce gated graphs AND gated statute mappings.
    for entry in CHAIN["queries"]:
        graph, cites, evs, ok_c, ok_g, gfails = _full_chain(entry)
        assert evs, entry["query_id"]  # at least one exact statute evidence
        assert ok_c and ok_g, (entry["query_id"], gfails)
        assert {c["judgement_id"] for c in cites} == {entry["judgement_id"]}
        assert all(e["relation"] in _EDGES for e in graph["edges"])
        assert not any(e["relation"] in ("applied_statute", "decisive_statute",
                                         "applies", "decides") for e in graph["edges"])


def test_contains_citation_links_reasoning_to_statutes():
    found = [e for case in SET["cases"] for e in case["graph"]["edges"]
             if e["relation"] == "contains_citation"]
    assert found  # R-02 and R-07 carry them (frozen in the set)
    for e in found:
        assert e["from"] != e["to"]


def test_b1_demo_full_evidence_graph():
    demo = [c for c in SET["cases"] if c["case_id"] in ("R-06-demo-disposition",
                                                       "R-07-demo-holding")]
    types = {n["evidence_type"] for c in demo for n in c["graph"]["nodes"]}
    assert {"DISPOSITION", "HOLDING", "REASONING"} <= types
    for c in demo:
        assert c["gate"]["verdict"] == "PASS"
