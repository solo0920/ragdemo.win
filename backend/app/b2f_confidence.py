"""B2-F: judicial retrieval confidence gate (deterministic, no LLM, no model).

Decides whether a Rank-1 judgement is trustworthy enough to enter the answer
layer. NEVER changes ranking. Operates on one ranking only.

Feature evidence (from frozen-data probes, documented in the B2-F report):
- margin (rank1_score - rank2_score): separates (OK med >> BAD med). SCALEFUL:
  thresholds are per-method; unknown methods ABSTAIN (cannot judge).
- identity mismatch (explicit case number in query vs rank-1 doc): rare but
  strong -> REJECT. No fuzzy persons, no invented constraints.
- concentration / lexical coverage / cross-method agreement: measured and
  REJECTED as gate features (no separation, inverted, or saturated). They are
  reported, never gating. See report for the negative evidence.

Gate states: ACCEPT (may enter B2-D), REJECT (explicit mismatch),
ABSTAIN (insufficient confidence or unjudgeable input).
"""
from __future__ import annotations

import re

GATE_VERSION = "b2f/v1"

# Per-method margin thresholds (absolute score units of that method).
# Calibrated in B2-F (see report §calibration); pinned here, tested here.
# Rule: max coverage subject to zero wrong-accepts on the calibration split.
# bm25: 38.742660905265 (cal 3/37) | snowflake-C: 0.12242048428695307 (cal 0/37).
# A method without an entry cannot be judged -> ABSTAIN.
MARGIN_THRESHOLDS: dict[str, float] = {
    "bm25": 38.742660905265,
    "snowflake-C": 0.12242048428695307,
}

CASE_PATTERN = re.compile(r"(\d+)年度(.{1,8}?)字第(\d+)號")


def _case_numbers(text: str) -> list[tuple[str, str, str]]:
    """Explicit case references as (year, middle, number) triples."""
    return CASE_PATTERN.findall(text or "")


def _norm_case(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _identity_match(pattern: tuple[str, str, str], judgement_id: str) -> bool:
    """Field-wise JID comparison: JID shape COURT,YEAR,TYPE,NUMBER,..."""
    year, middle, number = pattern
    parts = _norm_case(judgement_id).split(",")
    if len(parts) < 4:
        return False
    if parts[1] != year or parts[3] != number:
        return False
    jtype = parts[2]
    return middle in jtype or jtype in middle


def evaluate_confidence(query_text: str, ranked: list[dict], *,
                        method: str,
                        thresholds: dict[str, float] | None = None,
                        top_n: int = 20) -> dict:
    """ranked: [{chunk_id, judgement_id, score}] in rank order (re-sorted
    defensively). Returns the confidence report (always a dict, never raises
    on data shape: malformed input -> ABSTAIN with reason)."""
    th = MARGIN_THRESHOLDS if thresholds is None else thresholds
    feats: dict = {"method": method, "gate_version": GATE_VERSION,
                   "n_ranked": len(ranked)}
    try:
        rows = sorted(ranked, key=lambda r: (-float(r["score"]), str(r.get("chunk_id", ""))))
    except (KeyError, TypeError, ValueError):
        return _report("ABSTAIN", feats, "malformed-ranking")
    if len(rows) < 2:
        return _report("ABSTAIN", feats, "insufficient-ranking-depth")
    r1, r2 = rows[0], rows[1]
    try:
        s1, s2 = float(r1["score"]), float(r2["score"])
    except (KeyError, TypeError, ValueError):
        return _report("ABSTAIN", feats, "malformed-scores")
    j1 = str(r1.get("judgement_id") or "")
    feats.update({"rank1_score": s1, "rank2_score": s2,
                  "score_margin": s1 - s2,
                  "rank1_judgement_id": j1,
                  "rank1_chunk_id": str(r1.get("chunk_id", ""))})
    # Identity consistency (REJECT on explicit mismatch only).
    for pat in _case_numbers(query_text):
        if not _identity_match(pat, j1):
            feats["identity"] = {"query_pattern": "".join(pat),
                                 "rank1_judgement_id": j1, "match": False}
            return _report("REJECT", feats, "explicit-identity-mismatch")
    if _case_numbers(query_text):
        feats["identity"] = {"match": True}
    # Method must be calibrated.
    if method not in th:
        return _report("ABSTAIN", feats, "unknown-method")
    margin = s1 - s2
    if margin >= th[method]:
        return _report("ACCEPT", feats, f"margin {margin:.4g} >= {th[method]}")
    return _report("ABSTAIN", feats, f"margin {margin:.4g} < {th[method]}")


def _report(status: str, feats: dict, reason: str) -> dict:
    assert status in ("ACCEPT", "REJECT", "ABSTAIN"), status
    return {"status": status,
            "score": feats.get("score_margin"),
            "features": feats,
            "gate_version": GATE_VERSION,
            "reason": reason}
