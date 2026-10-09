"""B3-D: judgment statute attribution and court-use boundary (deterministic).

Binds each B2-B citation to a B3-A attribution status, separating five
dimensions that must never upgrade each other: citation existence, speaker
attribution, corpus resolution, temporal status, legal application.

Statuses (citation speaker vocabulary):
  COURT_VOICE_EXPLICIT_CITATION / PARTY_ATTRIBUTION /
  PROSECUTOR_OR_INDICTMENT_ATTRIBUTION / QUOTED_STATUTE /
  QUOTED_OTHER_JUDGMENT / UNRESOLVED_ATTRIBUTION
Only COURT_VOICE_EXPLICIT_CITATION supports material court claims
(`court_used`). Everything else is displayable solely with its honest
actor label, or as an unattributed mention. APPLIED/DECISIVE_STATUTE are
never emitted here: no relation in this module proves them.

Court-use rule (replicates the B2-D span bridge exactly, then adds the
speaker override): a citation is court-used iff some attribution-COURT
evidence node covers its span (same text containment + span containment),
AND its own sentence carries no explicit non-court speaker
(PARTY/prosecutor/quoted/procedural). A sentence that merely fails to
classify (R-UNKNOWN) never overrides a covering node; a missing covering
node is never repaired by a court-voiced sentence (no recall invention).
"""
from __future__ import annotations

COURT_VOICE_EXPLICIT_CITATION = "COURT_VOICE_EXPLICIT_CITATION"
PARTY_ATTRIBUTION = "PARTY_ATTRIBUTION"
PROSECUTOR_OR_INDICTMENT_ATTRIBUTION = "PROSECUTOR_OR_INDICTMENT_ATTRIBUTION"
QUOTED_STATUTE = "QUOTED_STATUTE"
QUOTED_OTHER_JUDGMENT = "QUOTED_OTHER_JUDGMENT"
UNRESOLVED_ATTRIBUTION = "UNRESOLVED_ATTRIBUTION"

STATUSES = (COURT_VOICE_EXPLICIT_CITATION, PARTY_ATTRIBUTION,
            PROSECUTOR_OR_INDICTMENT_ATTRIBUTION, QUOTED_STATUTE,
            QUOTED_OTHER_JUDGMENT, UNRESOLVED_ATTRIBUTION)

_DISPLAY = {
    COURT_VOICE_EXPLICIT_CITATION: "court_citation",
    PARTY_ATTRIBUTION: "party_citation",
    PROSECUTOR_OR_INDICTMENT_ATTRIBUTION: "prosecution_citation",
    QUOTED_STATUTE: "quoted_statute",
    QUOTED_OTHER_JUDGMENT: "quoted_judgment_citation",
    UNRESOLVED_ATTRIBUTION: "mention_only",
}

# Sentence-level speaker evidence overrides a covering node. R-UNKNOWN and
# R-COURT/R-DISP carry no speaker information and never override.
_OVERRIDE = {
    "PARTY_VOICE": PARTY_ATTRIBUTION,
    "QUOTED_STATUTE": QUOTED_STATUTE,
    "QUOTED_OTHER_JUDGMENT": QUOTED_OTHER_JUDGMENT,
    "PROCEDURAL_DESCRIPTION": UNRESOLVED_ATTRIBUTION,
}


def link_citation(citation: dict, nodes: list[dict],
                  attribution_map: dict | None,
                  temporal_map: dict | None = None) -> dict:
    """One citation -> attribution relation. Never raises on data shape;
    unprovable attribution yields UNRESOLVED_ATTRIBUTION, never a guess."""
    temporal_map = temporal_map or {}
    norm = citation.get("normalized", {}) or {}
    ev_text = citation.get("evidence_text", "") or ""
    # 1. Covering test: attribution-COURT node containing the citation
    # (exact B2-D bridge semantics: text containment + span containment).
    covering = None
    if attribution_map is not None:
        pool = [n for n in nodes
                if attribution_map.get(n.get("evidence_id", "")) == "COURT_VOICE"]
    else:
        pool = list(nodes)
    by_text: dict[str, list[dict]] = {}
    for n in pool:
        by_text.setdefault(n.get("text", ""), []).append(n)
    cspan = citation.get("source_span", {}) or {}
    for text, ns in by_text.items():
        if not ev_text or ev_text not in text:
            continue
        for n in ns:
            nsp = n.get("source_span", {}) or {}
            if nsp.get("start", -1) <= cspan.get("start", -2) \
                    and cspan.get("end", -2) <= nsp.get("end", -1) \
                    and isinstance(cspan.get("start"), int) \
                    and isinstance(cspan.get("end"), int):
                covering = n
                break
        if covering is not None:
            break
    # 2. Sentence-level speaker evidence (own evidence_text, no context).
    from . import b3a_attribution as A3
    sent = A3.classify_attribution(ev_text)
    if sent["attribution"] in _OVERRIDE:
        status = _OVERRIDE[sent["attribution"]]
        rule = sent["rule_id"]
    elif sent["rule_id"] == "R-PROSECUTOR":
        status = PROSECUTOR_OR_INDICTMENT_ATTRIBUTION
        rule = sent["rule_id"]
    elif covering is not None:
        status = COURT_VOICE_EXPLICIT_CITATION
        rule = "bridge-cover"
    else:
        status = UNRESOLVED_ATTRIBUTION
        rule = "no-covering-node"
    eid = citation.get("evidence_id")
    return {
        "citation_id": citation.get("citation_id", ""),
        "judgement_id": citation.get("judgement_id", ""),
        "chunk_id": citation.get("chunk_id", ""),
        "source_span": dict(cspan) if isinstance(cspan, dict) else {},
        "raw_text": citation.get("raw_text", ""),
        "normalized_law": norm.get("law_name", ""),
        "normalized_article": norm.get("article", ""),
        "corpus_resolution_status": citation.get("resolution_status", ""),
        "statute_evidence_id": eid,
        "attribution": status,
        "attribution_rule_id": rule,
        "attribution_source_span": dict(
            covering["source_span"]) if covering is not None else {},
        "covering_node_id": covering["evidence_id"] if covering is not None else None,
        "temporal_status": temporal_map.get(eid) if eid else None,
        "downstream_eligibility": {
            "court_used": status == COURT_VOICE_EXPLICIT_CITATION,
            "display_as": _DISPLAY[status],
        },
        "provenance": {
            "covering_node_id": covering["evidence_id"] if covering is not None else None,
            "sentence_rule": sent["rule_id"],
        },
    }


def link_all(citations: list[dict], nodes: list[dict],
             attribution_map: dict | None,
             temporal_map: dict | None = None) -> list[dict]:
    """Relations for every citation (resolved and unresolved alike)."""
    return [link_citation(c, nodes, attribution_map, temporal_map)
            for c in citations]


def court_used_citation_ids(relations: list[dict]) -> set[str]:
    """Citation IDs whose relation supports material court claims."""
    return {r["citation_id"] for r in relations
            if r.get("attribution") == COURT_VOICE_EXPLICIT_CITATION}
