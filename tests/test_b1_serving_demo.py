"""B1 integration: real question + real judgement + real statutes, end to end.

The vector-DB roundtrip is stubbed at the search_fn boundary with payloads
built by the REAL T015/T017 code on the REAL vendored JFULL — everything else
(retrieval adapter, evidence assembly, Level-1 linking, composition, gate) runs
unmodified. Live Qdrant population is B1-FOLLOWUPS.md F2.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import b1_helpers as H
from app import b1_serve as B1


def _ahits(hits):
    async def go(q, v, lim):
        return hits
    return go


def _avec(vec):
    async def go(texts):
        return vec
    return go

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))


def test_demo_case_end_to_end_grounded():
    fx = H.load_case()
    hits = H.real_hits()  # real T017 payloads, real offsets/hashes
    r = asyncio.run(B1.serve_question(
        fx["question"],
        search_fn=_ahits(hits),
        embed_fn=_avec([[0.0] * 4]),
        jfull_by_entry=H.jfull_map(),
    ))
    assert r["status"] == "grounded", r.get("abstention")
    # B: court absent by spec, number/date/source present
    assert r["judgements"][0]["judgement_number"] == fx["document"]["JID"]
    assert r["judgements"][0]["date"] == fx["document"]["JDATE"]
    assert r["judgements"][0]["source"] == fx["entry_path"]
    # C: explicitly-cited statutes verified in corpus
    linked = {(s["law_name"], s["article"]) for s in r["statutes"]}
    for law, art in fx["expected_linked_statutes"]:
        assert (law, art) in linked, f"missing statute evidence: {law}{art}"
    # 之18 resolves to the hyphen-form insert article (flagged), never to base 436;
    # base-436 evidence comes only from the scoped 第436條第2項 mention.
    assert ("民事訴訟法", "第436-18條") in linked
    base436 = [e for e in r["evidence"]
               if e["evidence_type"] == "statute" and e["citation"] == "民事訴訟法第436條"]
    assert len(base436) == 1
    assert "之18" not in (base436[0]["provenance"]["mention_surface"] or "")
    assert base436[0]["provenance"]["scoped"] is True
    # Holdings quoted verbatim in the answer
    for holding in fx["expected_holdings"]:
        assert holding in r["answer"]
    # D/E: every claim grounded; gate passes on the final artifact
    ok, failures = B1.grounding_gate(r)
    assert ok, failures
    for claim in r["claims"]:
        assert claim["text"] in r["answer"]
        assert claim["evidence_ids"]
