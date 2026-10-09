"""B2-D: evidence-grounded judicial answer (extractive, deterministic, no LLM).

Consumes the authorized chain only: B2-R1-selected judgement -> B2-C
holding/disposition/reasoning graph -> B2-B citations -> exact statute
evidence. composing is fixed-section assembly of verbatim quotes; the only
freely-placed strings are allowlisted headers and limitation statements.
A deterministic answer gate (A1-A10) rejects anything ungrounded; failures
surface as abstention, never as best-effort answers.

Response contract extends B1's (status/answer/judgements/statutes/evidence/
claims/abstention) with a judgement block and a citation display list. Court
is omitted per spec 004 FR-016 (no source field, never inferred); the
judgement number is the opaque JID (FR-028).
"""
from __future__ import annotations

from . import b2c_evidence as b2c
from . import b2b_cite as b2b
from . import b2f_confidence as conf
from . import b3a_attribution as attr
from . import b3c_contra as contra
from . import b4a_continuity as cont
from .b3b_temporal import (
    CURRENT_ONLY,
    HISTORICAL_VERIFIED,
    TEMPORALLY_UNKNOWN,
    TEMPORAL_LIMITATIONS,
)
from . import retrieve
from .b1_serve import (
    JudgmentAnswerError,
    StatuteCorpus,
    answer_judgments,
)
from .b2r1_rank import aggregate_by_judgement

ANSWERED = "ANSWERED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
ABSTAINED = "ABSTAINED"

ABSTAIN_REASONS = {
    "NO_JUDGMENT": "找不到相關判決；不生成答案",
    "INSUFFICIENT_JUDGMENT_EVIDENCE": "判決證據不足（缺少可引用的主文或結論）；不生成答案",
    "INSUFFICIENT_CONTINUITY_EVIDENCE": "跨 chunk 證據連續性無法確認；不以片段拼湊作答",
    "NO_REASONING_EVIDENCE": "缺少法院理由證據；不生成答案",
    "STATUTE_EVIDENCE_UNRESOLVED": "必要的法規證據無法驗證；不生成答案",
    "GROUNDING_FAILURE": "grounding gate 未通過；不輸出答案",
    "CONTRADICTORY_EVIDENCE": "證據內部矛盾，無法擇一回答；不生成答案",
    "LOW_RETRIEVAL_CONFIDENCE": "檢索信心不足，選出的判決不可靠；不生成答案",
    "RETRIEVAL_IDENTITY_MISMATCH": "檢索首位判決與問題指明的案件不符；不生成答案",
}

# Fixed, content-free section headers (never legal claims).
H_JUDGMENT = "【本案相關判決】"
H_DISPOSITION = "【法院判決結果】"
H_HOLDING = "【法院認定】"
H_REASONING = "【法院理由】"
H_STATUTES = "【本判決明確引用的法規】"
H_LIMIT = "【說明與限制】"
HEADERS = (H_JUDGMENT, H_DISPOSITION, H_HOLDING, H_REASONING, H_STATUTES, H_LIMIT)

# Fixed limitation statements (the only non-quote, non-header sentences allowed).
LIMIT_SCOPE = "以下回答僅依據所列判決證據整理，不構成法律意見。"
LIMIT_SINGLE_CASE = "本判決的結論僅適用於該判決處理的事實情形，不足以證明所有同類案件均會如此處理。"
LIMIT_CITATION = "此處所列法規為該判決明確引用的條文，不代表法院以之作為判決的決定性依據。"
LIMITATIONS = (LIMIT_SCOPE, LIMIT_SINGLE_CASE, LIMIT_CITATION)

# Decisive phrasing that must never appear outside verbatim quotes.
_DECIDED_PHRASES = ("依民法", "依刑法", "以該條", "根據本法", "本院認定被告應")


def _judgment_header_lines(judgement_id: str, jdate: str) -> list[str]:
    line = f"案號：{judgement_id}" + (f"｜日期：{jdate}" if jdate else "")
    return [H_JUDGMENT, line]


def _display_citation(statute_evidence: dict) -> str:
    prov = statute_evidence.get("provenance", {})
    return (f"{statute_evidence.get('law_name', '')}"
            f"{statute_evidence.get('article', '')}"
            f"（{statute_evidence.get('document_id', '')}"
            f"#{prov.get('article_seq', '')}）")


def build_answer(question: str, *, judgement_id: str, graph: dict,
                 citations: list[dict], statute_evidences: list[dict],
                 require_statutes: bool = False, jdate: str = "",
                 attribution: dict | None = None,
                 temporal: dict | None = None) -> dict:
    """Assemble the answer from authorized evidence only. No retrieval, no LLM.

    attribution (B3-A): evidence_id -> attribution status. When provided, only
    COURT_VOICE nodes may support material court claims; other nodes' text
    leaves the answer (their B2-B citations still resolve — citation behavior
    is untouched). Missing entries fail closed as UNRESOLVED. None preserves
    the pre-B3-A composer contract.
    """
    nodes = [n for n in graph.get("nodes", []) if n.get("judgement_id") == judgement_id]
    if attribution is not None:
        nodes = [n for n in nodes
                 if attribution.get(n["evidence_id"],
                                    attr.UNRESOLVED_ATTRIBUTION) == attr.COURT_VOICE]
    # B3-D citation attribution: every citation is bound to a speaker
    # relation (transparency record, always computed). Filtering applies
    # ONLY under enforcement (attribution map provided), preserving the
    # legacy direct-composition contract otherwise: the covering rule
    # replicates the historical span bridge exactly, and the sentence-level
    # speaker override is the sole deliberate delta (paid for by reviewed
    # fixtures + unit tests).
    from . import b3d_attribution as b3d
    relations = b3d.link_all(citations, nodes, attribution, temporal)
    attribution_enforced = attribution is not None
    if attribution_enforced:
        _court_used = b3d.court_used_citation_ids(relations)
        citations = [c for c in citations
                     if c.get("judgement_id") == judgement_id
                     and c.get("citation_id") in _court_used]
    # B3-E court-applied statute relations over the answer's citations.
    # No map means no speaker proof: application stays UNRESOLVED there.
    from . import b3e_applied as b3e
    applied_relations = b3e.link_all(citations, nodes, attribution, relations)
    keep_ids = {c.get("evidence_id") for c in citations
                if c.get("resolution_status") == "RESOLVED"}
    statute_evidences = [e for e in statute_evidences
                         if e["evidence_id"] in keep_ids]
    by_type: dict[str, list[dict]] = {}
    for n in nodes:
        by_type.setdefault(n["evidence_type"], []).append(n)
    for k in by_type:
        by_type[k].sort(key=lambda n: (n["source_span"]["start"], n["source_span"]["end"]))
    disps = by_type.get("DISPOSITION", [])
    holds = by_type.get("HOLDING", [])
    reas = by_type.get("REASONING", [])

    if not nodes:
        return _abstain("NO_JUDGMENT")
    if not disps and not holds:
        return _abstain("INSUFFICIENT_JUDGMENT_EVIDENCE")
    if not reas:
        return _abstain("NO_REASONING_EVIDENCE")
    # Minimal contradiction tripwire (no engine): two different dispositions.
    if len({d["text"] for d in disps}) > 1:
        return _abstain("CONTRADICTORY_EVIDENCE")

    resolved = [c for c in citations
                if c.get("resolution_status") == "RESOLVED"
                and c.get("judgement_id") == judgement_id]
    linked_evs = []
    for c in resolved:
        ev = next((e for e in statute_evidences
                   if e["evidence_id"] == c.get("evidence_id")), None)
        if ev is not None and ev not in linked_evs:
            linked_evs.append(ev)
    if require_statutes and not linked_evs:
        return _abstain("STATUTE_EVIDENCE_UNRESOLVED")

    # B3-B temporal handling (status-derived, fixed wording). Only when the
    # caller supplies a temporal map; default None preserves legacy output.
    # TEMPORALLY_INVALID versions never enter as applicable statute text.
    from .b3b_temporal import TEMPORALLY_INVALID
    temporal = temporal or {}
    invalid_ids = {e["evidence_id"] for e in linked_evs
                   if temporal.get(e["evidence_id"]) == TEMPORALLY_INVALID}
    if invalid_ids:
        linked_evs = [e for e in linked_evs if e["evidence_id"] not in invalid_ids]
        tlims_invalid_note = ["部分引用的法規因版本時間不適用，未列出其條文內容。"]
    else:
        tlims_invalid_note = []

    jdate = jdate or _judgement_date(nodes)
    parts = _judgment_header_lines(judgement_id, jdate)
    claims: list[dict] = []
    seq = [0]

    def claim(kind: str, text: str, eids: list[str]) -> None:
        seq[0] += 1
        claims.append({"claim_id": f"C{seq[0]}", "type": kind, "text": text,
                       "evidence_ids": eids})

    for ev in disps:
        parts += [H_DISPOSITION, ev["text"]]
        claim("DISPOSITION", ev["text"], [ev["evidence_id"]])
    for ev in holds:
        parts += [H_HOLDING, ev["text"]]
        claim("HOLDING", ev["text"], [ev["evidence_id"]])
    for ev in reas:
        parts += [H_REASONING, ev["text"]]
        claim("REASONING", ev["text"], [ev["evidence_id"]])
    for ev in linked_evs:
        disp = _display_citation(ev)
        parts += [H_STATUTES, f"{disp}\n{ev['text']}"]
        claim("STATUTE", ev["text"], [ev["evidence_id"]])
    parts += [H_LIMIT, LIMIT_SCOPE, LIMIT_SINGLE_CASE]
    if linked_evs:
        parts.append(LIMIT_CITATION)
    lims = [LIMIT_SCOPE, LIMIT_SINGLE_CASE] + ([LIMIT_CITATION] if linked_evs else [])
    # Status-derived limitation wording (tlims_invalid_note set above when
    # invalid versions were excluded).
    from .b3b_temporal import (LIMIT_CURRENT, LIMIT_UNKNOWN, LIMIT_VERIFIED)
    tstates = {temporal.get(e["evidence_id"]) for e in linked_evs} - {None}
    tlims = list(tlims_invalid_note)
    if any(s == HISTORICAL_VERIFIED for s in tstates):
        tlims.append(LIMIT_VERIFIED)
    if any(s == CURRENT_ONLY for s in tstates):
        tlims.append(LIMIT_CURRENT)
    if any(s not in (HISTORICAL_VERIFIED, CURRENT_ONLY) for s in tstates):
        tlims.append(LIMIT_UNKNOWN)
    parts += tlims
    for lim in lims + tlims:
        claim("LIMITATION", lim, [])
    answer = "\n".join(parts)

    judgements = [{"judgement_id": judgement_id, "judgement_number": judgement_id,
                   "date": jdate}]
    response = {
        "status": ANSWERED,
        "question": question,
        "judgment": {"judgement_id": judgement_id,
                     "judgement_number": judgement_id,
                     "date": jdate},
        "answer": answer,
        "judgements": judgements,
        "statutes": [{"law_name": e.get("law_name"), "article": e.get("article"),
                      "paragraph": e.get("paragraph")} for e in linked_evs],
        "evidence": {"judgment": [n["evidence_id"] for n in nodes],
                     "statutes": [e["evidence_id"] for e in linked_evs]},
        "claims": claims,
        "citations": [{"evidence_id": e["evidence_id"], "display": _display_citation(e)}
                      for e in linked_evs],
        "statute_attributions": relations,
        "attribution_enforced": attribution_enforced,
        "applied_statutes": applied_relations,
        "abstention": None,
    }
    ok, failures = answer_gate(response, selected_judgement_id=judgement_id,
                               citations=citations, nodes=nodes)
    if not ok:
        return _abstain("GROUNDING_FAILURE", gate_failures=failures)
    return response


def _judgement_date(nodes: list[dict]) -> str:
    for n in nodes:
        jdate = n.get("provenance", {}).get("jdate", "")
        if jdate:
            return jdate
    return ""


def _synthesis_decomposes(claim: dict, evidence_texts: dict | None) -> bool:
    """True iff an ANSWER_SYNTHESIS claim's text is exactly the ordered join
    of its cited evidence texts (each removed once; separators must be
    whitespace-only). Missing map, empty text, or any mismatch -> False
    (fail closed; the text then trips A7 like any ungrounded span)."""
    if claim.get("type") != "ANSWER_SYNTHESIS":
        return False
    if not evidence_texts:
        return False
    rest = claim.get("text", "")
    if not rest:
        return False
    for eid in claim.get("evidence_ids", []):
        t = evidence_texts.get(eid)
        if not t or t not in rest:
            return False
        rest = rest.replace(t, "", 1)
    return not rest.strip()


def answer_gate(response: dict, *, selected_judgement_id: str,
                citations: list[dict],
                evidence_texts: dict | None = None,
                nodes: list[dict] | None = None) -> tuple[bool, list[str]]:
    """Deterministic answer gate A1-A10.

    evidence_texts maps evidence_id -> verbatim evidence text. It enables
    ANSWER_SYNTHESIS claims (multi-evidence joins): a synthesis claim passes
    only when its text decomposes exactly into its cited evidence texts.
    Without a map, synthesis text is ungrounded by construction (A7 fails),
    preserving the pre-B4-B contract for all existing callers.

    nodes enables the A11 pairing check (S4): each APPLIED relation's
    citation/node binding is re-verified (same judgement, node span covers
    the citation span, citation sentence inside node text). Without nodes
    only membership is checked, preserving the pre-S4 contract for all
    existing callers.
    """
    failures: list[str] = []
    if response.get("status") != ANSWERED:
        return False, ["not an ANSWERED response"]
    claims = response.get("claims", [])
    ev_lists = response.get("evidence", {})
    known = set(ev_lists.get("judgment", [])) | set(ev_lists.get("statutes", []))
    cited_by_id = {c.get("evidence_id"): c for c in citations
                   if c.get("resolution_status") == "RESOLVED"}
    quote_texts = [c["text"] for c in claims
                   if c["type"] in ("DISPOSITION", "HOLDING", "REASONING", "STATUTE")]
    # ANSWER_SYNTHESIS claims are grounded only by exact decomposition into
    # cited evidence texts (verified below); anything else fails explicitly.
    synth_texts: list[str] = []
    verified_synth: set[str] = set()
    for c in claims:
        if c.get("type") != "ANSWER_SYNTHESIS":
            continue
        if _synthesis_decomposes(c, evidence_texts):
            synth_texts.append(c["text"])
            verified_synth.add(c.get("claim_id") or "")
    for c in claims:
        if c.get("type") == "ANSWER_SYNTHESIS" and c.get("claim_id") not in verified_synth:
            failures.append(f"A7: synthesis claim not grounded: {c.get('claim_id')}")
    # A1/A2: every material claim has existing evidence.
    for claim in claims:
        if claim.get("type") == "LIMITATION":
            if claim.get("text") not in LIMITATIONS + TEMPORAL_LIMITATIONS:
                failures.append(f"A1: LIMITATION not allowlisted: {claim.get('claim_id')}")
            if claim.get("evidence_ids"):
                failures.append(f"A1: LIMITATION must not cite: {claim.get('claim_id')}")
            continue
        ids = claim.get("evidence_ids") or []
        if not ids:
            failures.append(f"A1: claim without evidence: {claim.get('claim_id')}")
        for eid in ids:
            if eid not in known:
                failures.append(f"A2: unknown evidence_id {eid}")
    # A3/A5: judgement evidence belongs to the selected judgement.
    # Evidence IDs embed the judgement: {type}:{jid}#... (statute ids are
    # pcode-based and constrained by A4 instead).
    for eid in ev_lists.get("judgment", []):
        doc = _doc_of_evidence(eid)
        if doc is not None and doc != selected_judgement_id:
            failures.append(f"A3/A5: evidence from unrelated judgement: {eid}")
    if response.get("judgment", {}).get("judgement_id") != selected_judgement_id:
        failures.append("A3: response judgement is not the selected judgement")
    # A4: every statute evidence is B2-B-resolved and citation-linked.
    for eid in ev_lists.get("statutes", []):
        if eid not in cited_by_id:
            failures.append(f"A4: statute without RESOLVED citation: {eid}")
    # A4b: under attribution enforcement, every statute evidence must carry
    # a court-used relation (defense in depth; skipped for legacy responses
    # without the enforcement flag).
    if response.get("attribution_enforced"):
        _rel_by_eid: dict[str, dict] = {}
        for _r in response.get("statute_attributions", []):
            if _r.get("statute_evidence_id"):
                _rel_by_eid[_r["statute_evidence_id"]] = _r
        for eid in ev_lists.get("statutes", []):
            _rel = _rel_by_eid.get(eid)
            if _rel is None or not _rel.get("downstream_eligibility", {}).get("court_used"):
                failures.append(f"A4: statute without court-used relation: {eid}")
    # A6/A7: claim texts are verbatim evidence (or allowlisted); the remainder
    # after removing every grounded span must be whitespace-only. Section
    # headers, the judgement header lines, citation displays, and limitation
    # statements are the complete allowlist — anything else is ungrounded.
    remainder = response.get("answer") or ""
    for t in quote_texts + synth_texts:
        if t:
            remainder = remainder.replace(t, "", 1)
    for disp in [c["display"] for c in response.get("citations", [])]:
        remainder = remainder.replace(disp, "", 1)
    for fixed in (list(HEADERS) + list(LIMITATIONS) + list(TEMPORAL_LIMITATIONS)
                  + _judgment_header_lines(
                      response.get("judgment", {}).get("judgement_id", ""),
                      response.get("judgment", {}).get("date", ""))):
        remainder = remainder.replace(fixed, "")
    if remainder.strip():
        failures.append(f"A7: ungrounded spans in answer: {remainder.strip()[:60]!r}")
    # NOTE: decisive-phrasing control lives in the remainder check above (the
    # remainder must be empty) plus a unit test pinning HEADERS/LIMITATIONS
    # against _DECIDED_PHRASES. No free-text path exists for such phrasing.
    # A8: structural non-contradiction (quotes cannot contradict themselves;
    # deeper semantic contradiction is B3 scope and abstained upstream).
    # A9: citation displays re-derived from evidence metadata.
    for c in response.get("citations", []):
        ev = next((e for e in _all_statute_evs(response, citations)
                   if e["evidence_id"] == c["evidence_id"]), None)
        if ev is None or _display_citation(ev) != c["display"]:
            failures.append(f"A9: display mismatch: {c.get('evidence_id')}")
    # A10: mandatory evidence present for ANSWERED.
    kinds = {c["type"] for c in claims}
    if not ({"DISPOSITION", "HOLDING"} & kinds):
        failures.append("A10: ANSWERED without disposition/holding claim")
    if "REASONING" not in kinds:
        failures.append("A10: ANSWERED without reasoning claim")
    # A11: applied-statute consistency (B3-E). Every APPLIED entry must
    # reference a kept citation + a listed statute + a listed judgement
    # node, and carry no decisive claim. Forged or dangling entries fail.
    #
    # S4 binding integrity: membership alone cannot catch two valid IDs
    # paired incorrectly. When nodes are provided, the pairing itself is
    # re-verified with the B3-D bridge semantics (same judgement, node span
    # covers the citation span, citation sentence inside node text).
    # chunk_id is deliberately NOT compared: B2-B (`{jid}#chunk{i}`) and
    # retrieval chunk IDs follow different conventions with no authoritative
    # equivalence. Without nodes only membership is checked (pre-S4 callers).
    cited_by_id = {c.get("citation_id", ""): c for c in citations}
    nodes_by_id = {n.get("evidence_id", ""): n for n in (nodes or [])}
    for _a in response.get("applied_statutes", []):
        _r = _a.get("relation_type", "")
        if _r not in ("COURT_EXPLICIT_APPLICATION", "APPLICATION_UNRESOLVED"):
            failures.append(f"A11: bad applied relation type: {_r}")
            continue
        if _r != "COURT_EXPLICIT_APPLICATION":
            continue
        _cids = {c.get("citation_id", "") for c in citations}
        if _a.get("citation_id", "") not in _cids:
            failures.append(f"A11: applied citation not in response: {_a.get('citation_id')}")
            continue
        _cit = cited_by_id.get(_a.get("citation_id", "")) or {}
        if (_a.get("judgement_id", "") != selected_judgement_id
                or _cit.get("judgement_id", "") != selected_judgement_id):
            failures.append(f"A11: applied judgement mismatch: {_a.get('citation_id')}")
            continue
        _app = _a.get("application_evidence") or {}
        if _app.get("evidence_id", "") not in known:
            failures.append(f"A11: applied evidence unknown: {_app.get('evidence_id')}")
            continue
        if _app.get("attribution", "") != "COURT_VOICE":
            failures.append("A11: applied evidence not court voice")
            continue
        # S2a P1: multi-chunk provenance. Single-chunk entries keep the
        # pairing check below unchanged. Multi entries (chunk_ids len > 1)
        # fail closed without nodes: pairing cannot be replayed from IDs
        # alone, and pre-S4 callers never emit multi entries, so failing
        # here changes nothing for them.
        _mchunks = _app.get("chunk_ids")
        _multi = isinstance(_mchunks, list) and len(_mchunks) > 1
        if _multi and nodes is None:
            failures.append(f"A11: applied multi-chunk needs nodes: {_a.get('citation_id')}")
            continue
        if _multi:
            from . import b3e_applied as b3e
            _mspans = _app.get("source_spans")
            if (not isinstance(_mspans, list) or len(_mspans) != len(_mchunks)
                    or any(not isinstance((s or {}).get("start"), int)
                           or not isinstance((s or {}).get("end"), int)
                           or (s or {}).get("start", 0) >= (s or {}).get("end", 0)
                           for s in _mspans)):
                failures.append(f"A11: applied provenance malformed: {_a.get('citation_id')}")
                continue
            if _app.get("continuity_status", "") != "CONTIGUOUS":
                failures.append(f"A11: applied continuity not contiguous: {_a.get('citation_id')}")
                continue
            if _app.get("assembly_id", "") != b3e.multi_assembly_id(
                    _a.get("citation_id", ""), _a.get("judgement_id", ""),
                    _mchunks, _mspans):
                failures.append(f"A11: applied assembly mismatch: {_a.get('citation_id')}")
                continue
            _fnids = _app.get("fragment_node_ids")
            if (not isinstance(_fnids, list) or len(_fnids) != len(_mchunks)
                    or any((nodes_by_id.get(i) or {}).get("judgement_id", "")
                           != selected_judgement_id for i in _fnids)):
                failures.append(f"A11: applied fragment nodes mismatch: {_a.get('citation_id')}")
                continue
            try:
                _cs, _ce = int((_cit.get("source_span") or {}).get("start")), \
                    int((_cit.get("source_span") or {}).get("end"))
            except (TypeError, ValueError):
                failures.append(f"A11: applied span not covered: {_a.get('citation_id')}")
                continue
            _sent = _cit.get("evidence_text", "")
            _paired = False
            for _fid, _sp in zip(_fnids, _mspans):
                _nd = nodes_by_id.get(_fid) or {}
                try:
                    _ok = int(_sp.get("start")) <= _cs and _ce <= int(_sp.get("end"))
                except (TypeError, ValueError):
                    _ok = False
                if _ok and isinstance(_sent, str) and _sent and _sent in (_nd.get("text") or ""):
                    _paired = True
                    break
            if not _paired:
                failures.append(f"A11: applied multi bridge failed: {_a.get('citation_id')}")
            continue
        if nodes is not None:
            _node = nodes_by_id.get(_app.get("evidence_id", ""))
            if _node is None:
                failures.append(f"A11: applied evidence has no node: {_app.get('evidence_id')}")
                continue
            if _node.get("judgement_id", "") != selected_judgement_id:
                failures.append(f"A11: applied node judgement mismatch: {_app.get('evidence_id')}")
                continue
            try:
                _ns, _ne = int((_node.get("source_span") or {}).get("start")), \
                    int((_node.get("source_span") or {}).get("end"))
                _cs, _ce = int((_cit.get("source_span") or {}).get("start")), \
                    int((_cit.get("source_span") or {}).get("end"))
                _covered = _ns <= _cs and _ce <= _ne
            except (TypeError, ValueError):
                _covered = False
            if not _covered:
                failures.append(f"A11: applied span not covered: {_a.get('citation_id')}")
                continue
            _sent = _cit.get("evidence_text", "")
            if not isinstance(_sent, str) or not _sent or _sent not in (_node.get("text") or ""):
                failures.append(f"A11: applied sentence not in node text: {_a.get('citation_id')}")
                continue
    # A6 types: no forbidden claim kinds.
    for c in claims:
        if c.get("type") not in ("JUDGMENT_FACT", "DISPOSITION", "HOLDING",
                                 "REASONING", "STATUTE", "ANSWER_SYNTHESIS",
                                 "LIMITATION"):
            failures.append(f"A6: forbidden claim type: {c.get('type')}")
    return (len(failures) == 0), failures


def _doc_of_evidence(eid: str) -> str | None:
    """Judgement embedded in judgement-evidence IDs ({type}:{jid}#...)."""
    try:
        head = eid.split("#", 1)[0]
        return head.split(":", 1)[1] or None
    except (IndexError, AttributeError):
        return None


def _all_statute_evs(response: dict, citations: list[dict]) -> list[dict]:
    out = []
    for c in citations:
        ev = c.get("statute_evidence")
        if ev and ev["evidence_id"] in response.get("evidence", {}).get("statutes", []):
            out.append(ev)
    return out


def _abstain(reason: str, **extra) -> dict:
    status = ABSTAINED if reason in ("GROUNDING_FAILURE", "CONTRADICTORY_EVIDENCE",
                                     "RETRIEVAL_IDENTITY_MISMATCH") \
        else INSUFFICIENT_EVIDENCE
    return {"status": status, "answer": None, "judgment": None, "judgements": [],
            "statutes": [], "evidence": {"judgment": [], "statutes": []},
            "claims": [], "citations": [],
            "abstention": {"reason": reason, "detail": ABSTAIN_REASONS[reason], **extra}}


def _enforce_continuity(graph: dict, chunks: list[dict]):
    """B4-A serving enforcement. Completes cut dispositions across proven
    contiguous chunks; assembles same-type adjacent COURT groups; returns None
    (caller abstains INSUFFICIENT_CONTINUITY_EVIDENCE) when a material
    (disposition/holding) group cannot be ordered. Returns the updated graph.
    """
    nodes = list(graph.get("nodes", []))
    by_chunk = {ch.get("chunk_id", ""): ch for ch in chunks}
    # 1. Complete cut dispositions whose continuation verifies by spans.
    for n in nodes:
        prov = n.get("provenance", {})
        if n.get("evidence_type") != "DISPOSITION" or not prov.get("incomplete"):
            continue
        nxt = by_chunk.get(prov.get("continued_chunk_id", ""), None)
        cur = by_chunk.get(n.get("chunk_id", ""), None)
        if nxt is None or cur is None:
            continue
        cs, ns = cur.get("span") or {}, nxt.get("span") or {}
        try:
            ok = (cs["start"] + n["source_span"]["end"] == ns["start"])
        except (KeyError, TypeError):
            ok = False
        if not ok:
            continue
        # Take continued text up to the next section header (or 500 chars).
        import re as _re
        tail = nxt.get("text", "")
        m = _re.search(r"事實及理由|理由要領|附錄|附件|犯罪事實", tail)
        add = tail[:m.start()] if m else tail[:500]
        n["text"] = n["text"] + add
        n["source_span"] = {**n["source_span"], "end": n["source_span"]["end"] + len(add)}
        prov.pop("incomplete", None)
        prov.pop("continues_in_next_chunk", None)
        prov["completed_from"] = nxt.get("chunk_id", "")
    # 2. Assemble same-type groups; drop uncompletable incomplete dispositions.
    nodes = [n for n in nodes
             if not (n.get("evidence_type") == "DISPOSITION"
                     and n.get("provenance", {}).get("incomplete"))]
    groups: dict[tuple, list[dict]] = {}
    for n in nodes:
        groups.setdefault((n.get("judgement_id", ""),
                           n.get("evidence_type", "")), []).append(n)
    texts = {ch.get("chunk_id", ""): ch.get("text", "") for ch in chunks}
    offsets = {}
    for ch in chunks:
        sp = ch.get("span") or {}
        if isinstance(sp.get("start"), int):
            offsets[ch.get("chunk_id", "")] = sp["start"]
    out, used = [], set()
    for (jid, etype), members in sorted(groups.items()):
        if len(members) < 2:
            out.extend(members)
            continue
        amap = attribute_graph_nodes(members, texts)
        rec = cont.assemble(members, evidence_type=etype,
                            attributions={k: amap.get(k, "UNRESOLVED_ATTRIBUTION")
                                          for k in [m["evidence_id"] for m in members]},
                            doc_offsets=offsets, chunk_texts=texts)
        if rec["status"] == "CONTINUITY_UNKNOWN" and etype in ("DISPOSITION", "HOLDING"):
            return None
        if rec["status"] in ("CONTIGUOUS", "ORDERED_GAPPED"):
            gate = cont.continuity_gate([rec])
            if gate["verdict"] != "PASS":
                return None
            multi = {"evidence_id": rec["evidence_id"], "evidence_type": etype,
                     "judgement_id": jid, "document_id": jid,
                     "chunk_id": rec["chunk_ids"][0], "text": rec["text"],
                     "source_span": {"start": 0, "end": len(rec["text"]),
                                     "unit": "code_point", "basis": "assembled"},
                     "provenance": {"multi_chunk": True,
                                    "continuity_status": rec["status"],
                                    "chunk_ids": rec["chunk_ids"],
                                    "source_spans": rec["source_spans"],
                                    "assembly_id": rec["evidence_id"]}}
            out.append(multi)
            used.update(m["evidence_id"] for m in members)
        else:
            out.extend(members)
    graph["nodes"] = out
    return graph


def attribute_graph_nodes(nodes: list[dict], chunk_texts: dict[str, str]) -> dict:
    """B3-A attribution per graph node (chunk text supplies section context).
    Pure; used by the serving path when attribution is enforced."""
    out = {}
    for n in nodes:
        text = chunk_texts.get(n["chunk_id"], n["text"])
        sp = n.get("source_span", {})
        s, e = sp.get("start", 0), sp.get("end", len(n.get("text", "")))
        rec = attr.classify_attribution(
            n.get("text", ""),
            context_before=text[max(0, s - 400):s],
            context_after=text[e:e + 400])
        out[n["evidence_id"]] = rec["attribution"]
    return out


async def serve_question(question: str, *, search_fn=None, embed_fn=None,
                         jfull_by_entry: dict | None = None,
                         statute_corpus: StatuteCorpus | None = None,
                         chunk_roles: dict | None = None,
                         recall: int = 50, top_k: int = 5,
                         require_statutes: bool = False,
                         retrieval_method: str | None = None,
                         enforce_confidence: bool = False,
                         enforce_attribution: bool = False,
                         enforce_temporal: bool = False,
                         enforce_contradiction: bool = False,
                         enforce_continuity: bool = False,
                         answer_mode: str = "single",
                         sections: list | None = None) -> dict:
    """Full B2-D chain with injectable boundaries (production or test doubles).

    Retrieval (B2-R1 max aggregation over hits) selects the judgement; B2-C
    graph + B2-B citations are built over its chunks; the answer is composed
    and gated. chunk_roles maps chunk_id -> corpus record (structural roles);
    without roles the path abstains honestly (INUFFICIENT_JUDGMENT_EVIDENCE).

    Confidence enforcement (B2-F): with enforce_confidence=True the retrieval
    ranking is judged by the confidence gate BEFORE any evidence work, using
    retrieval_method's calibrated thresholds. Non-ACCEPT abstains; the default
    False preserves the pre-B2-F composer contract (B1/B2-D tests pin it).

    answer_mode "single" (default) composes via build_answer; "sectioned"
    composes multi-evidence sections via b4b_compose with the same gates.
    Any other value raises ValueError immediately (programmer error, never
    user input). sections with single mode raises ValueError as well: an
    incompatible combination must fail fast, never silently discard scope.
    """
    if answer_mode not in ("single", "sectioned"):
        raise ValueError(f"unknown answer_mode: {answer_mode!r}")
    if answer_mode == "single" and sections is not None:
        raise ValueError("sections requires answer_mode='sectioned'")
    from .gateway import embed as _embed
    question = (question or "").strip()
    if not question:
        return _abstain("NO_JUDGMENT")
    corpus = statute_corpus or StatuteCorpus.from_jsonl()
    jfull_by_entry = jfull_by_entry if jfull_by_entry is not None else {}
    chunk_roles = chunk_roles if chunk_roles is not None else {}
    try:
        embed = embed_fn or _embed
        vector = (await embed([question]))[0]
        search = search_fn
        if search is None:
            async def search(q, v, limit):
                return await retrieve.search_judgments(q, v, limit=limit)
        hits = await search(question, vector, recall)
    except Exception as e:  # noqa: BLE001 - retrieval failure abstains (Case A)
        return _abstain("NO_JUDGMENT", error=type(e).__name__)
    if not hits:
        return _abstain("NO_JUDGMENT")
    if enforce_confidence:
        verdict = conf.evaluate_confidence(
            question,
            sorted(({"chunk_id": h.get("id"), "judgement_id": (h.get("payload") or {}).get("jid", ""),
                     "score": h.get("score", 0.0)} for h in hits),
                   key=lambda r: -float(r["score"] or 0.0))[:20],
            method=retrieval_method or "uncalibrated-serving")
        if verdict["status"] == "REJECT":
            return _abstain("RETRIEVAL_IDENTITY_MISMATCH",
                            confidence=verdict)
        if verdict["status"] != "ACCEPT":
            return _abstain("LOW_RETRIEVAL_CONFIDENCE",
                            confidence={k: v for k, v in verdict.items()
                                        if k in ("status", "reason", "gate_version")})
    ranked = aggregate_by_judgement(
        [{"chunk_id": h["id"] if "id" in h else h.get("chunk_id", ""),
          "judgement_id": (h.get("payload") or {}).get("jid", ""),
          "score": h.get("score", 0.0)} for h in hits[:max(top_k * 4, 20)]],
        method="max")
    if not ranked or not ranked[0]["judgement_id"]:
        return _abstain("NO_JUDGMENT")
    selected = ranked[0]["judgement_id"]
    # Assemble selected judgement's chunks: quotes (B1 reuse) + roles.
    try:
        jq = answer_judgments(
            question,
            [h for h in hits
             if (h.get("payload") or {}).get("jid") == selected][:max(top_k, 1)],
            jfull_by_entry=jfull_by_entry)
    except JudgmentAnswerError as e:
        return _abstain("INSUFFICIENT_JUDGMENT_EVIDENCE", error=str(e)[:200])
    if jq.get("no_match"):
        return _abstain("INSUFFICIENT_JUDGMENT_EVIDENCE", trace=jq.get("trace", ""))
    chunks = []
    for q in jq.get("quoted_blocks", []):
        view = q["view"]
        role = chunk_roles.get(view.get("id") or q.get("view", {}).get("id"))
        if role is None:
            return _abstain("INSUFFICIENT_JUDGMENT_EVIDENCE",
                            reason_detail="structural roles unavailable for retrieved chunks")
        chunks.append(role)
    graph_nodes_source = chunks
    graph = b2c.build_evidence_graph(graph_nodes_source, [])
    cites = []
    for ch in chunks:
        cites.extend(b2b.extract_citations(
            ch["text"], jid=selected, chunk_index=ch.get("chunk_index", 0),
            corpus=corpus))
    graph = b2c.build_evidence_graph(chunks, cites)
    evs = [c["statute_evidence"] for c in cites if c["statute_evidence"]]
    ok_c, _ = b2b.statute_gate(cites, evs, corpus)
    if not ok_c:
        return _abstain("STATUTE_EVIDENCE_UNRESOLVED")
    if enforce_contradiction:
        pre = _contradiction_precheck(graph, cites)
        if pre is not None:
            return pre
    if enforce_continuity:
        graph = _enforce_continuity(graph, chunks)
        if graph is None:
            return _abstain("INSUFFICIENT_CONTINUITY_EVIDENCE")
    amap = None
    if enforce_attribution:
        amap = attribute_graph_nodes(
            graph.get("nodes", []),
            {ch.get("chunk_id", ""): ch.get("text", "") for ch in chunks})
    tmap = None
    if enforce_temporal:
        from .b3b_temporal import load_law_meta, validate_temporal
        _meta = load_law_meta()
        _jdate = ""
        for _h in hits:
            if ((_h.get("payload") or {}).get("jid") == selected
                    and (_h.get("payload") or {}).get("jdate")):
                _jdate = (_h.get("payload") or {})["jdate"]
                break
        tmap = {}
        for _c in cites:
            if _c.get("resolution_status") != "RESOLVED":
                continue
            _ev = next((e for e in evs if e["evidence_id"] == _c.get("evidence_id")), None)
            _row = corpus.find(_ev.get("law_name", ""), _ev.get("article", "")) \
                if _ev is not None else None
            _rec = validate_temporal(
                judgement_id=selected, jdate=_jdate, citation=_c,
                statute_row=_row, law_meta=_meta.get((_ev or {}).get("law_name", ""), {}))
            tmap[_c["evidence_id"]] = _rec["status"]
    if answer_mode == "sectioned":
        from . import b4b_compose as b4b
        resp = b4b.compose_sectioned(
            question, judgement_id=selected, graph=graph,
            citations=cites, statute_evidences=evs,
            require_statutes=require_statutes, attribution=amap,
            temporal=tmap, sections=sections)
    else:
        resp = build_answer(question, judgement_id=selected, graph=graph,
                            citations=cites, statute_evidences=evs,
                            require_statutes=require_statutes, attribution=amap,
                            temporal=tmap)
    if enforce_contradiction and resp.get("status") == "ANSWERED":
        post = _contradiction_postcheck(resp, graph, cites)
        if post is not None:
            return post
    return resp
def _contradiction_precheck(graph: dict, cites: list[dict]):
    """Evidence-level contradiction gate (D1/D2/D4) before answer composition.
    Returns an abstention response on MATERIAL findings, else None."""
    nodes = graph.get("nodes", [])
    findings = contra.check_dispositions(
        [{"evidence_id": n["evidence_id"], "judgement_id": n["judgement_id"],
          "text": n["text"]} for n in nodes if n.get("evidence_type") == "DISPOSITION"])
    findings += contra.check_holdings(
        [{"evidence_id": n["evidence_id"], "judgement_id": n["judgement_id"],
          "chunk_id": n["chunk_id"], "text": n["text"]} for n in nodes
         if n.get("evidence_type") == "HOLDING"])
    findings += contra.check_statute_identity(cites)
    verdict = contra.contradiction_gate(findings)
    if verdict["verdict"] in ("FAIL", "ABSTAIN"):
        return _abstain("CONTRADICTORY_EVIDENCE", gate=verdict)
    return None


def _contradiction_postcheck(response: dict, graph: dict, citations: list[dict]):
    """Answer-level checks (D3 answer-vs-evidence, D5 metadata). Returns an
    abstention response on MATERIAL findings, else None."""
    if response.get("status") != "ANSWERED":
        return None
    texts = {n["evidence_id"]: n.get("text", "") for n in graph.get("nodes", [])}
    for c in citations:
        ev = c.get("statute_evidence") or {}
        if ev and ev.get("evidence_id"):
            texts[ev["evidence_id"]] = ev.get("text", "")
    evidences = [{"evidence_id": eid, "text": t} for eid, t in texts.items()]
    # ANSWER_SYNTHESIS claims verified as exact joins of cited evidence need
    # no per-evidence containment: the answer gate already proved the
    # decomposition against these same texts. Unverifiable synthesis still
    # faces the normal containment check (and fails it).
    checkable = [c for c in response.get("claims", [])
                 if not (c.get("type") == "ANSWER_SYNTHESIS"
                         and _synthesis_decomposes(c, texts))]
    findings = contra.check_answer_claims(checkable, evidences)
    findings += contra.check_metadata([
        {"judgement_id": (response.get("judgment") or {}).get("judgement_id", ""),
         "judgement_number": (response.get("judgment") or {}).get("judgement_number", ""),
         "date": (response.get("judgment") or {}).get("date", "")}])
    verdict = contra.contradiction_gate(findings)
    if verdict["verdict"] in ("FAIL", "ABSTAIN"):
        return _abstain("CONTRADICTORY_EVIDENCE", gate=verdict)
    return None




async def serve_live(question: str, *, recall: int = 50, top_k: int = 5, require_statutes: bool = False, answer_mode: str = "single", sections: list | None = None) -> dict:
    """Production entry: existing runtime only. JFULL comes from the frozen
    seed store (S1); chunk roles stay unwired (no role provenance for live
    chunks yet) and the live ranking method is uncalibrated -> still abstains
    honestly with reasons, now naming the actual missing piece."""
    if answer_mode == "single" and sections is not None:
        raise ValueError("sections requires answer_mode='sectioned'")
    try:
        from . import judgement_store as _store
        jfull = _store.jfull_map()
    except Exception:
        jfull = {}
    return await serve_question(question, recall=recall, top_k=top_k,
                                require_statutes=require_statutes,
                                answer_mode=answer_mode, sections=sections,
                                jfull_by_entry=jfull,
                                enforce_confidence=True,
                                retrieval_method="uncalibrated-serving",
                                enforce_attribution=True,
                                enforce_temporal=True,
                                enforce_contradiction=True,
                                enforce_continuity=True)
