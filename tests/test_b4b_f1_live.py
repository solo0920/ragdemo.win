"""B4-B-F1: live adoption of sectioned mode — route contract, forwarding, gates."""
from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402


def _hit(pid, entry, text, score=0.9, jid="JX"):
    return {"id": pid,
            "payload": {"entry_path": entry, "jid": jid, "chunk_index": 0,
                        "start_offset": 0, "end_offset": len(text),
                        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "jyear": "115", "jdate": "20260713", "jcase": "x"},
            "score": score}


async def _vec(texts):
    return [[0.0]]


def test_live_forwards_mode_and_sections():
    captured = {}

    async def _fake_serve_question(question, **kw):
        captured.update(kw)
        return {"status": "SERVED"}

    real = A.serve_question
    A.serve_question = _fake_serve_question
    try:
        asyncio.run(A.serve_live("問題", answer_mode="sectioned",
                                 sections=["DISPOSITION", "REASONING"]))
    finally:
        A.serve_question = real
    assert captured.get("answer_mode") == "sectioned"
    assert captured.get("sections") == ["DISPOSITION", "REASONING"]
    assert captured.get("enforce_continuity") is True
    assert captured.get("enforce_contradiction") is True


def test_live_single_with_sections_rejected():
    try:
        asyncio.run(A.serve_live("問題", sections=["DISPOSITION"]))
    except ValueError:
        pass
    else:
        raise AssertionError("single+sections must fail fast")


def test_serve_question_single_with_sections_rejected():
    async def _search(q, v, lim):
        return []

    try:
        asyncio.run(A.serve_question(
            "問題", search_fn=_search, embed_fn=_vec, sections=["DISPOSITION"]))
    except ValueError:
        pass
    else:
        raise AssertionError("single+sections must fail fast")


def test_route_source_forwards_mode_and_validates():
    # main.py cannot be imported here (no fastapi in test venv); the route
    # contract is pinned at source level instead.
    src = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    assert 'answer_mode: Literal["single", "sectioned"]' in src
    seg = src[src.index("async def judgments_answer"):]
    seg = seg[:seg.index("\n@app.", 1)]
    assert "answer_mode=body.answer_mode" in seg
    assert "sections=body.sections" in seg
    assert "status_code=400" in seg
    assert "SECTION_KINDS" in seg


def test_sectioned_live_answer_carries_sections():
    t1 = "主文原告之訴駁回。查甲理由成立。"
    t2 = "查乙理由亦成立。"
    hits = [_hit("p0", "e1.json", t1), _hit("p1", "e2.json", t2, score=0.8)]

    async def _search(q, v, lim):
        return hits

    roles = {"p0": {"chunk_id": "JX#C1", "document_id": "JX",
                    "structural_role": "FACTS", "structural_labels": ["FACTS"],
                    "text": t1, "span": {"start": 0}},
             "p1": {"chunk_id": "JX#C2", "document_id": "JX",
                    "structural_role": "FACTS", "structural_labels": ["FACTS"],
                    "text": t2, "span": {"start": 0}}}
    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={"e1.json": t1, "e2.json": t2}, chunk_roles=roles,
        answer_mode="sectioned"))
    assert r["status"] == "ANSWERED", (r.get("abstention") or {})
    assert isinstance(r.get("sections"), list) and r["sections"]
    assert any(c["type"] == "ANSWER_SYNTHESIS" for c in r["claims"])


def test_sectioned_subset_scope_enforced_live():
    t1 = "主文原告之訴駁回。查甲理由成立。"
    hits = [_hit("p0", "e.json", t1)]

    async def _search(q, v, lim):
        return hits

    roles = {"p0": {"chunk_id": "JX#C1", "document_id": "JX",
                    "structural_role": "FACTS", "structural_labels": ["FACTS"],
                    "text": t1, "span": {"start": 0}}}
    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={"e.json": t1}, chunk_roles=roles,
        answer_mode="sectioned", sections=["DISPOSITION", "REASONING"]))
    assert r["status"] == "ANSWERED", (r.get("abstention") or {})
    assert {s["kind"] for s in r["sections"]} == {"DISPOSITION", "REASONING"}
    assert r["statutes"] == [] and r["citations"] == []
