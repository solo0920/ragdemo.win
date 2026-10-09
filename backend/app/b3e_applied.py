"""B3-E: court-applied statute evidence (deterministic, no LLM, no similarity).

Links an exact B2-B citation to the court-voice evidence applying it.
COURT_EXPLICIT_APPLICATION holds iff ALL of the following are proven:

  A1. the citation is corpus-resolved AND its B3-D relation is
      COURT_VOICE_EXPLICIT_CITATION (speaker + coverage already proven);
  A2. the covering node is a B2-C HOLDING selected by an explicit conclusory
      pattern (provenance method "conclusory_pattern") — the sentence both
      cites the statute and states the court's conclusion;
  A3. that node independently classifies COURT_VOICE (attribution map;
      missing entries fail closed);
  A4. single-text locality: the covering linkage comes from one shared
      source text (inherited from the B3-D relation, never re-derived);
      multi-chunk assembled nodes are excluded (limitation, not merged).

Anything else yields APPLICATION_UNRESOLVED with an explicit reason — never
a forced applied/not-applied binary. In particular a court-voice passage
that merely names a statute, a disposition containing a citation, and any
cross-chunk citation/conclusion pairing stay UNRESOLVED in v1.

DECISIVE_STATUTE is never emitted: application is not decisiveness.
"""
from __future__ import annotations

COURT_EXPLICIT_APPLICATION = "COURT_EXPLICIT_APPLICATION"
APPLICATION_UNRESOLVED = "APPLICATION_UNRESOLVED"

RULE_ID = "holding-containment-v1"


def link_application(citation: dict, nodes_by_id: dict,
                     attribution_map: dict | None,
                     relations_by_citation_id: dict) -> dict:
    """One citation -> application relation. Never raises on data shape."""
    base = {
        "relation_id": "",
        "citation_id": citation.get("citation_id", ""),
        "judgement_id": citation.get("judgement_id", ""),
        "relation_type": APPLICATION_UNRESOLVED,
        "citation_evidence": {
            "chunk_id": citation.get("chunk_id", ""),
            "source_span": dict(citation.get("source_span", {}) or {}),
            "raw_text": citation.get("raw_text", ""),
        },
        "application_evidence": None,
        "rule_id": RULE_ID,
        "verification_status": "COMPUTED",
    }
    rel = relations_by_citation_id.get(citation.get("citation_id", ""))
    if rel is None or rel.get("attribution") != "COURT_VOICE_EXPLICIT_CITATION":
        return {**base, "reason": "not-court-used"}
    node = nodes_by_id.get(rel.get("covering_node_id", "") or "")
    if node is None:
        return {**base, "reason": "no-covering-node"}
    if node.get("evidence_type") != "HOLDING":
        return {**base, "reason": "covering-not-holding"}
    if (node.get("provenance", {}) or {}).get("method") != "conclusory_pattern":
        return {**base, "reason": "holding-not-conclusory"}
    if (node.get("provenance", {}) or {}).get("multi_chunk"):
        return {**base, "reason": "multi-chunk-application-unsupported"}
    att = (attribution_map or {}).get(node.get("evidence_id", ""),
                                      "UNRESOLVED_ATTRIBUTION")
    if att != "COURT_VOICE":
        return {**base, "reason": "node-attribution-not-court"}
    import hashlib
    basis = f"{citation.get('citation_id', '')}|{node.get('evidence_id', '')}"
    return {
        **base,
        "relation_id": "applied:" + hashlib.sha256(basis.encode()).hexdigest()[:16],
        "relation_type": COURT_EXPLICIT_APPLICATION,
        "application_evidence": {
            "evidence_id": node.get("evidence_id", ""),
            "evidence_type": "HOLDING",
            "attribution": "COURT_VOICE",
            "chunk_ids": [node.get("chunk_id", "")],
            "source_spans": [dict(node.get("source_span", {}) or {})],
            "verbatim_text": node.get("text", ""),
            "conclusory_marker": (node.get("provenance", {}) or {}).get("marker", ""),
        },
        "reason": "citation-inside-conclusory-holding",
    }


def link_all(citations: list[dict], nodes: list[dict],
             attribution_map: dict | None,
             relations: list[dict]) -> list[dict]:
    """Application relations for every citation (applied + unresolved)."""
    by_id = {n.get("evidence_id", ""): n for n in nodes}
    by_cit = {r.get("citation_id", ""): r for r in relations}
    return [link_application(c, by_id, attribution_map, by_cit)
            for c in citations]


def applied_citation_ids(relations: list[dict]) -> set[str]:
    """Citation IDs with a verified application relation."""
    return {r["citation_id"] for r in relations
            if r.get("relation_type") == COURT_EXPLICIT_APPLICATION}


# ═══════════════════════════════════════════════════════════════════════════
# S2a P0: cross-chunk evidence-link validator (infrastructure only).
#
# Validates that a citation and a set of same-judgement HOLDING fragments can
# be linked through B4-A continuity — WITHOUT promoting anything to APPLIED.
# `link_application` above is untouched: cross-chunk inputs still yield
# APPLICATION_UNRESOLVED there (no promotion path exists in this module).
#
# Trust rule: continuity is always RECOMPUTED by calling `assemble()` on the
# supplied fragments. A caller-supplied status string is never pass evidence
# (it may be attached as `claimed` for audit, and is then ignored).
# ═══════════════════════════════════════════════════════════════════════════

LINK_OK = "LINK_OK"
LINK_FAIL = "LINK_FAIL"


def multi_assembly_id(citation_id: str, judgement_id: str,
                      chunk_ids: list, source_spans: list) -> str:
    """Deterministic content address for a multi-chunk application proof.

    Binds citation + judgement + every fragment (chunk id + span). Any
    substitution changes the ID. A11 recomputes this exact function.
    """
    import hashlib
    parts = [str(citation_id or ""), str(judgement_id or "")]
    for cid, sp in zip(list(chunk_ids or []), list(source_spans or [])):
        sp = sp or {}
        parts.append(f"{cid}|{sp.get('start')}|{sp.get('end')}")
    return "applied-multi:" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _wsq(s) -> str:
    """Whitespace-collapsed form (mirrors B2-B matching on collapsed text)."""
    return "".join(str(s or "").split())


def validate_link(*, citation: dict, citation_text: str,
                  fragments: list[dict], attribution_map: dict | None,
                  doc_offsets: dict, chunk_texts: dict,
                  claimed: dict | None = None) -> dict:
    """Validate a cross-chunk citation->conclusory-holding link. Never raises.

    `claimed` (e.g. a caller-supplied continuity_status) is recorded verbatim
    for audit and NEVER influences the verdict. The verdict comes only from
    recomputation below. Returns a link record with LINK_OK/LINK_FAIL.
    """
    try:
        return _validate_link(citation=citation, citation_text=citation_text,
                              fragments=fragments, attribution_map=attribution_map,
                              doc_offsets=doc_offsets, chunk_texts=chunk_texts,
                              claimed=claimed)
    except Exception as e:  # noqa: BLE001 - validator itself must fail closed
        return {"verdict": LINK_FAIL, "reason": "internal-error",
                "detail": type(e).__name__, "claimed": claimed or {}}


def _validate_link(*, citation, citation_text, fragments, attribution_map,
                   doc_offsets, chunk_texts, claimed) -> dict:
    base = {"citation_id": (citation or {}).get("citation_id", ""),
            "judgement_id": (citation or {}).get("judgement_id", ""),
            "fragment_refs": [], "assembly_id": "", "continuity_status": "",
            "covering_rule_ids": [], "claimed": claimed or {}}
    # 1. citation shape.
    cspan = (citation or {}).get("source_span") or {}
    if not ((citation or {}).get("citation_id")
            and (citation or {}).get("judgement_id")
            and isinstance(cspan.get("start"), int)
            and isinstance(cspan.get("end"), int)
            and cspan["start"] < cspan["end"]
            and isinstance((citation or {}).get("evidence_text", ""), str)
            and (citation or {}).get("evidence_text")):
        return {**base, "verdict": LINK_FAIL, "reason": "missing-citation-fields"}
    jid = citation["judgement_id"]
    # 2. fragments present, single judgement matching the citation.
    frags = list(fragments or [])
    if not frags:
        return {**base, "verdict": LINK_FAIL, "reason": "no-fragments"}
    fjids = {f.get("judgement_id", "") for f in frags}
    if len(fjids) != 1 or next(iter(fjids)) != jid:
        return {**base, "verdict": LINK_FAIL, "reason": "cross-judgement-fragments"}
    amap = attribution_map or {}
    # 3-4. per-fragment: HOLDING + conclusory method + COURT_VOICE +
    # verbatim slice of its chunk text (G4-style; missing text fails closed).
    for f in frags:
        if f.get("evidence_type") != "HOLDING":
            return {**base, "verdict": LINK_FAIL,
                    "reason": "fragment-not-holding"}
        if (f.get("provenance", {}) or {}).get("method") != "conclusory_pattern":
            return {**base, "verdict": LINK_FAIL,
                    "reason": "fragment-not-conclusory"}
        if amap.get(f.get("evidence_id", ""), "UNRESOLVED_ATTRIBUTION") != "COURT_VOICE":
            return {**base, "verdict": LINK_FAIL,
                    "reason": "fragment-attribution-not-court"}
        fspan = f.get("source_span") or {}
        ctext = (chunk_texts or {}).get(f.get("chunk_id", ""))
        if (not isinstance(fspan.get("start"), int)
                or not isinstance(fspan.get("end"), int)
                or not isinstance(ctext, str)
                or ctext[fspan["start"]:fspan["end"]] != (f.get("text") or "")):
            return {**base, "verdict": LINK_FAIL,
                    "reason": "fragment-text-not-verbatim"}
    # 5. citation surface re-verified against its source text (collapsed,
    # matching B2-B's collapsed matching), then located in exactly one
    # fragment by span coverage + sentence containment.
    cs, ce = cspan["start"], cspan["end"]
    if _wsq((citation_text or "")[cs:ce]) != _wsq(citation.get("raw_text", "")):
        return {**base, "verdict": LINK_FAIL, "reason": "citation-surface-mismatch"}
    holders = [f for f in frags
               if (f.get("source_span") or {}).get("start", 0) <= cs
               and ce <= (f.get("source_span") or {}).get("end", -1)
               and citation["evidence_text"] in (f.get("text") or "")]
    if len(holders) != 1:
        return {**base, "verdict": LINK_FAIL,
                "reason": "citation-not-in-exactly-one-fragment"}
    # Coordinate-space binding: the covering fragment's text must be the
    # verbatim slice of the citation's own source text at the fragment span.
    # Otherwise a duplicate sentence in another chunk (same relative
    # offsets, different chunk) could numerically "cover" the citation span
    # while living in a different coordinate space.
    hspan = holders[0].get("source_span") or {}
    if (citation_text or "")[hspan.get("start", 0):hspan.get("end", -1)] != (
            holders[0].get("text") or ""):
        return {**base, "verdict": LINK_FAIL,
                "reason": "citation-space-mismatch"}
    # 6. continuity RECOMPUTED (never trusted): CONTIGUOUS only.
    from . import b4a_continuity as cont
    rec = cont.assemble(
        [{"evidence_id": f.get("evidence_id", ""), "evidence_type": "HOLDING",
          "judgement_id": f.get("judgement_id", ""),
          "chunk_id": f.get("chunk_id", ""), "text": f.get("text", ""),
          "source_span": dict(f.get("source_span") or {})} for f in frags],
        evidence_type="HOLDING",
        attributions={f.get("evidence_id", ""): amap.get(f.get("evidence_id", ""),
                                                          "UNRESOLVED_ATTRIBUTION")
                      for f in frags},
        doc_offsets=dict(doc_offsets or {}),
        chunk_texts=dict(chunk_texts or {}))
    if rec.get("status") != cont.CONTIGUOUS:
        return {**base, "verdict": LINK_FAIL,
                "reason": f"continuity-not-contiguous:{rec.get('status')}"}
    chunk_ids = [f.get("chunk_id", "") for f in frags]
    spans = [dict((f.get("source_span") or {})) for f in frags]
    rules = [f"fragment-bridge:{holders[0].get('evidence_id', '')}",
             f"assembly-contiguous:{rec.get('evidence_id', '')}"]
    return {**base,
            "fragment_refs": [{"chunk_id": f.get("chunk_id", ""),
                               "source_span": dict(f.get("source_span") or {}),
                               "node_id": f.get("evidence_id", "")} for f in frags],
            "assembly_id": multi_assembly_id(citation.get("citation_id", ""),
                                             jid, chunk_ids, spans),
            "continuity_status": rec.get("status", ""),
            "covering_rule_ids": rules,
            "verdict": LINK_OK, "reason": "citation-in-contiguous-holding"}
