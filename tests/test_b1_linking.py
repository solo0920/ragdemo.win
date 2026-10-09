"""B1 unit: Level-1 statute linking — explicit mention + corpus proof, else unlinked."""
from __future__ import annotations

import sys
from pathlib import Path

import b1_helpers as H
from app import b1_serve as B1

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))


def test_demo_case_links_expected_statutes():
    corpus = H.real_corpus()
    fx = H.load_case()
    mentions = B1.extract_statute_mentions(fx["document"]["JFULL"], corpus)
    linked = {(m.law_name, m.article) for m in mentions if corpus.find(m.law_name, m.article)}
    for law, art in fx["expected_linked_statutes"]:
        assert (law, art) in linked, f"expected link missing: {law}{art}"


def test_zhi_branch_never_falls_back_to_base_article():
    corpus = H.real_corpus()
    mentions = B1.extract_statute_mentions("依民事訴訟法第436條之18第1項之規定", corpus)
    assert [(m.law_name, m.article) for m in mentions] == [("民事訴訟法", "第436條之18")]
    row = corpus.find("民事訴訟法", "第436條之18")
    # Corpus renders inserts hyphen-form: resolves to 436-18, flagged — never to base 436.
    assert row is not None and row["article_no"].replace(" ", "") == "第436-18條"
    assert corpus.rows.get(("民事訴訟法", "第436條")) is not None  # base exists separately
    ev = B1.make_statute_evidence(row, mentions[0])
    assert ev["provenance"]["orthography_mapped"] is True
    assert ev["provenance"]["resolved_as"] == "第436-18條"


def test_xiang_kuan_detail_preserved_but_lookup_uses_article():
    corpus = H.real_corpus()
    mentions = B1.extract_statute_mentions("依民法第184條第1項之規定", corpus)
    assert len(mentions) == 1
    m = mentions[0]
    assert (m.law_name, m.article, m.detail) == ("民法", "第184條", "第1項")
    assert corpus.find(m.law_name, m.article) is not None


def test_unknown_law_name_does_not_link():
    corpus = H.real_corpus()
    assert corpus.find("不存在法", "第1條") is None
    assert B1.extract_statute_mentions("依不存在法第1條", corpus) == []


def test_bare_article_scoped_to_same_sentence_law():
    corpus = H.real_corpus()
    mentions = B1.extract_statute_mentions("爰依民法第184條第1項、第195條第1項前段之規定", corpus)
    by_art = {(m.law_name, m.article): m for m in mentions}
    assert ("民法", "第184條") in by_art
    scoped = by_art[("民法", "第195條")]
    assert scoped.scoped is True
    assert scoped.scoped_from == "民法第184條第1項"


def test_bare_article_without_sentence_law_stays_unlinked():
    corpus = H.real_corpus()
    mentions = B1.extract_statute_mentions("第999條規定很清楚。依民法第184條辦理。", corpus)
    assert [(m.law_name, m.article) for m in mentions] == [("民法", "第184條")]


def test_wrapped_mention_across_line_break_links():
    corpus = H.real_corpus()
    mentions = B1.extract_statute_mentions("依據：民事訴訟法第78條、刑事訴訟法第50\r\n    3條第3項。", corpus)
    found = {(m.law_name, m.article): m for m in mentions}
    assert ("刑事訴訟法", "第503條") in found
    assert found[("刑事訴訟法", "第503條")].surface_collapsed is True


def test_cjk_numerals_normalize():
    assert B1.cjk_numeral_to_arabic("四十四") == "44"
    assert B1.cjk_numeral_to_arabic("一百一十四") == "114"
    assert B1.normalize_article("第 184 條") == "第184條"
    assert B1.cjk_numeral_to_arabic("xyz") is None
