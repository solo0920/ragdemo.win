"""B2-E tests: reviewed evaluation set is frozen, statuses hold, metrics stable."""
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
from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

FX = json.loads((ROOT / "tests/fixtures/b2e_answer_eval.json").read_text(encoding="utf-8"))
CORPUS = B1.StatuteCorpus.from_jsonl()

EXPECTED = {
    "E-01": ("ANSWERED", None), "E-02": ("ANSWERED", None),
    "E-03": ("ANSWERED", None), "E-04": ("ANSWERED", None),
    "E-05": ("ANSWERED", None),
    "E-06": ("ANSWERED", None), "E-07": ("ANSWERED", None),
    "E-08": ("INSUFFICIENT_EVIDENCE", "INSUFFICIENT_JUDGMENT_EVIDENCE"),
    "E-09": ("INSUFFICIENT_EVIDENCE", "NO_REASONING_EVIDENCE"),
    "E-10": ("INSUFFICIENT_EVIDENCE", "INSUFFICIENT_JUDGMENT_EVIDENCE"),
}


def _rebuild(case):
    jid = case["selected_judgement_id"]
    cites = []
    for ch in case["chunks"]:
        cites.extend(C.extract_citations(
            ch["text"], jid=ch.get("document_id", jid),
            chunk_index=ch.get("chunk_index", 0), corpus=CORPUS))
    graph = E.build_evidence_graph(case["chunks"], cites)
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    return A.build_answer(case["question"], judgement_id=jid, graph=graph,
                          citations=cites, statute_evidences=evs,
                          require_statutes=case.get("require_statutes", False))


def test_reviewed_statuses_hold():
    assert len(FX["cases"]) == 10
    for case in FX["cases"]:
        r = _rebuild(case)
        want_status, want_reason = EXPECTED[case["qid"]]
        assert r["status"] == want_status, case["qid"]
        assert r["answer"] == case["response"]["answer"], case["qid"]
        if want_reason:
            assert r["abstention"]["reason"] == want_reason, case["qid"]


def test_zero_unsupported_material_claims():
    for case in FX["cases"]:
        for c in case["response"].get("claims", []):
            if c["type"] == "LIMITATION":
                continue
            assert c["evidence_ids"], (case["qid"], c["claim_id"])


def test_categories_cover_positive_and_abstention():
    cats = {c["category"] for c in FX["cases"]}
    assert {"SUPPORTED_WITH_STATUTES", "OUTSIDE_SUPPORTED_SCOPE",
            "INSUFFICIENT_JUDGMENT", "INSUFFICIENT_REASONING"} <= cats


def test_fixture_fingerprint_stable():
    fp = hashlib.sha256(
        (ROOT / "tests/fixtures/b2e_answer_eval.json").read_bytes()).hexdigest()
    ev = json.loads((ROOT / "agent/architecture/B2-E-EVIDENCE.json").read_text(encoding="utf-8"))
    assert ev["reproducibility"]["evaluation_set_fingerprint"] == fp


def test_determinism_spot_check():
    case = next(c for c in FX["cases"] if c["qid"] == "E-04")
    assert _rebuild(case)["answer"] == case["response"]["answer"]
