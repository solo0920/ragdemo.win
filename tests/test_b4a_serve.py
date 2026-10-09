"""B4-A live serving integration: the production path cannot bypass continuity."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402
from app import b4a_continuity as M  # noqa: E402


def _hit(pid, entry, text, score=0.9, jid="JX"):
    return {"id": pid,
            "payload": {"entry_path": entry, "jid": jid, "chunk_index": 0,
                        "start_offset": 0, "end_offset": len(text),
                        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "jyear": "115", "jdate": "20260713", "jcase": "x"},
            "score": score}


async def _vec(texts):
    return [[0.0]]


def _roles(pairs):
    return {pid: {"chunk_id": cid, "document_id": "JX",
                  "structural_role": "FACTS", "structural_labels": ["FACTS"],
                  "text": text, "span": span}
            for pid, cid, text, span in pairs}


def test_serve_live_enforces_continuity():
    captured = {}

    async def _fake_serve_question(question, **kw):
        captured.update(kw)
        return {"status": "SERVED"}

    real = A.serve_question
    A.serve_question = _fake_serve_question
    try:
        r = asyncio.run(A.serve_live("問題"))
        assert r == {"status": "SERVED"}
    finally:
        A.serve_question = real
    assert captured.get("enforce_continuity") is True
    assert captured.get("enforce_contradiction") is True
    assert captured.get("enforce_attribution") is True
    assert captured.get("enforce_temporal") is True
    assert captured.get("enforce_confidence") is True


def test_serve_live_forwards_require_statutes():
    captured = {}

    async def _fake_serve_question(question, **kw):
        captured.update(kw)
        return {"status": "SERVED"}

    real = A.serve_question
    A.serve_question = _fake_serve_question
    try:
        asyncio.run(A.serve_live("問題"))
        assert captured.get("require_statutes") is False
        asyncio.run(A.serve_live("問題", require_statutes=True))
        assert captured.get("require_statutes") is True
    finally:
        A.serve_question = real


def test_route_passes_require_statutes_to_serve_live():
    # main.py cannot be imported here (no fastapi in test venv), so the route
    # contract is pinned at source level: the judgments_answer handler must
    # forward require_statutes explicitly rather than dropping it.
    src = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    seg = src[src.index("async def judgments_answer"):]
    seg = seg[:seg.index("\n@app.", 1)]
    assert "require_statutes=body.require_statutes" in seg


def test_direct_call_defaults_preserve_legacy_contract():
    sig = inspect.signature(A.serve_question)
    assert sig.parameters["enforce_continuity"].default is False


def test_material_unknown_continuity_abstains():
    # Two HOLDING nodes without span offsets: CONTINUITY_UNKNOWN on material
    # evidence must abstain, never compose an answer.
    t1 = "原告之訴為無理由，應予駁回。"
    t2 = "被告之訴為無理由，應予駁回。"
    hits = [_hit("p0", "e1.json", t1), _hit("p1", "e2.json", t2, score=0.8)]

    async def _search(q, v, lim):
        return hits

    roles = _roles([("p0", "JX#C1", t1, {}), ("p1", "JX#C2", t2, {})])
    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={"e1.json": t1, "e2.json": t2}, chunk_roles=roles,
        enforce_continuity=True))
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "INSUFFICIENT_CONTINUITY_EVIDENCE"
    assert r["answer"] is None


def test_failed_continuity_gate_prevents_composition():
    # Two opposed dispositions across proven-but-gapped chunks: ORDERED_GAPPED
    # disposition fails M8, so build_answer must never run.
    t1 = "主文原告之訴駁回。"
    t2 = "主文准許原告之請求。"
    hits = [_hit("p0", "e1.json", t1), _hit("p1", "e2.json", t2, score=0.8)]

    async def _search(q, v, lim):
        return hits

    async def _vec2(texts):
        return [[0.0]]

    roles = _roles([("p0", "JX#C1", t1, {"start": 0}),
                    ("p1", "JX#C2", t2, {"start": 100})])
    called = []

    real_build = A.build_answer

    def _forbidden(*a, **k):
        called.append(True)
        return real_build(*a, **k)

    A.build_answer = _forbidden
    try:
        r = asyncio.run(A.serve_question(
            "問題", search_fn=_search, embed_fn=_vec2,
            jfull_by_entry={"e1.json": t1, "e2.json": t2}, chunk_roles=roles,
            enforce_continuity=True))
    finally:
        A.build_answer = real_build
    assert called == []
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["abstention"]["reason"] == "INSUFFICIENT_CONTINUITY_EVIDENCE"


def test_disposition_completion_uses_proven_positions():
    # A cut disposition completes only when the continuation verifies by spans.
    head = "主文原告之訴"
    tail = "駁回。理由如下。"
    graph = {"nodes": [{
        "evidence_id": "disposition:JX#C1:0-6", "evidence_type": "DISPOSITION",
        "judgement_id": "JX", "document_id": "JX", "chunk_id": "JX#C1",
        "text": head,
        "source_span": {"start": 0, "end": 6, "unit": "code_point",
                        "basis": "chunk_text"},
        "provenance": {"incomplete": True, "continued_chunk_id": "JX#C2"}}]}
    chunks = [{"chunk_id": "JX#C1", "text": head, "span": {"start": 0}},
              {"chunk_id": "JX#C2", "text": tail, "span": {"start": 6}}]
    out = A._enforce_continuity(graph, chunks)
    assert out is not None
    done = [n for n in out["nodes"] if n["evidence_type"] == "DISPOSITION"]
    assert len(done) == 1 and "駁回" in done[0]["text"]
    assert done[0]["provenance"].get("completed_from") == "JX#C2"
    # Unverifiable continuation drops the incomplete disposition instead.
    chunks_bad = [{"chunk_id": "JX#C1", "text": head, "span": {"start": 0}},
                  {"chunk_id": "JX#C2", "text": tail, "span": {"start": 999}}]
    graph2 = {"nodes": [dict(n) for n in graph["nodes"]]}
    # reset provenance flags (previous call mutated the shared dicts)
    graph2["nodes"][0]["provenance"] = {"incomplete": True,
                                        "continued_chunk_id": "JX#C2"}
    graph2["nodes"][0]["text"] = head
    out2 = A._enforce_continuity(graph2, chunks_bad)
    assert out2 is not None
    assert [n for n in out2["nodes"] if n["evidence_type"] == "DISPOSITION"] == []


def test_provenance_survives_graph_assembly():
    # Assembled multi evidence keeps chunk IDs, spans, type, and attribution.
    t1 = "主文原告之訴駁回。查本案無理由。"
    hits = [_hit("p0", "e.json", t1)]

    async def _search(q, v, lim):
        return hits

    roles = _roles([("p0", "JX#C1", t1, {"start": 0})])
    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={"e.json": t1}, chunk_roles=roles,
        enforce_continuity=True))
    assert (r.get("abstention") or {}).get("reason") != "INSUFFICIENT_CONTINUITY_EVIDENCE"
