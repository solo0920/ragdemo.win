"""B4-B eval tests: reviewed fixture frozen, links complete, metrics stable."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2d_answer as A  # noqa: E402
from app import b4b_compose as B  # noqa: E402
from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

FX = json.loads((ROOT / "tests/fixtures/b4b_answer_eval.json").read_text(encoding="utf-8"))
B2C = {c["case_id"]: c for c in
       json.loads((ROOT / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))["cases"]}
CORPUS = B1.StatuteCorpus.from_jsonl()

EXPECTED = {
    "M-01-demo-dismissal": ("ANSWERED", None),
    "M-02-detention-cause": ("ANSWERED", None),
    "M-03-demo-partial": ("ANSWERED", None),
    "M-04-reasoning-only-abstain": ("INSUFFICIENT_EVIDENCE", "INSUFFICIENT_JUDGMENT_EVIDENCE"),
    "M-05-payment-order-abstain": ("INSUFFICIENT_EVIDENCE", "NO_REASONING_EVIDENCE"),
}


def _rebuild(case):
    chunks, jid = [], None
    for cid in case["b2c_cases"]:
        for ch in B2C[cid]["chunks"]:
            chunks.append(ch)
            jid = ch["document_id"]
    cites = []
    for ch in chunks:
        cites.extend(C.extract_citations(
            ch["text"], jid=ch["document_id"], chunk_index=ch["chunk_index"],
            corpus=CORPUS))
    graph = E.build_evidence_graph(chunks, cites)
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    return B.compose_sectioned(case["question"], judgement_id=jid, graph=graph,
                               citations=cites, statute_evidences=evs,
                               **case["kwargs"])


def test_reviewed_statuses_hold():
    assert len(FX["questions"]) == 5
    for case in FX["questions"]:
        r = _rebuild(case)
        want_status, want_reason = EXPECTED[case["qid"]]
        assert r["status"] == want_status, case["qid"]
        assert r["answer"] == case["response"]["answer"], case["qid"]
        if want_reason:
            assert r["abstention"]["reason"] == want_reason, case["qid"]


def test_synthesis_links_complete():
    for case in FX["questions"]:
        for c in case["response"].get("claims", []):
            if c["type"] == "LIMITATION":
                continue
            assert c["evidence_ids"], (case["qid"], c["claim_id"])
            if c["type"] == "ANSWER_SYNTHESIS":
                assert len(c["evidence_ids"]) >= 2, (case["qid"], c["claim_id"])


def test_no_forbidden_claim_types():
    for case in FX["questions"]:
        for c in case["response"].get("claims", []):
            assert "APPLIED" not in c["type"] and "DECISIVE" not in c["type"], \
                (case["qid"], c["claim_id"])


def test_fixture_fingerprint_stable():
    fp = hashlib.sha256(
        (ROOT / "tests/fixtures/b4b_answer_eval.json").read_bytes()).hexdigest()
    ev = json.loads((ROOT / "agent/architecture/B4-B-EVIDENCE.json").read_text(encoding="utf-8"))
    assert ev["reproducibility"]["evaluation_set_fingerprint"] == fp


def test_determinism_spot_check():
    case = next(c for c in FX["questions"] if c["qid"] == "M-01-demo-dismissal")
    assert _rebuild(case)["answer"] == case["response"]["answer"]
