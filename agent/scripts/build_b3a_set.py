#!/usr/bin/env python3
"""One-off B3-A verified-set builder (run by the batch author, then frozen).

Passages are hand-picked real corpus excerpts located by anchor needles;
expected labels are the author's verified judgments. The author reads every
recorded row against source text before freezing; tests assert exact equality
afterwards. Re-running overwrites (review required again).
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))
ART = Path("/home/solo/artifacts")

from app import b3a_attribution as A3  # noqa: E402

recs = {r["chunk_id"]: r for r in
        json.loads((ART / "t007e05cb/out/corpus_snapshot.json").read_text(encoding="utf-8"))["records"]}


def passage(chunk_id, start_needle, end_needle):
    t = recs[chunk_id]["text"]
    s = t.find(start_needle)
    assert s >= 0, (chunk_id, start_needle)
    e = t.find(end_needle, s)
    assert e >= 0, (chunk_id, end_needle)
    e += len(end_needle)
    ctx_before, ctx_after = t[max(0, s - 400):s], t[e:e + 400]
    return t[s:e], ctx_before, ctx_after


SPECS = [
    # C-01 party voice (incl. holding-language and prior-judgment cites inside).
    ("P-01", "TCEV,115,中簡,468,20260703,1#C004", "被告抗辯依96年度訴", "不需要給原告租金",
     "PARTY_VOICE", True),
    ("P-02", "KSHM,115,上訴,322,20260723,1#C002", "二、被告上訴意旨：", "酌減其刑等語",
     "PARTY_VOICE", True),
    ("P-03", "TNDV,114,重訴,219,20260710,2#C008", "被告蕭維", "即屬無據",
     "PARTY_VOICE", True),
    # C-02 quoted prior judgments.
    ("Q-01", "TCEV,115,中簡,468,20260703,1#C004", "（最高法院61年台上", "例意旨參照）",
     "QUOTED_OTHER_JUDGMENT", True),
    ("Q-02", "TNDV,114,重訴,219,20260710,2#C008", "已有上開最高法院裁判意旨供參", "供參",
     "QUOTED_OTHER_JUDGMENT", True),
    # C-03 quoted statutes.
    ("S-01", "CTDM,115,訴,1694,20260731,1#C006", "中華民國刑法第339條之4", "前項之未遂犯罰之",
     "QUOTED_STATUTE", True),
    ("S-02", "CHDM,115,簡,1769,20260730,1#C005", "而按司法院憲法法庭113年度", "實屬過苛」",
     "QUOTED_OTHER_JUDGMENT", True),
    # C-04 procedural.
    ("D-01", "CHDM,115,簡,1769,20260730,1#C002", "如不服本判決", "附繕本）。",
     "PROCEDURAL_DESCRIPTION", True),
    ("D-02", "CHDM,115,簡,1769,20260730,1#C005", "此 致", "正本證明",
     "PROCEDURAL_DESCRIPTION", True),
    # C-05 clean court voice.
    ("C-01", "CHDM,115,簡,1769,20260730,1#C002", "查被告與告訴人為叔嫂關係", "陳述明確",
     "COURT_VOICE", True),
    ("C-02", "TNDV,114,重訴,219,20260710,2#C008", "原告主張以附圖所", "尚屬有理",
     "PARTY_VOICE", True),  # endorsement nuance noted: fails closed either way (follow-up)
    # C-07 unresolved.
    ("U-01", "CHDM,115,簡,1769,20260730,1#C005", "經查，被告與告訴人", "不另為不起訴處分",
     "UNRESOLVED_ATTRIBUTION", True),
]

out = {"verification_status": "AUTHOR_REVIEWED",
       "provenance": "real frozen corpus excerpts located by anchor needles",
       "cases": []}
for pid, cid, s_needle, e_needle, expected, _ in SPECS:
    text, ctx_before, ctx_after = passage(cid, s_needle, e_needle)
    rec = A3.classify_attribution(text, context_before=ctx_before,
                                  context_after=ctx_after)
    out["cases"].append({
        "passage_id": pid, "chunk_id": cid, "passage": text,
        "expected_attribution": expected,
        "recorded_attribution": rec["attribution"],
        "recorded_rule": rec["rule_id"],
        "recorded_reason": rec["reason"],
        "match": rec["attribution"] == expected,
        "expected_eligible": expected == "COURT_VOICE",
        "recorded_eligible": rec["eligible_for_court_claim"]})
p = REPO / "tests/fixtures/b3a_attribution_set.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
n_match = sum(1 for c in out["cases"] if c["match"])
print(f"wrote {p} ({len(out['cases'])} passages, {n_match} match) — MANUAL REVIEW REQUIRED")
for c in out["cases"]:
    if not c["match"]:
        print("MISMATCH:", c["passage_id"], "expected", c["expected_attribution"],
              "got", c["recorded_attribution"], c["recorded_rule"])
