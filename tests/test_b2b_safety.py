"""B2-B safety tests: refusal is explicit, never silent, never substituted."""
from __future__ import annotations

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
    return C.extract_citations(text, jid="J", chunk_index=0, corpus=CORPUS, **kw)


def test_short_form_refused_not_resolved():
    cs = run("係犯刑法第277條第1項之傷害罪。")
    assert len(cs) == 1
    assert cs[0]["resolution_status"] == "UNRESOLVED:unknown_alias"
    assert cs[0]["evidence_id"] is None


def test_refused_parallel_construction_not_reattributed():
    # 第16條's nearest preceding article event is refused 憲法第8條: stays refused.
    cs = run("違反憲法第8條及第16條保障訴訟權之意旨。")
    by_raw = {c["raw_text"]: c for c in cs}
    assert by_raw["憲法第8條"]["resolution_status"] == "UNRESOLVED:unknown_alias"
    assert by_raw["第16條"]["resolution_status"] == "UNRESOLVED:unknown_alias"
    assert by_raw["第16條"]["scoped_from"] == "憲法第8條"


def test_full_name_inside_refusal_scope_wins():
    cs = run("依中華民國刑法第339條之規定。")
    assert len(cs) == 1 and cs[0]["resolution_status"] == "RESOLVED"
    assert cs[0]["normalized"]["law_name"] == "中華民國刑法"


def test_nonexistent_article_refused_not_substituted():
    cs = run("依民法第9999條之規定。")
    assert len(cs) == 1
    assert cs[0]["resolution_status"] == "UNRESOLVED:no_corpus_match"


def test_non_statute_lookalikes_produce_nothing():
    for text in ["系爭契約第一條約定不明", "41年鬮書協議分割", "見本院卷第55",
                 "前項之未遂犯罰之", "原告之訴駁回"]:
        assert run(text) == [], text


def test_bare_article_without_any_anchor_dropped_openly():
    # No law anywhere: nothing to attribute to — zero citations, not a guess.
    assert run("第5條規定很清楚。") == []


def test_no_applied_or_decisive_labels_exist():
    cs = run("依民法第184條第1項請求賠償。")
    assert all(c["citation_type"] == "EXPLICIT_CITATION" for c in cs)
    assert "APPLIED" not in C.CITATION_TYPE and "DECISIVE" not in C.CITATION_TYPE
