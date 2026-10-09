"""B2-A: judicial retrieval accuracy evaluation helpers (pure, service-free).

Operates on plain data (golden query dicts, corpus records, ranked chunk-id
lists) so every check is deterministic and unit-testable without Qdrant,
embeddings, or downloads. Metric *semantics* mirror the frozen E05-C-B harness
(recall_at_k, MRR, nDCG, hard-negative counting); frozen *numbers* are cited
from frozen result files, never recomputed here.

Target identity rules (from the frozen Golden Set, not reinvented): a hit is a
retrieved chunk_id that is a member of the query's target chunk_id set. Title
similarity, keyword overlap, LLM judgement, and guessed equivalence are NOT
identity and are never used here.
"""
from __future__ import annotations

FAILURE_CATEGORIES = (
    "wrong_judgement",
    "target_below_cutoff",
    "hard_negative_outranks_target",
    "missing_judgement",
    "evaluation_identity_issue",
    "unknown",
)
# Taxonomy members that require proof this module cannot manufacture
# (query_normalization_failure, index_data_availability,
# retrieval_implementation_issue) are NEVER emitted without evidence;
# unprovable cases are `unknown` with the evidence attached.


def recall_at_1(ranked_ids: list[str], target_ids: set[str]) -> float:
    """Same semantics as the frozen harness: 1.0 iff rank-1 is a target."""
    if not ranked_ids:
        return 0.0
    return 1.0 if ranked_ids[0] in target_ids else 0.0


def mrr(ranked_ids: list[str], target_ids: set[str]) -> float:
    for i, cid in enumerate(ranked_ids, 1):
        if cid in target_ids:
            return 1.0 / i
    return 0.0


def audit_target_identity(queries: list[dict], corpus_chunk_ids: set[str]) -> dict:
    """R1 input: every target/HN chunk_id must resolve into the frozen corpus;
    target grades must use the frozen vocabulary (HIGH/PARTIAL/LOW per the
    frozen C-A spec; IRRELEVANT/HARD_NEGATIVE are never target grades).

    Target/HN overlap is NOT a validity failure: no frozen rule forbids it and
    the frozen 18/18 validator accepted the one known case (GQ-002 C001:
    PARTIAL disposition context AND same-doc-different-role trap). Overlap is
    reported under `observations` for answer-level batches to handle.
    Returns per-query audit."""
    out: dict[str, dict] = {}
    for q in queries:
        qid = q["query_id"]
        issues: list[str] = []
        observations: list[str] = []
        targets = [t["chunk_id"] for t in q.get("targets", [])]
        hns = [h["chunk_id"] for h in q.get("hard_negatives", [])]
        if not targets:
            issues.append("no_targets_defined")
        for cid in targets:
            if cid not in corpus_chunk_ids:
                issues.append(f"target_not_in_corpus:{cid}")
        for cid in hns:
            if cid not in corpus_chunk_ids:
                issues.append(f"hard_negative_not_in_corpus:{cid}")
        overlap = set(targets) & set(hns)
        if overlap:
            observations.append(f"target_hard_negative_overlap:{sorted(overlap)}")
        for t in q.get("targets", []):
            if t.get("relevance_grade") not in ("HIGH", "PARTIAL", "LOW"):
                issues.append(f"bad_relevance_grade:{t.get('chunk_id')}")
        out[qid] = {"valid": not issues, "issues": issues,
                    "observations": observations,
                    "n_targets": len(targets), "n_hard_negatives": len(hns)}
    return out


def classify_recall1_failure(query: dict, ranked_ids: list[str],
                             doc_of: dict[str, str | None],
                             top_k: int = 20) -> dict:
    """Classify one Recall@1 failure mechanically from frozen evidence.

    Caller checks rank-1 hits separately (recall_at_1). Never attributes to
    query normalization, index availability, or implementation without proof:
    those collapse to `unknown` with evidence attached.
    """
    qid = query["query_id"]
    targets = [t["chunk_id"] for t in query.get("targets", [])]
    hn_set = {h["chunk_id"] for h in query.get("hard_negatives", [])}
    expected_docs = set(query.get("judgement_ids", []))
    if not ranked_ids:
        return _row(qid, "unknown", None, None, None,
                    "empty ranking: no evidence to classify", "low")
    top = ranked_ids[0]
    target_rank = next((i for i, c in enumerate(ranked_ids, 1) if c in targets), None)
    if top in hn_set:
        return _row(qid, "hard_negative_outranks_target", None, top, target_rank,
                    f"rank-1 {top} is a declared hard negative", "high")
    for cid in targets:
        if cid not in doc_of:
            return _row(qid, "evaluation_identity_issue", None, top, None,
                        f"target {cid} absent from corpus index", "high")
    ranked_docs = {doc_of[c] for c in ranked_ids if doc_of.get(c)}
    if not (ranked_docs & expected_docs):
        return _row(qid, "missing_judgement", None, top, None,
                    "no chunk of any expected judgement in ranking", "high")
    top_doc = doc_of.get(top)
    if top_doc is not None and top_doc not in expected_docs:
        return _row(qid, "wrong_judgement", top_doc, top, target_rank,
                    f"rank-1 doc {top_doc} not in expected {sorted(expected_docs)}", "high")
    if target_rank is not None and target_rank <= top_k:
        return _row(qid, "target_below_cutoff", top_doc, top, target_rank,
                    f"target at rank {target_rank}", "high")
    return _row(qid, "unknown", top_doc, top, target_rank,
                "no mechanical rule fires; cause unproven", "low")


def _row(qid, category, expected, top_result, rank_of_target, evidence, confidence):
    assert category in FAILURE_CATEGORIES, category
    return {"query_id": qid, "category": category, "expected": expected,
            "top_result": top_result, "rank_of_target": rank_of_target,
            "evidence": evidence, "confidence": confidence,
            "retrieval_status": f"MISS:{category}"}


def provenance_ok(record: dict) -> tuple[bool, list[str]]:
    """R5 input: a ranked record must carry chunk identity + score.

    Note (observation, not failure): frozen experiment records carry
    document_id=null; the chunk_id itself embeds the judgement identity and the
    serving path (judgment_hit_view) carries full provenance. Null document_id
    is reported, never silently patched.
    """
    problems: list[str] = []
    if not record.get("chunk_id"):
        problems.append("missing chunk_id")
    if not isinstance(record.get("score"), (int, float)):
        problems.append("missing/non-numeric score")
    notes = []
    if record.get("document_id") is None:
        notes.append("document_id null in record (chunk_id still identifies doc)")
    return (len(problems) == 0), problems + notes


def gate_r1_to_r5(*, identity_audit: dict, runs_agree: bool | None,
                  hn_threshold: float | None, hn_reference: dict,
                  failure_table: list[dict], total_failures: int,
                  provenance_result: dict) -> dict:
    """Mechanical retrieval gate R1-R5. No invented thresholds: any gate whose
    PASS condition needs an undeclared threshold returns STOP with the exact
    missing evidence named."""
    invalid = {qid: a for qid, a in identity_audit.items() if not a["valid"]}
    gates = {}
    gates["R1"] = _verdict(
        not invalid, "PASS" if not invalid else "FAIL",
        f"{len(identity_audit) - len(invalid)}/{len(identity_audit)} queries identity-valid"
        + (f"; invalid: {sorted(invalid)}" if invalid else ""))
    if runs_agree is None:
        gates["R2"] = _verdict(False, "STOP", "no run1/run2 agreement evidence supplied")
    else:
        gates["R2"] = _verdict(
            runs_agree, "PASS" if runs_agree else "FAIL",
            "frozen run1==run2 ranked lists" if runs_agree else "runs disagree")
    if hn_threshold is None:
        gates["R3"] = _verdict(
            False, "STOP",
            f"no hard-negative threshold declared; reference rates recorded {hn_reference}; "
            "no candidate retriever in this batch to regress")
    else:
        worst = max(hn_reference.values()) if hn_reference else 0.0
        gates["R3"] = _verdict(
            worst <= hn_threshold, "PASS" if worst <= hn_threshold else "FAIL",
            f"worst HN rate {worst} vs threshold {hn_threshold}")
    unrowed = total_failures - len(failure_table)
    gates["R4"] = _verdict(
        unrowed == 0, "PASS" if unrowed == 0 else "FAIL",
        f"{len(failure_table)}/{total_failures} failures classified with evidence"
        + (f"; {unrowed} missing rows" if unrowed else ""))
    bad = {k: v for k, v in provenance_result.items() if v.get("problems")}
    gates["R5"] = _verdict(
        not bad, "PASS" if not bad else "FAIL",
        "all ranked records carry chunk_id+score" if not bad else f"provenance loss: {sorted(bad)}")
    return gates


def _verdict(ok: bool, state: str, evidence: str) -> dict:
    assert state in ("PASS", "FAIL", "STOP"), state
    return {"verdict": state, "satisfied": ok, "evidence": evidence}
