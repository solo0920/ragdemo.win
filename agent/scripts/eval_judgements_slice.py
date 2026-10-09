#!/usr/bin/env python3
"""S4: live judgement-slice evaluation (frozen seed index, no mocks).

Live probes B1 /judgments/query (serve_live) and B2-D serve_live over the real
embed + Qdrant + seed-store path. Zero-hits / unquotable / no-statute /
gate-fail abstentions are unit-pinned in tests/test_b1_abstention.py
(Cases A-D); live re-proves the reachable ones here.

Known frozen-semantics limitation (NOT changed by this slice): B1 quotes the
top retrieved chunks without a relevance floor, so on a 1-doc index even an
unrelated question returns real quotes. The honesty guarantees that DO hold:
quotes verbatim (offset+hash re-verified, acceptance ZERO fabricated) and
statutes corpus-verified. Irrelevant-grounding cases are reported below as
observations, never counted as successes.

Requires: Qdrant up with the S0 seed index, ollama reachable, CWD=repo root
(seed store is a relative path). Secrets via environment, never printed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app import b1_serve as B1  # noqa: E402
from app import b2d_answer as B2D  # noqa: E402
from app import judgement_store as JSTORE  # noqa: E402

ANSWERABLE = [
    ("fixture-question",
     "原告依民法第184條第1項及第195條第1項前段請求被告賠償非財產上損害新臺幣3萬元，法院准許了嗎？理由為何？",
     ["民法第184條", "第195條"]),
    ("article-184-only", "民法第184條第1項的侵權行為損害賠償，法院在本案怎麼認定？",
     ["民法第184條"]),
    ("article-195-only", "精神上損害3萬元是什麼依據？", ["第195條"]),
]

# B1 frozen semantics: top chunks are quoted regardless of relevance, so these
# are OBSERVED (quotes must still be verbatim + statutes verified), not
# abstention probes. Empty input abstains live (invalid_question).
IRRELEVANT_OBSERVED = [
    "今天天氣如何？",
    "關於外星人土地所有權的判決內容為何？",
]

SEED_JID = "STEV,115,店小,450,20260713,1"


def verify_evidence(resp: dict, jfull_by_entry: dict) -> tuple[int, int, int, int]:
    """(span_valid, span_invalid, stat_ok, stat_bad).

    Judgement quotes are re-sliced from seed JFULL (offset + sha256). Statute
    evidences carry no offsets by contract; their identity fields
    (law_name/article/text) are checked present instead — corpus verification
    itself is the B2-B gate's job, pinned by tests.
    """
    valid = invalid = stat_ok = stat_bad = 0
    for ev in resp.get("evidence", []):
        if ev.get("evidence_type") != "judgement":
            if ev.get("law_name") and ev.get("article") and ev.get("text"):
                stat_ok += 1
            else:
                stat_bad += 1
            continue
        prov = ev.get("provenance", {}) or {}
        jfull = jfull_by_entry.get(prov.get("entry_path", ""))
        if jfull is None:
            invalid += 1
            continue
        block = jfull[prov.get("start_offset", 0):prov.get("end_offset", 0)]
        if block == ev.get("text") and hashlib.sha256(
                block.encode("utf-8")).hexdigest() == prov.get("content_hash", ""):
            valid += 1
        else:
            invalid += 1
    return valid, invalid, stat_ok, stat_bad


async def _run() -> dict:
    jfull = JSTORE.jfull_map()
    out: dict = {"answerable": [], "empty": {}, "irrelevant": [], "b2d": [],
                 "fabricated_spans": 0, "verified_spans": 0}
    for pid, question, needs in ANSWERABLE:
        r = await B1.serve_live(question)
        ok = r.get("status") == "grounded"
        blob = json.dumps(r, ensure_ascii=False)
        found = sum(1 for n in needs if n in blob)
        v, iv, _, sb = verify_evidence(r, jfull)
        out["verified_spans"] += v
        out["fabricated_spans"] += iv
        out["stat_bad"] = out.get("stat_bad", 0) + sb
        top = (r.get("judgements") or [{}])[0].get("jid", "")
        out["answerable"].append({"id": pid, "grounded": ok, "top_jid_ok": top == SEED_JID,
                                  "needles_found": found, "needles": len(needs),
                                  "quotes_valid": v, "quotes_invalid": iv, "stat_bad": sb})
    r = await B1.serve_live("")
    out["empty"] = {"b1": (r.get("abstention") or {}).get("reason")}
    r2 = await B2D.serve_live("")
    out["empty"]["b2d"] = (r2.get("abstention") or {}).get("reason")
    for question in IRRELEVANT_OBSERVED:
        r = await B1.serve_live(question)
        v, iv, _, sb = verify_evidence(r, jfull)
        out["verified_spans"] += v
        out["fabricated_spans"] += iv
        out["stat_bad"] = out.get("stat_bad", 0) + sb
        out["irrelevant"].append({"q": question, "status": r.get("status"),
                                  "quotes_valid": v, "quotes_invalid": iv, "stat_bad": sb})
    r2 = await B2D.serve_live(ANSWERABLE[0][1])
    out["b2d"].append({"id": "fixture-question",
                       "abstained": r2.get("status") in (
                           "INSUFFICIENT_EVIDENCE", "ABSTAINED"),
                       "reason": (r2.get("abstention") or {}).get("reason")})
    return out


if __name__ == "__main__":
    import asyncio
    rep = asyncio.run(_run())
    n_ans = len(rep["answerable"])
    print(f"answerable grounded: {sum(1 for r in rep['answerable'] if r['grounded'])}/{n_ans}")
    print(f"top-jid correct: {sum(1 for r in rep['answerable'] if r['top_jid_ok'])}/{n_ans}")
    print(f"needles: {sum(r['needles_found'] for r in rep['answerable'])}/{sum(r['needles'] for r in rep['answerable'])}")
    print(f"empty abstains: b1={rep['empty']['b1']} b2d={rep['empty']['b2d']}")
    print(f"b2d fixture abstains honestly: {rep['b2d']}")
    print(f"irrelevant grounded (limitation, quotes must verify): "
          f"{[(o['status'], o['quotes_valid'], o['quotes_invalid']) for o in rep['irrelevant']]}")
    print(f"spans verified/fabricated: {rep['verified_spans']}/{rep['fabricated_spans']} "
          f"statute-identity-bad: {rep.get('stat_bad', 0)}")
    ok = (all(r["grounded"] and r["top_jid_ok"] and r["needles_found"] == r["needles"]
              for r in rep["answerable"])
          and rep["empty"]["b1"] == "invalid_question"
          and all(r["abstained"] for r in rep["b2d"])
          and all(o["quotes_invalid"] == 0 and o["quotes_valid"] > 0 for o in rep["irrelevant"])
          and rep["fabricated_spans"] == 0 and rep["verified_spans"] > 0
          and rep.get("stat_bad", 0) == 0)
    print("SLICE:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)
