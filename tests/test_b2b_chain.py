"""B2-B end-to-end chain: frozen rank-1 judgement -> citations -> statutes.

Hermetic: all data vendored in tests/fixtures/b2b_chain.json (frozen BM25
rank-1 chunk IDs + full chunk texts of those judgements). No services, no
~/artifacts dependency. Proves the product chain on real retrieval output:
the statutes come from the retrieved judgement, never from the question.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

CHAIN = json.loads((ROOT / "tests/fixtures/b2b_chain.json").read_text(encoding="utf-8"))
CORPUS = B1.StatuteCorpus.from_jsonl()


def run_chain(entry):
    cites = []
    for ch in entry["chunks"]:
        cites.extend(C.extract_citations(
            ch["text"], jid=entry["judgement_id"],
            chunk_index=ch["chunk_index"], corpus=CORPUS))
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    ok, failures = C.statute_gate(cites, evs, CORPUS)
    return cites, evs, ok, failures


def test_rank1_judgement_yields_resolved_statutes_with_gate_pass():
    for entry in CHAIN["queries"]:
        cites, evs, ok, failures = run_chain(entry)
        resolved = [c for c in cites if c["resolution_status"] == "RESOLVED"]
        assert resolved, f"{entry['query_id']}: rank-1 judgement yields no statute"
        assert ok, f"{entry['query_id']}: gate failures {failures}"
        # every statute traces to this judgement's chunks only
        assert {c["judgement_id"] for c in cites} == {entry["judgement_id"]}


def test_unresolved_explicitly_represented_in_chain():
    for entry in CHAIN["queries"]:
        cites, _, _, _ = run_chain(entry)
        unres = [c for c in cites if c["resolution_status"].startswith("UNRESOLVED")]
        for c in unres:
            assert c["evidence_id"] is None and c["source_span"]["start"] < c["source_span"]["end"]


def test_statute_selection_ignores_the_question():
    # Same judgement hits, two different questions -> identical statute sets.
    # (b1_serve takes the question for RETRIEVAL only; linking sees hits.)
    import hashlib
    import app.b1_serve as S
    text = "依民法第184條第1項請求。"
    hits = [{"id": "p", "payload": {
        "entry_path": "e.json", "jid": "JX", "chunk_index": 0,
        "start_offset": 0, "end_offset": len(text),
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "jyear": "115", "jdate": "20260713", "jcase": "店小"}, "score": 0.9}]

    async def _search(q, v, lim):
        return hits

    async def _embed(texts):
        return [[0.0]]

    async def go(question):
        return await S.serve_question(
            question,
            search_fn=_search,
            embed_fn=_embed,
            jfull_by_entry={"e.json": text},
            statute_corpus=CORPUS)

    r1 = asyncio.run(go("精神慰撫金三萬元可以請求嗎"))
    r2 = asyncio.run(go("完全不同的問題關於車禍賠償"))
    assert r1["status"] == r2["status"] == "grounded"
    assert [(s["law_name"], s["article"]) for s in r1["statutes"]] == \
           [(s["law_name"], s["article"]) for s in r2["statutes"]]


def test_linker_takes_no_question():
    sig = inspect.signature(C.extract_citations)
    assert "question" not in sig.parameters and "query" not in sig.parameters
