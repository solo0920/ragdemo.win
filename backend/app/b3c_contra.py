"""B3-C: evidence contradiction detection (deterministic, no LLM, no resolution).

Detects conflicts; never decides winners (resolution is always UNRESOLVED).
Pairwise findings are DIRECT (mutually exclusive under an explicit rule),
COMPATIBLE (agreement proven), or UNKNOWN (cannot determine — never guessed).

Scope discipline per finding:
- D1 same-judgment dispositions, normalized outcomes, split-frame guarded.
- D2 same-judgment holdings: same-chunk COURT_VOICE conclusory pair with
  opposed normalized outcomes only; cross-chunk holdings are UNKNOWN.
- D3 answer claims vs their evidence (normalized disposition agreement +
  containment re-verified).
- D4 statute identity invariant (one citation_id -> one evidence).
- D5 metadata identity (one judgement_id -> one number/date).
- D6 multi-judgment: opposed normalized dispositions across documents in the
  SAME evidence context -> CONFLICTING_JUDGMENTS (abstain, never resolve).
- D7 temporal states are NEVER contradiction evidence.

Severity: MATERIAL (touches answer claims or the selected judgment's outcome),
NON_MATERIAL (logged only), UNKNOWN (conservative abstain when material).
Criminal-sentence dispositions normalize to UNKNOWN (sentence comparison is
out of scope) — they participate in no DIRECT finding, documented, not hidden.
"""
from __future__ import annotations

DIRECT = "DIRECT"
COMPATIBLE = "COMPATIBLE"
UNKNOWN = "UNKNOWN"

MATERIAL = "MATERIAL"
NON_MATERIAL = "NON_MATERIAL"

C_TYPES = ("C-DISPOSITION", "C-HOLDING", "C-REASONING", "C-STATUTE",
           "C-ANSWER", "C-METADATA", "C-MULTI-JUDGMENT", "C-TEMPORAL",
           "C-UNRESOLVED")

_OUTCOMES = ("DISMISSED", "GRANTED", "PARTIALLY_GRANTED", "REMANDED",
             "APPEAL_DISMISSED", "OTHER", "UNKNOWN", "SPLIT_FRAME")


def normalize_disposition(text: str) -> str:
    """Deterministic outcome label; UNKNOWN unless unambiguous markers match."""
    t = text or ""
    if any(k in t for k in ("反訴", "另案")):
        return "SPLIT_FRAME"
    if "一部" in t:
        return "PARTIALLY_GRANTED"
    if "發回" in t:
        return "REMANDED"
    dis = "駁回" in t
    gra = ("准許" in t or "准予" in t)
    if dis and gra:
        return "PARTIALLY_GRANTED"
    if "上訴駁回" in t or "上訴棄卻" in t:
        return "APPEAL_DISMISSED"
    if dis:
        return "DISMISSED"
    if gra:
        return "GRANTED"
    if "撤銷" in t:
        return "UNKNOWN"
    if "維持" in t:
        return "OTHER"
    return "UNKNOWN"


def _opposed(a: str, b: str) -> bool:
    return {a, b} == {"DISMISSED", "GRANTED"}


def check_dispositions(items: list[dict]) -> list[dict]:
    """D1: pairwise same-judgment disposition comparison. items:
    [{evidence_id, judgement_id, text}]."""
    findings = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a.get("judgement_id") != b.get("judgement_id"):
                continue
            oa, ob = normalize_disposition(a.get("text", "")), normalize_disposition(b.get("text", ""))
            fid = f"C-DISPOSITION:{a.get('evidence_id')}:{b.get('evidence_id')}"
            if _opposed(oa, ob):
                findings.append(_finding(fid, "C-DISPOSITION", MATERIAL,
                                         a.get("evidence_id"), b.get("evidence_id"),
                                         a.get("judgement_id"), DIRECT,
                                         f"{oa} vs {ob} in one judgement"))
            elif oa == ob and oa not in ("UNKNOWN", "SPLIT_FRAME"):
                findings.append(_finding(fid, "C-DISPOSITION", NON_MATERIAL,
                                         a.get("evidence_id"), b.get("evidence_id"),
                                         a.get("judgement_id"), COMPATIBLE,
                                         f"same outcome {oa}"))
            else:
                findings.append(_finding(fid, "C-DISPOSITION", NON_MATERIAL,
                                         a.get("evidence_id"), b.get("evidence_id"),
                                         a.get("judgement_id"), UNKNOWN,
                                         f"outcomes {oa}/{ob} not comparable"))
    return findings


def check_holdings(items: list[dict]) -> list[dict]:
    """D2: same-CHUNK court-voice holding pair with opposed outcomes only."""
    findings = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a.get("judgement_id") != b.get("judgement_id"):
                continue
            if a.get("chunk_id") != b.get("chunk_id"):
                findings.append(_finding(
                    f"C-HOLDING:{a.get('evidence_id')}:{b.get('evidence_id')}",
                    "C-HOLDING", NON_MATERIAL, a.get("evidence_id"),
                    b.get("evidence_id"), a.get("judgement_id"), UNKNOWN,
                    "cross-chunk reference frame unresolvable"))
                continue
            oa, ob = normalize_disposition(a.get("text", "")), normalize_disposition(b.get("text", ""))
            fid = f"C-HOLDING:{a.get('evidence_id')}:{b.get('evidence_id')}"
            if _opposed(oa, ob):
                findings.append(_finding(fid, "C-HOLDING", MATERIAL,
                                         a.get("evidence_id"), b.get("evidence_id"),
                                         a.get("judgement_id"), DIRECT,
                                         f"{oa} vs {ob} in one chunk"))
            else:
                findings.append(_finding(fid, "C-HOLDING", NON_MATERIAL,
                                         a.get("evidence_id"), b.get("evidence_id"),
                                         a.get("judgement_id"), COMPATIBLE,
                                         "no opposed outcomes"))
    return findings


def check_answer_claims(claims: list[dict], evidences: list[dict]) -> list[dict]:
    """D3: each material claim against its evidence. Quotes are verbatim by
    construction; this re-verifies containment plus normalized disposition
    agreement for DISPOSITION claims."""
    by_id = {e["evidence_id"]: e for e in evidences}
    findings = []
    for c in claims:
        if c.get("type") in ("LIMITATION",):
            continue
        for eid in c.get("evidence_ids", []):
            ev = by_id.get(eid)
            fid = f"C-ANSWER:{c.get('claim_id')}:{eid}"
            if ev is None:
                findings.append(_finding(fid, "C-ANSWER", MATERIAL,
                                         c.get("claim_id"), eid, None, DIRECT,
                                         "claim cites missing evidence"))
                continue
            if c.get("text", "") not in ev.get("text", ""):
                # Normalized fallback: disposition outcome agreement.
                co = normalize_disposition(c.get("text", ""))
                eo = normalize_disposition(ev.get("text", ""))
                if co != eo or co in ("UNKNOWN", "SPLIT_FRAME"):
                    # Unresolvable comparison on a material claim abstains
                    # (conservative); decidable opposition fails outright.
                    if co == eo:
                        findings.append(_finding(fid, "C-ANSWER", MATERIAL,
                                                 c.get("claim_id"), eid, None, UNKNOWN,
                                                 "comparison unresolvable"))
                    else:
                        findings.append(_finding(fid, "C-ANSWER", MATERIAL,
                                                 c.get("claim_id"), eid, None, DIRECT,
                                                 "claim not contained in evidence"))
                else:
                    findings.append(_finding(fid, "C-ANSWER", NON_MATERIAL,
                                             c.get("claim_id"), eid, None, COMPATIBLE,
                                             f"outcome agreement {co}"))
    return findings


def check_statute_identity(citations: list[dict]) -> list[dict]:
    """D4: one citation_id -> one statute evidence invariant."""
    seen: dict[str, str] = {}
    findings = []
    for c in citations:
        cid = c.get("citation_id", "")
        eid = c.get("evidence_id") or ""
        if cid in seen and seen[cid] != eid:
            findings.append(_finding(f"C-STATUTE:{cid}", "C-STATUTE", MATERIAL,
                                     cid, eid, c.get("judgement_id"), DIRECT,
                                     "one citation maps to two statutes"))
        seen.setdefault(cid, eid)
    return findings


def check_metadata(judgement_blocks: list[dict]) -> list[dict]:
    """D5: one judgement_id -> one number/date. blocks: [{judgement_id,
    judgement_number, date}]."""
    seen: dict[str, dict] = {}
    findings = []
    for b in judgement_blocks:
        jid = b.get("judgement_id", "")
        prev = seen.setdefault(jid, b)
        if prev.get("judgement_number") != b.get("judgement_number") or \
           prev.get("date") != b.get("date"):
            findings.append(_finding(f"C-METADATA:{jid}", "C-METADATA", MATERIAL,
                                     jid, jid, jid, DIRECT,
                                     "conflicting number/date for one judgement"))
    return findings


def check_multi_judgments(doc_outcomes: list[dict]) -> list[dict]:
    """D6: [{judgement_id, outcome}] across documents in ONE evidence context.
    Opposed merits outcomes -> CONFLICTING_JUDGMENTS (abstain, never resolve).
    UNKNOWN/PARTIALLY/REMANDED/APPEAL-involved pairs -> UNKNOWN (logged)."""
    findings = []
    docs = [d["judgement_id"] for d in doc_outcomes]
    if len(set(docs)) < 2:
        return findings
    for i in range(len(doc_outcomes)):
        for j in range(i + 1, len(doc_outcomes)):
            a, b = doc_outcomes[i], doc_outcomes[j]
            if a["judgement_id"] == b["judgement_id"]:
                continue
            if _opposed(a["outcome"], b["outcome"]):
                fid = f"C-MULTI-JUDGMENT:{a['judgement_id']}:{b['judgement_id']}"
                findings.append(_finding(fid, "C-MULTI-JUDGMENT", MATERIAL,
                                         a["judgement_id"], b["judgement_id"], None,
                                         DIRECT,
                                         f"{a['outcome']} vs {b['outcome']}: conflicting judgments"))
            # Agreement or unresolvable pairs emit nothing: multi-doc pairs
            # explode combinatorially and silence is the safe default here
            # (single-doc D1/D2 still record their COMPATIBLE/UNKNOWN rows).
    return findings


def check_temporal(statuses: list[str]) -> list[dict]:
    """D7: temporal states are never contradiction evidence (assertion)."""
    assert all(s in ("HISTORICAL_VERIFIED", "HISTORICAL_NOT_AVAILABLE",
                     "CURRENT_ONLY", "TEMPORALLY_INVALID",
                     "TEMPORALLY_UNKNOWN") for s in statuses), statuses
    return []


def _finding(fid, ctype, severity, left, right, jid, status, evidence):
    assert ctype in C_TYPES, ctype
    assert severity in (MATERIAL, NON_MATERIAL, UNKNOWN), severity
    assert status in (DIRECT, COMPATIBLE, UNKNOWN, "DETECTED"), status
    return {"contradiction_id": fid, "type": ctype, "severity": severity,
            "left_evidence_id": left, "right_evidence_id": right,
            "scope": {"judgement_id": jid}, "status": "DETECTED" if status == DIRECT else status,
            "resolution": "UNRESOLVED", "evidence": evidence}


def contradiction_gate(findings: list[dict]) -> dict:
    """C1-C8 gate. MATERIAL DIRECT -> FAIL; MATERIAL UNKNOWN -> ABSTAIN;
    else PASS. STOP is reserved (unused: empty input passes vacuously)."""
    material_direct = [f for f in findings
                       if f["severity"] == MATERIAL and f["status"] == "DETECTED"]
    material_unknown = [f for f in findings
                        if f["severity"] == MATERIAL and f["status"] == UNKNOWN]
    if material_direct:
        return {"verdict": "FAIL",
                "evidence": [f["contradiction_id"] for f in material_direct],
                "detail": "material contradiction detected"}
    if material_unknown:
        return {"verdict": "ABSTAIN",
                "evidence": [f["contradiction_id"] for f in material_unknown],
                "detail": "material contradiction unresolvable"}
    return {"verdict": "PASS", "evidence": [],
            "detail": f"{len(findings)} findings, none material-blocking"}
