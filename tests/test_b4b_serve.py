"""B4-B serving tests: sectioned mode wiring, gate enforcement, live default."""
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


def test_sectioned_mode_answers_with_sections_metadata():
    t1 = "主文原告之訴駁回。查甲理由成立。"
    t2 = "查乙理由亦成立。"
    hits = [_hit("p0", "e1.json", t1), _hit("p1", "e2.json", t2, score=0.8)]

    async def _search(q, v, lim):
        return hits

    roles = _roles([("p0", "JX#C1", t1, {"start": 0}),
                    ("p1", "JX#C2", t2, {"start": 0})])
    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={"e1.json": t1, "e2.json": t2}, chunk_roles=roles,
        answer_mode="sectioned"))
    assert r["status"] == "ANSWERED", (r.get("abstention") or {})
    assert isinstance(r.get("sections"), list) and r["sections"]
    assert r.get("omitted_sections") is not None


def test_sectioned_mode_enforces_all_gates():
    t1 = "主文原告之訴駁回。查甲理由成立。"
    hits = [_hit("p0", "e.json", t1)]

    async def _search(q, v, lim):
        return hits

    roles = _roles([("p0", "JX#C1", t1, {"start": 0})])
    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={"e.json": t1}, chunk_roles=roles,
        answer_mode="sectioned", enforce_contradiction=True,
        enforce_attribution=True, enforce_continuity=True))
    assert (r.get("abstention") or {}).get("reason") != "GROUNDING_FAILURE", \
        (r.get("abstention") or {})


def test_unknown_answer_mode_rejected():
    async def _search(q, v, lim):
        return []

    try:
        asyncio.run(A.serve_question(
            "問題", search_fn=_search, embed_fn=_vec, answer_mode="nope"))
    except ValueError:
        pass
    else:
        raise AssertionError("unknown answer_mode must raise")


def test_live_route_stays_single_by_default():
    captured = {}

    async def _fake_serve_question(question, **kw):
        captured.update(kw)
        return {"status": "SERVED"}

    real = A.serve_question
    A.serve_question = _fake_serve_question
    try:
        asyncio.run(A.serve_live("問題"))
    finally:
        A.serve_question = real
    assert captured.get("answer_mode", "single") == "single"
    assert captured.get("enforce_continuity") is True


def test_single_mode_default_preserved():
    assert inspect.signature(A.serve_question).parameters[
        "answer_mode"].default == "single"
