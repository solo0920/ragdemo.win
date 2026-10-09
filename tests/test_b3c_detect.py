"""B3-C detection tests: D1-D7 rules on minimal fixtures (synthetic where no
real conflicting pair exists — courts are self-consistent; documented)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3c_contra as K  # noqa: E402


def disp(eid, jid, text):
    return {"evidence_id": eid, "judgement_id": jid, "text": text}


def test_d1_direct_same_judgment_opposed_outcomes():
    fs = K.check_dispositions([
        disp("d1", "J", "主文原告之訴駁回。"),
        disp("d2", "J", "主文准許原告之請求。")])
    assert len(fs) == 1 and fs[0]["status"] == "DETECTED"
    assert fs[0]["severity"] == "MATERIAL" and fs[0]["type"] == "C-DISPOSITION"
    assert fs[0]["resolution"] == "UNRESOLVED"


def test_d1_compatible_and_unknown():
    fs = K.check_dispositions([
        disp("d1", "J", "主文原告之訴駁回。"),
        disp("d2", "J", "主文原告之訴駁回。")])
    assert all(f["status"] == "COMPATIBLE" for f in fs)
    fs = K.check_dispositions([
        disp("d1", "J", "羅仕竤犯傷害罪，處拘役肆拾日。"),
        disp("d2", "J", "處有期徒刑一年。")])
    assert all(f["status"] == "UNKNOWN" for f in fs)
    # Different judgments are never compared by D1.
    assert K.check_dispositions([disp("d1", "J1", "駁回。"), disp("d2", "J2", "准許。")]) == []


def test_d2_same_chunk_opposed_holdings_and_cross_chunk_unknown():
    a = {"evidence_id": "h1", "judgement_id": "J", "chunk_id": "J#C1",
         "text": "原告之訴為無理由，應予駁回。"}
    b = {"evidence_id": "h2", "judgement_id": "J", "chunk_id": "J#C1",
         "text": "原告之訴為有理由，應予准許。"}
    fs = K.check_holdings([a, b])
    assert len(fs) == 1 and fs[0]["status"] == "DETECTED"
    c = dict(b, evidence_id="h3", chunk_id="J#C2")
    fs = K.check_holdings([a, c])
    assert fs[0]["status"] == "UNKNOWN"


def test_d3_answer_vs_evidence():
    evs = [{"evidence_id": "d1", "text": "原告之訴駁回。"}]
    ok = [{"claim_id": "C1", "type": "DISPOSITION", "text": "原告之訴駁回。",
           "evidence_ids": ["d1"]}]
    assert K.check_answer_claims(ok, evs) == []
    bad = [{"claim_id": "C1", "type": "DISPOSITION", "text": "原告之訴准許。",
            "evidence_ids": ["d1"]}]
    fs = K.check_answer_claims(bad, evs)
    assert len(fs) == 1 and fs[0]["status"] == "DETECTED"
    assert fs[0]["severity"] == "MATERIAL"
    missing = [{"claim_id": "C1", "type": "DISPOSITION", "text": "駁回。",
                "evidence_ids": ["nope"]}]
    fs = K.check_answer_claims(missing, evs)
    assert fs[0]["status"] == "DETECTED"


def test_d4_statute_identity_invariant():
    cites = [{"citation_id": "cit:J#c0:1-2", "evidence_id": "st:P#1",
              "judgement_id": "J"}]
    assert K.check_statute_identity(cites) == []
    dup = cites + [{"citation_id": "cit:J#c0:1-2", "evidence_id": "st:P#2",
                    "judgement_id": "J"}]
    fs = K.check_statute_identity(dup)
    assert len(fs) == 1 and fs[0]["status"] == "DETECTED"


def test_d5_metadata_identity():
    good = [{"judgement_id": "J", "judgement_number": "J", "date": "20260713"}]
    assert K.check_metadata(good + [dict(good[0])]) == []
    bad = good + [{"judgement_id": "J", "judgement_number": "JX", "date": "20260713"}]
    fs = K.check_metadata(bad)
    assert len(fs) == 1 and fs[0]["status"] == "DETECTED"


def test_d6_multi_judgment_opposed_and_compatible():
    fs = K.check_multi_judgments([
        {"judgement_id": "J1", "outcome": "DISMISSED"},
        {"judgement_id": "J2", "outcome": "GRANTED"}])
    assert len(fs) == 1 and fs[0]["status"] == "DETECTED"
    assert fs[0]["severity"] == "MATERIAL" and fs[0]["resolution"] == "UNRESOLVED"
    fs = K.check_multi_judgments([
        {"judgement_id": "J1", "outcome": "DISMISSED"},
        {"judgement_id": "J2", "outcome": "DISMISSED"}])
    assert fs == []
    fs = K.check_multi_judgments([{"judgement_id": "J1", "outcome": "DISMISSED"}])
    assert fs == []


def test_d7_temporal_never_contradiction():
    assert K.check_temporal(["CURRENT_ONLY", "TEMPORALLY_UNKNOWN",
                             "HISTORICAL_NOT_AVAILABLE"]) == []
