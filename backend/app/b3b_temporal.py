"""B3-B: statute temporal validity (deterministic, no LLM, no external data).

Answers one question per citation: was this statute version demonstrably
valid on the judgment date — and is the exact historical text available?

Statuses (never collapsed):
  HISTORICAL_VERIFIED / HISTORICAL_NOT_AVAILABLE / CURRENT_ONLY /
  TEMPORALLY_INVALID / TEMPORALLY_UNKNOWN
with separate validity {VALID, INVALID, UNKNOWN} and text
{EXACT_TEXT, CURRENT_TEXT_ONLY, UNAVAILABLE} axes.

Current corpus reality (capability audit, see report): statute rows carry NO
article intervals and NO version history; laws_meta carries law-level
modified/effective dates only. Per the brief's law/article rule, law-level
dates are recorded as provenance and NEVER upgrade article status. Hence on
real data only CURRENT_ONLY / TEMPORALLY_UNKNOWN are reachable; the
HISTORICAL_VERIFIED / TEMPORALLY_INVALID paths are implemented + unit-tested
with clearly-synthetic version fixtures so the machinery exists when
versioned data arrives. Terminal expectation: HOLD, not PASS-by-fabrication.
"""
from __future__ import annotations

import datetime as _dt
import functools as _functools
import json
from pathlib import Path

HISTORICAL_VERIFIED = "HISTORICAL_VERIFIED"
HISTORICAL_NOT_AVAILABLE = "HISTORICAL_NOT_AVAILABLE"
CURRENT_ONLY = "CURRENT_ONLY"
TEMPORALLY_INVALID = "TEMPORALLY_INVALID"
TEMPORALLY_UNKNOWN = "TEMPORALLY_UNKNOWN"

STATUSES = (HISTORICAL_VERIFIED, HISTORICAL_NOT_AVAILABLE, CURRENT_ONLY,
            TEMPORALLY_INVALID, TEMPORALLY_UNKNOWN)

# Fixed answer wordings derived from status (B2-D allowlists these).
LIMIT_CURRENT = "所列法規文字取自現行法規資料庫；現有資料無法確認其與判決日期當時的歷史版本文字一致。"
LIMIT_UNKNOWN = "部分法規的歷史版本資料不足，現有資料無法確認其在判決日期當時是否有效或文字是否一致。"
LIMIT_VERIFIED = "判決日期所對應的法條版本狀態已有資料確認，詳見各法規證據之版本註記。"
LIMIT_EXCLUDED = "部分引用的法規因版本時間不適用，未列出其條文內容。"
TEMPORAL_LIMITATIONS = (LIMIT_CURRENT, LIMIT_UNKNOWN, LIMIT_VERIFIED,
                        LIMIT_EXCLUDED)

LAWS_META_DEFAULT = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "laws" / "laws_meta.jsonl"
)
LAWS_SYNC_DEFAULT = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "laws" / ".law_sync.json"
)


def parse_compact_date(s: str | None) -> str | None:
    """Strict YYYYMMDD Gregorian validation; anything else -> None."""
    if not isinstance(s, str) or len(s) != 8 or not s.isdigit():
        return None
    try:
        _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None
    return s


@_functools.lru_cache(maxsize=4)
def _load_law_meta_cached(path_str: str) -> dict:
    out = {}
    try:
        with open(path_str, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    out.setdefault(d.get("law_name", ""), d)
    except (FileNotFoundError, ValueError):
        pass
    return out


def load_law_meta(path: str | Path = LAWS_META_DEFAULT) -> dict:
    """law_name -> meta row (modified/effective dates, histories). Cached."""
    return _load_law_meta_cached(str(path))


def corpus_sync_record(path: str | Path = LAWS_SYNC_DEFAULT) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def validate_temporal(*, judgement_id: str, jdate: str | None,
                      citation: dict, statute_row: dict | None,
                      law_meta: dict | None = None,
                      sync_record: dict | None = None,
                      version_interval: dict | None = None) -> dict:
    """Temporal record for one resolved citation.

    version_interval ({effective_from, effective_to, exact_text: bool}) is
    the ONLY path to HISTORICAL_VERIFIED/TEMPORALLY_INVALID and exists only
    in synthetic rule tests until a versioned corpus exists. Real rows never
    carry it: real outcomes are CURRENT_ONLY / TEMPORALLY_UNKNOWN.
    """
    law_meta = law_meta or {}
    sync_record = sync_record or {}
    base_prov = {
        "statute_source": "data/laws/laws_flat.jsonl",
        "corpus_sync": {k: sync_record.get(k) for k in
                        ("sha256", "update_date", "applied_at") if sync_record.get(k)},
        "validation_rule": None,
    }
    j = parse_compact_date(jdate)
    if j is None:
        return _rec(judgement_id, jdate, citation, statute_row, TEMPORALLY_UNKNOWN,
                    "UNKNOWN", "UNAVAILABLE", base_prov, "T-date-missing")
    if statute_row is None:
        return _rec(judgement_id, jdate, citation, statute_row, TEMPORALLY_UNKNOWN,
                    "UNKNOWN", "UNAVAILABLE", base_prov, "T-no-record")
    if version_interval is not None:
        return _interval_rule(judgement_id, jdate, citation, statute_row, j,
                              version_interval, base_prov)
    # Real-data path: no article intervals exist. Record law-level facts
    # without upgrading article status (law/article rule).
    base_prov["law_modified_date"] = (law_meta or {}).get("law_modified_date")
    base_prov["law_effective_date"] = (law_meta or {}).get("law_effective_date")
    return _rec(judgement_id, jdate, citation, statute_row, CURRENT_ONLY,
                "UNKNOWN", "CURRENT_TEXT_ONLY", base_prov, "T4-no-history")


def _interval_rule(judgement_id, raw_jdate, citation, row, jdate, interval, prov):
    start, end = interval.get("effective_from"), interval.get("effective_to")
    if not (parse_compact_date(start) and (end is None or parse_compact_date(end))):
        return _rec(judgement_id, raw_jdate, citation, row, TEMPORALLY_UNKNOWN,
                    "UNKNOWN", "UNAVAILABLE", prov, "T-interval-malformed")
    prov = dict(prov, effective_from=start, effective_to=end)
    if end is None:
        # Open-ended versions can never be HISTORICAL_VERIFIED: claiming the
        # version was still current at jdate needs a continuity proof (full
        # version list) that this record shape cannot carry (T2 strict).
        return _rec(judgement_id, raw_jdate, citation, row, TEMPORALLY_UNKNOWN,
                    "UNKNOWN", "UNAVAILABLE", prov, "T2-open-unproven")
    # Boundary equality is checked FIRST: landing exactly on an effective
    # date is ambiguous (no retroactivity rules invented) -> UNKNOWN, and
    # crucially never INVALID-by-equality.
    if jdate == start or jdate == end:
        return _rec(judgement_id, raw_jdate, citation, row, TEMPORALLY_UNKNOWN,
                    "UNKNOWN", "UNAVAILABLE", prov, "T-boundary-ambiguous")
    if not (start <= jdate < end):
        return _rec(judgement_id, raw_jdate, citation, row, TEMPORALLY_INVALID,
                    "INVALID", "UNAVAILABLE", prov, "T3-mismatch")
    # Inside a proven closed interval: validity established; exact text only
    # with explicit text evidence (never implied).
    if interval.get("exact_text"):
        return _rec(judgement_id, raw_jdate, citation, row, HISTORICAL_VERIFIED,
                    "VALID", "EXACT_TEXT", prov, "T1-interval")
    return _rec(judgement_id, raw_jdate, citation, row, HISTORICAL_NOT_AVAILABLE,
                "VALID", "UNAVAILABLE", prov, "T1-validity-without-text")


def _rec(judgement_id, raw_jdate, citation, row, status, validity, text, prov, rule):
    assert status in STATUSES, status
    return {
        "judgement_id": judgement_id,
        "judgement_date": raw_jdate or "",
        "citation_id": citation.get("citation_id", ""),
        "law_id": (row or {}).get("pcode", ""),
        "law_name": citation.get("normalized", {}).get("law_name", ""),
        "article": citation.get("normalized", {}).get("article", ""),
        "statute_version_id": None,
        "effective_from": prov.get("effective_from"),
        "effective_to": prov.get("effective_to"),
        "status": status,
        "validity": validity,
        "text_version": text,
        "provenance": {**prov, "validation_rule": rule},
    }


def temporal_gate(records: list[dict]) -> tuple[bool, list[str]]:
    """T1-T8 over temporal records."""
    failures: list[str] = []
    for r in records:
        cid = r.get("citation_id", "?")
        # T3: status explicit and known.
        if r.get("status") not in STATUSES:
            failures.append(f"T3: {cid} bad status")
        # T1: judgement date provenance-backed (8-digit validated or absent
        # with UNKNOWN status only).
        jd = r.get("judgement_date", "")
        if jd and parse_compact_date(jd) is None:
            failures.append(f"T1: {cid} malformed judgement date")
        if not jd and r.get("status") not in (TEMPORALLY_UNKNOWN,):
            failures.append(f"T1: {cid} dateless record claims {r.get('status')}")
        # T2: resolved statute identity exists (except explicitly recorded
        # absence: T-no-record carries no identity by definition).
        if not r.get("law_id") and r.get("provenance", {}).get("validation_rule") != "T-no-record":
            failures.append(f"T2: {cid} missing statute identity")
        # T6: validity and text axes distinguished.
        if r.get("validity") not in ("VALID", "INVALID", "UNKNOWN"):
            failures.append(f"T6: {cid} bad validity axis")
        if r.get("text_version") not in ("EXACT_TEXT", "CURRENT_TEXT_ONLY", "UNAVAILABLE"):
            failures.append(f"T6: {cid} bad text axis")
        if r.get("status") == HISTORICAL_VERIFIED and (
                r.get("validity") != "VALID" or r.get("text_version") != "EXACT_TEXT"):
            failures.append(f"T6: {cid} VERIFIED without validity+text proof")
        # T7: provenance retained.
        if not r.get("provenance", {}).get("statute_source"):
            failures.append(f"T7: {cid} missing statute source")
    # T4/T5/T8 are enforced at answer composition (see b2d integration tests),
    # not in this record-level gate.
    return (len(failures) == 0), failures
