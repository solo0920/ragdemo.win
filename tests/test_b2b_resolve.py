"""B2-B resolution + gate tests: exact identity, version flags, S1-S8."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

CORPUS = B1.StatuteCorpus.from_jsonl()


def run(text, **kw):
    return C.extract_citations(text, jid="J9", chunk_index=3, corpus=CORPUS, **kw)


def evidences(cites):
    return [c["statute_evidence"] for c in cites if c["statute_evidence"]]


def test_exact_identity_and_version_provenance():
    cs = run("依民法第184條第1項請求賠償。")
    assert len(cs) == 1
    ev = cs[0]["statute_evidence"]
    assert ev["document_id"] == "B0000001"  # pcode, exact
    assert ev["law_name"] == "民法" and ev["article"] == "第184條"
    prov = ev["provenance"]
    assert prov["corpus_version"]["sha256"] and prov["corpus_version"]["update_date"]
    assert prov["historical_text_unverified"] is True
    assert cs[0]["citation_id"] == "cit:J9#c3:1-11"


def test_orthography_mapping_flagged_not_silent():
    cs = run("依民事訴訟法第436條之18第1項之規定。")
    assert len(cs) == 1 and cs[0]["resolution_status"] == "RESOLVED"
    prov = cs[0]["statute_evidence"]["provenance"]
    assert prov["orthography_mapped"] is True
    assert prov["resolved_as"] == "第436-18條"
    assert prov["mention_surface"] == "民事訴訟法第436條之18第1項"


def test_branch_never_falls_back_to_base():
    row_base = CORPUS.find("民事訴訟法", "第436條")
    assert row_base is not None
    cs = run("依民事訴訟法第436條之18第1項之規定。")
    assert cs[0]["statute_evidence"]["document_id"] != row_base["pcode"] or \
        cs[0]["statute_evidence"]["chunk_id"] != f"{row_base['pcode']}#{row_base['article_seq']}"


def test_gate_passes_clean_mapping():
    cs = run("依民法第184條第1項、第195條第1項前段之規定。")
    ok, failures = C.statute_gate(cs, evidences(cs), CORPUS)
    assert ok, failures


def test_gate_s1_statute_without_citation():
    cs = run("依民法第184條第1項請求。")
    evs = evidences(cs) + [dict(evidences(cs)[0], evidence_id="st:FAKE#1")]
    ok, failures = C.statute_gate(cs, evs, CORPUS)
    assert not ok and any(f.startswith("S1") for f in failures)


def test_gate_s3_inexact_identity():
    cs = run("依民法第184條第1項請求。")
    evs = evidences(cs)
    bad = dict(evs[0], document_id="B0000002")
    ok, failures = C.statute_gate([dict(cs[0], evidence_id=bad["evidence_id"])],
                                  [bad], CORPUS)
    assert not ok and any(f.startswith("S3") for f in failures)


def test_gate_s4_surface_must_be_in_judgment_text():
    cs = run("依民法第184條第1項請求。")
    forged = dict(cs[0], raw_text="民法第999條")
    ok, failures = C.statute_gate([forged], evidences(cs), CORPUS)
    assert not ok and any(f.startswith("S4") for f in failures)


def test_gate_s5_s7_unresolved_represented_not_dropped():
    cs = run("係犯刑法第277條第1項之罪。依民法第184條請求。")
    ok, failures = C.statute_gate(cs, evidences(cs), CORPUS)
    assert ok, failures
    unres = [c for c in cs if c["resolution_status"].startswith("UNRESOLVED")]
    assert len(unres) == 1 and unres[0]["normalized"]["law_name"] == "刑法"


def test_gate_verdict_states():
    ok, _ = C.statute_gate([], [], CORPUS)
    assert ok  # vacuous pass on empty mapping; callers abstain upstream
