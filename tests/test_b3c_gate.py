"""B3-C gate + serving integration tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3c_contra as K  # noqa: E402
from app import b2d_answer as A  # noqa: E402


def test_gate_fail_abstain_pass_and_no_resolve_api():
    assert K.contradiction_gate([])["verdict"] == "PASS"
    f = K.check_dispositions([
        {"evidence_id": "d1", "judgement_id": "J", "text": "駁回。"},
        {"evidence_id": "d2", "judgement_id": "J", "text": "准許。"}])
    assert K.contradiction_gate(f)["verdict"] == "FAIL"
    u = K.check_answer_claims(
        [{"claim_id": "C1", "type": "HOLDING", "text": "處拘役肆拾日。",
          "evidence_ids": ["d1"]}],
        [{"evidence_id": "d1", "text": "處有期徒刑一年。"}])
    assert K.contradiction_gate(u)["verdict"] == "ABSTAIN"
    assert not hasattr(K, "resolve") and not hasattr(K, "adjudicate")


def test_serve_precheck_abstains_on_material_conflict():
    import hashlib
    t1 = "主文原告之訴駁回。查本案無理由。"
    t2 = "主文准許原告之請求。查本案有理由。"
    def _hit(pid, entry, text, score):
        return {"id": pid,
                "payload": {"entry_path": entry, "jid": "JX", "chunk_index": 0,
                            "start_offset": 0, "end_offset": len(text),
                            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                            "jyear": "115", "jdate": "20260713", "jcase": "x"},
                "score": score}
    hits = [_hit("p0", "e1.json", t1, 0.9), _hit("p1", "e2.json", t2, 0.8)]

    async def _search(q, v, lim):
        return hits

    async def _vec(t):
        return [[0.0]]

    roles = {"p0": {"chunk_id": "JX#C1", "document_id": "JX",
                    "structural_role": "FACTS", "structural_labels": ["FACTS"],
                    "text": t1, "span": {}},
             "p1": {"chunk_id": "JX#C2", "document_id": "JX",
                    "structural_role": "FACTS", "structural_labels": ["FACTS"],
                    "text": t2, "span": {}}}
    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={"e1.json": t1, "e2.json": t2}, chunk_roles=roles,
        enforce_contradiction=True))
    assert r["status"] == "ABSTAINED"
    assert r["abstention"]["reason"] == "CONTRADICTORY_EVIDENCE"


def test_serve_postcheck_rejects_tampered_answer():
    # Postcheck on a forged response: claim contradicts its evidence.
    resp = {"status": "ANSWERED",
            "judgment": {"judgement_id": "J", "judgement_number": "J", "date": ""},
            "claims": [{"claim_id": "C1", "type": "DISPOSITION",
                        "text": "原告之訴准許。", "evidence_ids": ["d1"]}],
            "citations": []}
    graph = {"nodes": [{"evidence_id": "d1", "evidence_type": "DISPOSITION",
                        "judgement_id": "J", "document_id": "J",
                        "chunk_id": "J#C1", "text": "原告之訴駁回。",
                        "source_span": {"start": 0, "end": 7},
                        "provenance": {}}]}
    post = A._contradiction_postcheck(resp, graph, [])
    assert post is not None and post["status"] == "ABSTAINED"
    assert post["abstention"]["reason"] == "CONTRADICTORY_EVIDENCE"


def test_clean_path_not_blocked():
    import hashlib
    text = "主文原告之訴駁回。查本案無理由。"
    h = hashlib.sha256(text.encode()).hexdigest()
    hits = [{"id": "p0",
             "payload": {"entry_path": "e.json", "jid": "JX", "chunk_index": 0,
                         "start_offset": 0, "end_offset": len(text),
                         "content_hash": h, "jyear": "115", "jdate": "20260713",
                         "jcase": "x"}, "score": 0.9}]

    async def _search(q, v, lim):
        return hits

    async def _vec(t):
        return [[0.0]]

    roles = {"p0": {"chunk_id": "JX#C1", "document_id": "JX",
                    "structural_role": "FACTS", "structural_labels": ["FACTS"],
                    "text": text, "span": {}}}
    r = asyncio.run(A.serve_question(
        "問題", search_fn=_search, embed_fn=_vec,
        jfull_by_entry={"e.json": text}, chunk_roles=roles,
        enforce_contradiction=True))
    assert (r.get("abstention") or {}).get("reason") != "CONTRADICTORY_EVIDENCE"
