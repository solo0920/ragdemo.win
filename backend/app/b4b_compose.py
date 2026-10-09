"""B4-B: multi-evidence sectioned answer composition (extractive, deterministic).

Thin layer over B2-D build_answer: identical sufficiency/abstention contract,
plus per-section ANSWER_SYNTHESIS claims (verbatim joins only) and explicit
sections/omitted_sections metadata. No free text, no retrieval, no LLM,
no new legal relations — a synthesis claim asserts only that its member
evidence items jointly belong to one answer section.

Scope control: sections subset pre-filters the evidence graph before the
unchanged pipeline runs, so sufficiency, attribution, temporal rules, and all
gates apply to exactly the requested scope.
"""
from __future__ import annotations

from .b2d_answer import (
    ANSWERED,
    answer_gate,
    build_answer,
)

SECTION_KINDS = ("DISPOSITION", "HOLDING", "REASONING", "STATUTES")
_CLAIM_TO_SECTION = {
    "DISPOSITION": "DISPOSITION",
    "HOLDING": "HOLDING",
    "REASONING": "REASONING",
    "STATUTE": "STATUTES",
}


def compose_sectioned(question: str, *, judgement_id: str, graph: dict,
                      citations: list[dict], statute_evidences: list[dict],
                      sections: list | None = None, jdate: str = "",
                      attribution: dict | None = None,
                      temporal: dict | None = None,
                      require_statutes: bool = False) -> dict:
    """Compose a sectioned multi-evidence answer from authorized evidence.

    sections: requested section kinds (subset of SECTION_KINDS; None means
    all). Unknown kinds raise ValueError (programmer error). Kinds outside
    the request are neither composed nor reported as omitted.
    """
    if sections is None:
        requested = list(SECTION_KINDS)
    else:
        requested = list(sections)
        for kind in requested:
            if kind not in SECTION_KINDS:
                raise ValueError(f"unknown section kind: {kind!r}")
    scoped_graph = {"judgement_ids": graph.get("judgement_ids", []),
                    "nodes": [n for n in graph.get("nodes", [])
                              if _CLAIM_TO_SECTION.get(n.get("evidence_type"), "")
                              in requested],
                    "edges": graph.get("edges", [])}
    if "STATUTES" in requested:
        scoped_cites, scoped_evs = citations, statute_evidences
    else:
        scoped_cites, scoped_evs = [], []
    resp = build_answer(question, judgement_id=judgement_id, graph=scoped_graph,
                        citations=scoped_cites, statute_evidences=scoped_evs,
                        require_statutes=require_statutes, jdate=jdate,
                        attribution=attribution, temporal=temporal)
    if resp.get("status") != ANSWERED:
        return {**resp, "sections": [],
                "omitted_sections": [
                    {"kind": k,
                     "reason": (resp.get("abstention") or {}).get("reason", "")}
                    for k in requested]}
    by_section: dict[str, list[dict]] = {k: [] for k in requested}
    for c in resp["claims"]:
        if c.get("type") == "LIMITATION":
            continue
        by_section.setdefault(_CLAIM_TO_SECTION.get(c.get("type"), ""), []).append(c)
    sections_out, omitted, synth_claims = [], [], []
    seq = len(resp["claims"])
    for kind in requested:
        members = by_section.get(kind, [])
        if not members:
            omitted.append({"kind": kind, "reason": "no eligible evidence"})
            continue
        eids: list[str] = []
        for c in members:
            for eid in c.get("evidence_ids", []):
                if eid not in eids:
                    eids.append(eid)
        entry: dict = {"section_id": f"S-{kind}", "kind": kind,
                       "claim_ids": [c["claim_id"] for c in members],
                       "evidence_ids": eids, "synthesis_claim_id": None}
        if len(members) >= 2:
            seq += 1
            sid = f"C{seq}"
            synth_claims.append({"claim_id": sid, "type": "ANSWER_SYNTHESIS",
                                 "text": "\n".join(c["text"] for c in members),
                                 "evidence_ids": eids})
            entry["synthesis_claim_id"] = sid
        sections_out.append(entry)
    resp["claims"] = resp["claims"] + synth_claims
    resp["sections"] = sections_out
    resp["omitted_sections"] = omitted
    # Final gate over the extended response (defense in depth): synthesis
    # claims verify against the same evidence texts.
    texts = {n["evidence_id"]: n.get("text", "") for n in scoped_graph["nodes"]}
    for e in scoped_evs:
        texts[e["evidence_id"]] = e.get("text", "")
    ok, failures = answer_gate(resp, selected_judgement_id=judgement_id,
                               citations=scoped_cites, evidence_texts=texts)
    if not ok:
        from .b2d_answer import _abstain
        return {**_abstain("GROUNDING_FAILURE", gate_failures=failures),
                "sections": [],
                "omitted_sections": [{"kind": k, "reason": "GROUNDING_FAILURE"}
                                     for k in requested]}
    return resp
