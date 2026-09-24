"""normalize：法規清洗/扁平化純函式。"""
import normalize

A = normalize  # type: ignore[attr-defined]


def _law(**kw):
    law = {
        "LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode=A0000001",
        "LawName": "測試法", "LawCategory": "行政", "LawAbandonNote": "",
    }
    law.update(kw)
    return law


def test_clean_crlf_and_blank():
    assert A._clean("A\r\nB\r\n\r\nC") == "A\nB\nC"
    assert A._clean("  A\nB   ") == "A\nB"
    assert A._clean("") == "" and A._clean(None) == ""


def test_is_repealed_variants():
    assert A._is_repealed("（刪除）")
    assert A._is_repealed("（刪除）後註")
    assert A._is_repealed("(刪除)")
    assert A._is_repealed("刪除")
    assert A._is_repealed("  （刪除）  ")
    assert not A._is_repealed("第259條")
    assert not A._is_repealed("")
    assert not A._is_repealed(" ")


def test_pcode_of():
    assert A.pcode_of({"LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode=A0000001"}) == "A0000001"
    assert A.pcode_of({"LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode=A0000001/"}) == "A0000001"
    assert A.pcode_of({}) == ""  # 缺 URL → 空字串（不得崩潰）


def test_law_articles_chapter_stamps():
    law = _law(LawArticles=[
        {"ArticleType": "C", "ArticleContent": "第二章  契約"},
        {"ArticleType": "A", "ArticleNo": "第1條", "ArticleContent": "內容一。\r\n續。\r\n"},
        {"ArticleType": "C", "ArticleContent": "第三章  解約"},
        {"ArticleType": "A", "ArticleNo": " 第2條 ", "ArticleContent": "（刪除）"},
    ])
    arts = A.law_articles(law)
    assert [r["article_no"] for r in arts] == ["第1條", "第2條"]
    assert arts[0]["chapter"] == "第二章  契約"
    assert arts[1]["chapter"] == "第三章  解約"   # 章節往前帶
    assert arts[0]["char_len"] == len("內容一。\n續。")
    assert arts[0]["is_repealed"] is False
    assert arts[1]["is_repealed"] is True         # （刪除）
    assert arts[1]["is_abandoned"] is False
    assert arts[0]["pcode"] == "A0000001"


def test_law_articles_abandon_flag_and_seq():
    law = _law(LawAbandonNote="已廢止",
               LawArticles=[{"ArticleType": "A", "ArticleNo": "第1條", "ArticleContent": "x"},
                            {"ArticleType": "A", "ArticleNo": "第2條", "ArticleContent": "y"}])
    arts = A.law_articles(law)
    assert [r["article_seq"] for r in arts] == [1, 2]  # seq 連續
    assert all(r["is_abandoned"] for r in arts)


def test_law_articles_empty_and_c_only():
    assert A.law_articles(_law()) == []
    assert A.law_articles(_law(LawArticles=[
        {"ArticleType": "C", "ArticleContent": ""},      # 空章節標題不設章
        {"ArticleType": "A", "ArticleNo": "第1條", "ArticleContent": "x"}]))[0]["chapter"] == ""