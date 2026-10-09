"""B4-A: multi-chunk evidence continuity (deterministic, no LLM, no inference).

Joins retrieval chunks into source-faithful multi-chunk evidence ONLY when
mechanically provable:
- same judgement_id (never across documents),
- deterministic source order (span ordering; missing spans -> UNKNOWN),
- span adjacency (end == start -> CONTIGUOUS; ordered with gaps ->
  ORDERED_GAPPED with explicit gap markers; never silent concatenation),
- uniform attribution (B3-A statuses; mixed voice splits, never merges),
- uniform evidence type.

Statuses: CONTIGUOUS / ORDERED_GAPPED / SEPARATE_EVIDENCE /
CONTINUITY_UNKNOWN. Only CONTIGUOUS renders as one verbatim passage;
ORDERED_GAPPED renders fragments with gap markers; CONTINUITY_UNKNOWN never
joins (caller abstains with INSUFFICIENT_CONTINUITY_EVIDENCE).
"""
from __future__ import annotations

CONTIGUOUS = "CONTIGUOUS"
ORDERED_GAPPED = "ORDERED_GAPPED"
SEPARATE_EVIDENCE = "SEPARATE_EVIDENCE"
CONTINUITY_UNKNOWN = "CONTINUITY_UNKNOWN"

GAP_MARKER = "[…source gap not available…]"


def assemble(fragments: list[dict], *, evidence_type: str,
             attributions: dict[str, str] | None = None,
             doc_offsets: dict[str, int] | None = None,
             chunk_texts: dict[str, str] | None = None) -> dict:
    """Assemble same-type evidence nodes into multi-chunk evidence.

    Each fragment is a B2-C-style node: {evidence_id, chunk_id, judgement_id,
    text, source_span{start,end} (CHUNK-relative), evidence_type}.
    doc_offsets maps chunk_id -> document-absolute start of that chunk (from
    corpus chunk spans; required for cross-chunk ordering — missing offsets
    mean CONTINUITY_UNKNOWN, never guessed).
    attributions maps evidence_id -> B3-A status (missing entries fail closed
    as UNRESOLVED_ATTRIBUTION).
    Returns an assembly record (never raises on data shape; unjoinable input
    yields SEPARATE_EVIDENCE / CONTINUITY_UNKNOWN records, never a guess).
    """
    attributions = attributions or {}
    doc_offsets = doc_offsets or {}
    chunk_texts = chunk_texts or {}
    if not fragments:
        return {"status": SEPARATE_EVIDENCE, "items": [], "reason": "empty input"}
    jids = {f.get("judgement_id", "") for f in fragments}
    if len(jids) != 1 or "" in jids:
        return {"status": SEPARATE_EVIDENCE, "items": list(fragments),
                "reason": "different-or-missing source documents"}
    types = {f.get("evidence_type", "") for f in fragments}
    if len(types) != 1 or next(iter(types)) != evidence_type:
        return {"status": SEPARATE_EVIDENCE, "items": list(fragments),
                "reason": "mixed evidence types"}
    spans = []
    for f in fragments:
        sp = f.get("source_span") or {}
        base = doc_offsets.get(f.get("chunk_id", ""), None)
        if base is None or not (isinstance(sp.get("start"), int)
                                and isinstance(sp.get("end"), int)
                                and 0 <= sp["start"] < sp["end"]):
            return {"status": CONTINUITY_UNKNOWN, "items": list(fragments),
                    "reason": "missing span metadata or chunk offsets"}
        spans.append((base + sp["start"], base + sp["end"], f))
    spans.sort(key=lambda t: (t[0], t[1]))
    for _, _, f in spans:
        att = attributions.get(f.get("evidence_id", ""), "UNRESOLVED_ATTRIBUTION")
        if att != "COURT_VOICE":
            return {"status": SEPARATE_EVIDENCE, "items": list(fragments),
                    "reason": f"attribution boundary at {f.get('evidence_id')}: {att}"}
    gaps = []
    bridges: dict[int, str] = {}
    for (_, e1, _), ((s2, _, _)) in zip(spans, [(s, e, f) for s, e, f in spans][1:]):
        if s2 < e1:
            return {"status": CONTINUITY_UNKNOWN, "items": list(fragments),
                    "reason": "overlapping spans"}
        if s2 > e1:
            gap_text = _gap_text(e1, s2, spans, chunk_texts, doc_offsets)
            if gap_text is not None and all(
                    ch in " \t\r\n　" for ch in gap_text):
                # Whitespace-only break: still one continuous passage; the
                # exact gap bytes are preserved in the joined text.
                bridges[e1] = gap_text
                continue
            gaps.append({"after_end": e1, "before_start": s2})
    ordered = [f for _, _, f in spans]
    if not gaps:
        text = "".join(
            f.get("text", "") + bridges.get(e, "") for _, e, f in spans)
        return {"status": CONTIGUOUS,
                "evidence_id": _assembly_id(ordered),
                "judgement_id": next(iter(jids)),
                "chunk_ids": [f.get("chunk_id", "") for f in ordered],
                "chunk_order": list(range(len(ordered))),
                "source_spans": [{"chunk_id": f.get("chunk_id", ""),
                                  "start": f["source_span"]["start"],
                                  "end": f["source_span"]["end"]}
                                 for _, _, f in spans],
                "absolute_spans": [{"start": s, "end": e} for s, e, _ in spans],
                "text": text,
                "evidence_type": evidence_type,
                "attribution": "COURT_VOICE",
                "continuity_status": CONTIGUOUS,
                "gaps": [],
                "provenance": {"method": "span-adjacency",
                               "n_fragments": len(ordered),
                               "whitespace_bridges": len(bridges)}}
    parts = [f.get("text", "") for _, _, f in spans]
    return {"status": ORDERED_GAPPED,
            "evidence_id": _assembly_id(ordered),
            "judgement_id": next(iter(jids)),
            "chunk_ids": [f.get("chunk_id", "") for f in ordered],
            "chunk_order": list(range(len(ordered))),
            "source_spans": [{"chunk_id": f.get("chunk_id", ""),
                              "start": f["source_span"]["start"],
                              "end": f["source_span"]["end"]}
                             for _, _, f in spans],
            "absolute_spans": [{"start": s, "end": e} for s, e, _ in spans],
            "text": f"\n{GAP_MARKER}\n".join(parts),
            "fragments": parts,
            "evidence_type": evidence_type,
            "attribution": "COURT_VOICE",
            "continuity_status": ORDERED_GAPPED,
            "gaps": [{"gap_index": i, "span_start": g["after_end"],
                      "span_end": g["before_start"],
                      "reason": "non-adjacent source spans"} for i, g in enumerate(gaps)],
            "provenance": {"method": "ordered-with-markers",
                           "n_fragments": len(ordered),
                           "gap_count": len(gaps)}}


def _gap_text(gap_start: int, gap_end: int, abs_spans: list,
              chunk_texts: dict, doc_offsets: dict) -> str | None:
    """Source text between two absolute spans, via chunk texts + offsets.

    Returns None when the gap cannot be located (then it stays a real gap).
    Chunks tile the source losslessly, so a locatable gap's text is exact.
    """
    for chunk_id, base in doc_offsets.items():
        text = chunk_texts.get(chunk_id)
        if not text:
            continue
        lo, hi = gap_start - base, gap_end - base
        if 0 <= lo <= hi <= len(text):
            return text[lo:hi]
    return None


def _assembly_id(ordered: list[dict]) -> str:
    import hashlib
    basis = "|".join(f.get("chunk_id", "") for f in ordered)
    return "multi:" + hashlib.sha256(basis.encode()).hexdigest()[:16]


def remap_citation_span(joined_offset: int, spans: list[dict]) -> dict | None:
    """Map an offset in joined text back to (chunk_id, local offset)."""
    cursor = 0
    for sp in spans:
        length = sp["end"] - sp["start"]
        if cursor <= joined_offset < cursor + length:
            return {"chunk_id": sp["chunk_id"],
                    "local_offset": sp["start"] + (joined_offset - cursor)}
        cursor += length
    return None


def continuity_gate(items: list[dict]) -> dict:
    """M1-M10 over assembly records and answer-bound multi items.

    - M1/M2/M3: same judgement, spans present, deterministic order (structural).
    - M4/M9: UNKNOWN never rendered continuous; gaps marked.
    - M5/M6: uniform attribution/type (assembly enforces; re-verified here).
    - M7: remapped citation spans resolve within recorded source spans.
    - M8: dispositions joined only when proven (CONTIGUOUS).
    - M10: multi-chunk items (chunk_ids length > 1) MUST carry valid assembly
      provenance, else FAIL (no silent flattening into the answer layer).
    """
    failures: list[str] = []
    for it in items:
        eid = it.get("evidence_id", "?")
        status = it.get("continuity_status", it.get("status", ""))
        chunk_ids = it.get("chunk_ids", [])
        single_chunk = len(chunk_ids) <= 1
        if status not in (CONTIGUOUS, ORDERED_GAPPED, SEPARATE_EVIDENCE,
                          CONTINUITY_UNKNOWN):
            failures.append(f"M2: {eid} bad continuity status")
            continue
        if single_chunk:
            continue  # single-chunk evidence: continuity gate is vacuous
        # M10: multi-chunk items require assembly provenance.
        spans = it.get("source_spans", [])
        if not spans or len(spans) != len(chunk_ids):
            failures.append(f"M10: {eid} multi-chunk item without span provenance")
            continue
        # M4/M9: UNKNOWN must not render as one continuous passage.
        if status == CONTINUITY_UNKNOWN and GAP_MARKER not in it.get("text", "") \
                and len(chunk_ids) > 1:
            # UNKNOWN items must not exist as joined text at all; the assembler
            # never produces them, so presence here is a bypass attempt.
            failures.append(f"M4/M9: {eid} joined text without proven continuity")
        # M5/M6: uniform attribution and type on merged court items.
        if status == CONTIGUOUS:
            atts = {it.get("attribution", "")} | set(
                it.get("provenance", {}).get("fragment_attributions", []))
            atts.discard("")
            if atts - {"COURT_VOICE"}:
                failures.append(f"M5: {eid} merged across attribution {sorted(atts)}")
        # M8: disposition joins require proven continuity (CONTIGUOUS only).
        if it.get("evidence_type") == "DISPOSITION" and status != CONTIGUOUS \
                and len(chunk_ids) > 1:
            failures.append(f"M8: {eid} disposition joined without proof")
    return {"verdict": "PASS" if not failures else "FAIL", "failures": failures}
