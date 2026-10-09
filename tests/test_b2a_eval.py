"""B2-A unit: target matching semantics, identity audit, failure classification."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2a_eval as E  # noqa: E402


def q(qid="GQ-X", targets=("D1#C002",), hns=("D1#C001",), docs=("D1",)):
    return {"query_id": qid,
            "targets": [{"chunk_id": t, "relevance_grade": "HIGH"} for t in targets],
            "hard_negatives": [{"chunk_id": h} for h in hns],
            "judgement_ids": list(docs)}


def test_recall_at_1_is_chunk_identity_not_similarity():
    assert E.recall_at_1(["D1#C001"], {"D1#C002"}) == 0.0  # same doc, wrong chunk
    assert E.recall_at_1(["D1#C002"], {"D1#C002"}) == 1.0
    assert E.recall_at_1([], {"D1#C002"}) == 0.0


def test_mrr_uses_first_target_rank():
    assert E.mrr(["A", "D1#C002"], {"D1#C002"}) == 0.5
    assert E.mrr(["A"], {"D1#C002"}) == 0.0


def test_identity_audit_accepts_frozen_grades():
    audit = E.audit_target_identity(
        [q(targets=("D1#C001",)) | {"targets": [
            {"chunk_id": "D1#C001", "relevance_grade": "PARTIAL"}]}],
        {"D1#C001"})
    assert audit["GQ-X"]["valid"]


def test_identity_audit_rejects_forbidden_grade_and_missing_chunk():
    audit = E.audit_target_identity(
        [q(targets=("D9#C001",)) | {"targets": [
            {"chunk_id": "D9#C001", "relevance_grade": "IRRELEVANT"}]}],
        {"D1#C001"})
    assert not audit["GQ-X"]["valid"]
    assert any("target_not_in_corpus" in i for i in audit["GQ-X"]["issues"])
    assert any("bad_relevance_grade" in i for i in audit["GQ-X"]["issues"])


def test_overlap_is_observation_not_failure():
    audit = E.audit_target_identity(
        [q(targets=("D1#C001",), hns=("D1#C001",))], {"D1#C001", "D1#C002"})
    assert audit["GQ-X"]["valid"]
    assert any("overlap" in o for o in audit["GQ-X"]["observations"])


DOC = {"D1#C001": "D1", "D1#C002": "D1", "D2#C001": "D2"}


def test_hn_outranks_target_takes_precedence():
    row = E.classify_recall1_failure(q(), ["D1#C001", "D1#C002"], DOC)
    assert row["category"] == "hard_negative_outranks_target"
    assert row["confidence"] == "high"


def test_wrong_judgement_and_below_cutoff():
    row = E.classify_recall1_failure(q(hns=()), ["D2#C001", "D1#C002"], DOC)
    assert row["category"] == "wrong_judgement" and row["rank_of_target"] == 2
    row = E.classify_recall1_failure(
        q(hns=(), docs=("D1", "D2")), ["D2#C001", "D1#C002"], DOC)
    assert row["category"] == "target_below_cutoff"


def test_missing_judgement_and_identity_issue():
    row = E.classify_recall1_failure(q(), ["D2#C001"], DOC)
    assert row["category"] == "missing_judgement"
    row = E.classify_recall1_failure(q(targets=("D9#C009",)), ["D2#C001"], DOC)
    assert row["category"] == "evaluation_identity_issue"


def test_cutoff_boundary_and_empty_ranking():
    row = E.classify_recall1_failure(
        q(hns=(), docs=("D1", "D2")), ["D2#C001", "D2#C002", "D1#C002"], DOC, top_k=2)
    assert row["category"] == "unknown" and row["confidence"] == "low"
    row = E.classify_recall1_failure(q(), [], DOC)
    assert row["category"] == "unknown"


def test_classifier_is_deterministic():
    a = E.classify_recall1_failure(q(), ["D2#C001", "D1#C002"], DOC)
    b = E.classify_recall1_failure(q(), ["D2#C001", "D1#C002"], DOC)
    assert a == b
