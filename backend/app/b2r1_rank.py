"""B2-R1: chunk-to-judgment ranking (pure, deterministic, service-free).

Product unit is the JUDGMENT; computation may occur at chunk level. This module
aggregates chunk rankings into judgment rankings with fully deterministic,
explainable rules — no LLM, no query-specific weights, no learned ranker.

Aggregation candidates (smallest explainable set):
- "max": judgement_score = max supporting chunk score.
- "top2mean": mean of the top-2 supporting chunk scores (rewards depth).

Tie-break everywhere: (-score, judgement_id) and (-score, chunk_id).
Rank-1 audit categories: TARGET / WRONG_JUDGEMENT / HARD_NEGATIVE /
NO_TARGET_IN_CUTOFF (+ UNKNOWN when unprovable).

A judgement counts as correctly retrieved (strict, product definition) iff it
is an expected judgement AND at least one of its target chunks is inside the
chunk cutoff. Surfacing an expected judgement via a non-target chunk only
(e.g. a same-doc trap) does NOT count — that is precisely the failure the
product must not ship.
"""
from __future__ import annotations

AGGREGATIONS = ("max", "top2mean")
RANK1_CATEGORIES = (
    "TARGET",
    "WRONG_JUDGEMENT",
    "HARD_NEGATIVE",
    "NO_TARGET_IN_CUTOFF",
    "UNKNOWN",
)


def aggregate_by_judgement(chunk_ranking: list[dict], *,
                           method: str = "max",
                           support_n: int = 3) -> list[dict]:
    """chunk_ranking: [{chunk_id, judgement_id, score}] (any order).

    Returns judgement rows sorted by (-score, judgement_id), each carrying
    judgement_score, top_supporting_chunks [{chunk_id, score}], chunk_scores,
    and n_chunks. Deterministic: ties broken by judgement_id; chunk order by
    (-score, chunk_id)."""
    assert method in AGGREGATIONS, method
    groups: dict[str, list[dict]] = {}
    for c in chunk_ranking:
        groups.setdefault(c["judgement_id"], []).append(c)
    rows = []
    for jid, chunks in groups.items():
        ordered = sorted(chunks, key=lambda c: (-c["score"], c["chunk_id"]))
        support = ordered[:support_n]
        if method == "max":
            score = support[0]["score"]
        else:
            top2 = support[:2]
            score = sum(c["score"] for c in top2) / len(top2)
        rows.append({
            "judgement_id": jid,
            "judgement_score": score,
            "top_supporting_chunks": [
                {"chunk_id": c["chunk_id"], "score": c["score"]} for c in support
            ],
            "chunk_scores": [c["score"] for c in ordered],
            "n_chunks": len(ordered),
        })
    rows.sort(key=lambda r: (-r["judgement_score"], r["judgement_id"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def rrf_fuse(rankings: list[list[str]], *, k: int = 60) -> list[str]:
    """Reciprocal-rank fusion over chunk-id rankings. Deterministic;
    ties broken by chunk_id. Pure rank-based: no score-scale mixing."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(fused, key=lambda c: (-fused[c], c))


def judgement_recall_at_k(judgement_ranking: list[dict], expected: set[str],
                          target_chunks: set[str], k: int) -> float:
    """Strict: an expected judgement inside top-k WITH target-chunk support
    inside its supporting chunks."""
    for row in judgement_ranking[:k]:
        if row["judgement_id"] in expected and any(
                c["chunk_id"] in target_chunks for c in row["top_supporting_chunks"]):
            return 1.0
    return 0.0


def judgement_mrr(judgement_ranking: list[dict], expected: set[str],
                  target_chunks: set[str]) -> float:
    for row in judgement_ranking:
        if row["judgement_id"] in expected and any(
                c["chunk_id"] in target_chunks for c in row["top_supporting_chunks"]):
            return 1.0 / row["rank"]
    return 0.0


def audit_rank1(judgement_ranking: list[dict], *, expected: set[str],
                target_chunks: set[str], hn_chunks: set[str],
                cutoff: int = 20) -> dict:
    """Rank-1 audit row for one query. Chunk identity stays authoritative:
    the rank-1 judgement is judged by the chunks that put it there."""
    if not judgement_ranking:
        return _row(None, None, "NO_TARGET_IN_CUTOFF", [], "empty judgement ranking")
    top = judgement_ranking[0]
    support = {c["chunk_id"] for c in top["top_supporting_chunks"]}
    in_cutoff = any(r["judgement_id"] in expected for r in judgement_ranking[:cutoff])
    if top["judgement_id"] in expected and (support & target_chunks):
        cat = "TARGET"
    elif (support & hn_chunks) and not (support & target_chunks):
        cat = "HARD_NEGATIVE"
    elif top["judgement_id"] not in expected:
        cat = "WRONG_JUDGEMENT"
    elif not in_cutoff:
        cat = "NO_TARGET_IN_CUTOFF"
    else:
        cat = "UNKNOWN"
    return _row(top["judgement_id"], top["judgement_score"], cat,
                top["top_supporting_chunks"],
                f"support={sorted(support)}")


def _row(judgement_id, score, category, supporting, evidence):
    assert category in RANK1_CATEGORIES, category
    return {"rank_1_judgement_id": judgement_id, "rank_1_score": score,
            "category": category, "supporting_chunk_ids": supporting,
            "evidence": evidence}


def index_identity(*, embedding_model: str, model_revision: str,
                   model_fingerprint: str, embedding_dimension: int,
                   distance_metric: str, chunking_configuration: str,
                   prompt_configuration: str,
                   index_configuration: str) -> dict:
    """A vector index identity bound to its embedding identity. Two models
    must never share one; callers name collections from index_id."""
    import hashlib
    basis = "|".join([embedding_model, model_revision, model_fingerprint,
                      str(embedding_dimension), distance_metric,
                      chunking_configuration, prompt_configuration,
                      index_configuration])
    return {
        "embedding_model": embedding_model,
        "model_revision": model_revision,
        "model_fingerprint": model_fingerprint,
        "embedding_dimension": embedding_dimension,
        "distance_metric": distance_metric,
        "chunking_configuration": chunking_configuration,
        "prompt_configuration": prompt_configuration,
        "index_configuration": index_configuration,
        "index_id": "b2r1-" + hashlib.sha256(basis.encode()).hexdigest()[:16],
    }
