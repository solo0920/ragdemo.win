"""B2-R1 unit: aggregation determinism, fusion, judgement metrics, rank-1 audit."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b2r1_rank as R  # noqa: E402

CHUNKS = [
    {"chunk_id": "J1#C002", "judgement_id": "J1", "score": 0.9},
    {"chunk_id": "J2#C001", "judgement_id": "J2", "score": 0.9},
    {"chunk_id": "J1#C001", "judgement_id": "J1", "score": 0.7},
    {"chunk_id": "J3#C001", "judgement_id": "J3", "score": 0.2},
]


def test_max_aggregation_and_tiebreak():
    rows = R.aggregate_by_judgement(CHUNKS, method="max")
    assert [r["judgement_id"] for r in rows] == ["J1", "J2", "J3"]  # J1 before J2 on tie
    assert rows[0]["judgement_score"] == 0.9
    assert rows[0]["top_supporting_chunks"][0]["chunk_id"] == "J1#C002"
    assert rows[0]["rank"] == 1 and rows[0]["n_chunks"] == 2


def test_top2mean_rewards_depth_and_is_deterministic():
    rows = R.aggregate_by_judgement(CHUNKS, method="top2mean")
    j1 = next(r for r in rows if r["judgement_id"] == "J1")
    assert j1["judgement_score"] == (0.9 + 0.7) / 2
    again = R.aggregate_by_judgement(list(reversed(CHUNKS)), method="top2mean")
    assert [(r["judgement_id"], r["judgement_score"]) for r in rows] == [
        (r["judgement_id"], r["judgement_score"]) for r in again]


def test_chunk_provenance_survives_aggregation():
    rows = R.aggregate_by_judgement(CHUNKS)
    for r in rows:
        assert r["top_supporting_chunks"]
        assert all("chunk_id" in c and "score" in c for c in r["top_supporting_chunks"])


def test_rrf_fusion_is_deterministic_and_rank_based():
    fused = R.rrf_fuse([["a", "b", "c"], ["b", "a", "d"]])
    # a: 1/61+1/62 == b: 1/62+1/61 -> tie broken by chunk_id, not input order
    assert fused[:2] == ["a", "b"]
    assert set(fused) == {"a", "b", "c", "d"}
    assert R.rrf_fuse([["a", "b"], ["a", "b"]]) == R.rrf_fuse([["a", "b"], ["a", "b"]])
    # tie broken by chunk_id, not input order
    assert R.rrf_fuse([["x", "y"], ["y", "x"]])[0] == "x"


def test_judgement_recall_requires_target_support():
    rows = R.aggregate_by_judgement(CHUNKS, method="max")
    # J1 top-ranked via target chunk -> strict hit
    assert R.judgement_recall_at_k(rows, {"J1"}, {"J1#C002"}, 1) == 1.0
    # J1 top-ranked but only via non-target chunk -> NOT a hit
    assert R.judgement_recall_at_k(rows, {"J1"}, {"J1#C009"}, 1) == 0.0
    assert R.judgement_mrr(rows, {"J1"}, {"J1#C009"}) == 0.0


def _ranked():
    return R.aggregate_by_judgement(CHUNKS, method="max")


def test_rank1_target():
    a = R.audit_rank1(_ranked(), expected={"J1"}, target_chunks={"J1#C002"},
                      hn_chunks={"J1#C001"})
    assert a["category"] == "TARGET"


def test_rank1_hard_negative_beats_unsupported_expected():
    rows = R.aggregate_by_judgement([
        {"chunk_id": "J1#C001", "judgement_id": "J1", "score": 0.95},
        {"chunk_id": "J2#C001", "judgement_id": "J2", "score": 0.5},
    ])
    a = R.audit_rank1(rows, expected={"J2"}, target_chunks={"J2#C001"},
                      hn_chunks={"J1#C001"})
    assert a["category"] == "HARD_NEGATIVE"


def test_rank1_wrong_and_no_target_and_unknown():
    rows = R.aggregate_by_judgement([
        {"chunk_id": "J9#C001", "judgement_id": "J9", "score": 0.95},
        {"chunk_id": "J2#C001", "judgement_id": "J2", "score": 0.5},
    ])
    a = R.audit_rank1(rows, expected={"J2"}, target_chunks={"J2#C001"}, hn_chunks=set())
    assert a["category"] == "WRONG_JUDGEMENT"
    a = R.audit_rank1([], expected={"J2"}, target_chunks={"J2#C001"}, hn_chunks=set())
    assert a["category"] == "NO_TARGET_IN_CUTOFF"
    # expected judgement top-ranked via non-target, non-HN chunk, target in cutoff
    # only via deeper support... construct: top J2 via C009, target C001 deeper
    rows = R.aggregate_by_judgement([
        {"chunk_id": "J2#C009", "judgement_id": "J2", "score": 0.95},
    ])
    a = R.audit_rank1(rows, expected={"J2"}, target_chunks={"J2#C001"},
                      hn_chunks=set(), cutoff=1)
    assert a["category"] == "UNKNOWN"


def test_index_identity_separates_models():
    a = R.index_identity(embedding_model="m1", model_revision="r",
                         model_fingerprint="f1", embedding_dimension=768,
                         distance_metric="cosine", chunking_configuration="c",
                         prompt_configuration="p", index_configuration="qdrant")
    b = dict(a)
    b2 = R.index_identity(embedding_model="m2", model_revision="r",
                          model_fingerprint="f1", embedding_dimension=768,
                          distance_metric="cosine", chunking_configuration="c",
                          prompt_configuration="p", index_configuration="qdrant")
    assert a["index_id"] != b2["index_id"]
    assert R.index_identity(**{k: v for k, v in a.items() if k != "index_id"})["index_id"] == a["index_id"]
