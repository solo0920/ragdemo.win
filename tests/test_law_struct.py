"""law_struct：法條「項/款」結構解析（純函式）。"""
from app import law_struct as L


def test_structure_items_arabic_cjk_and_digit():
    text = "一、甲義務\n二、乙義務\n（三）丙義務\n1. 丁義務\n1、戊義務"
    s = L.structure(text)
    assert s["para"] == 1
    assert [label for label, _ in s["items"]] == ["一、", "二、", "（三）", "1.", "1、"]


def test_structure_paras_no_items():
    text = "殺人者，處死刑、無期徒刑或十年以上有期徒刑。\n前項之未遂犯罰之。\n預備犯第一項之罪者，處二年以下有期徒刑。"
    s = L.structure(text)
    assert s["para"] == 3
    assert s["items"] == []


def test_structure_empty():
    s = L.structure("")
    assert s == {"para": 0, "items": []}
    assert L.structure(None) == {"para": 0, "items": []}


def test_summarize_variants():
    # 一項多款 → 「1項6款」（對應民法259）
    text = "\n".join(f"{i}、" for i in ["一", "二", "三", "四", "五", "六"]) + "各履行義務。"
    assert L.summarize(text) == "1項6款"
    # 多段無款 → 「3項0款」（對應刑法271）
    para3 = "第一段。\n第二段。\n第三段。"
    assert L.summarize(para3) == "3項0款"
    # 單段無款 → 空字串
    assert L.summarize("唯一一段。") == ""
    assert L.summarize("") == ""


def test_cite_item():
    assert L.cite_item("") == "全文"
    assert L.cite_item("唯一一段。") == "全文"
    assert L.cite_item("一、返還之。\n二、利息。") == "第一款"
    assert L.cite_item("（三）丙義務。") == "第三款"
    assert L.cite_item("1、丁義務。") == "第1款"
    # 多段無款 → 第1項
    assert L.cite_item("第一段。\n第二段。") == "第1項"
    # 阿拉伯中文「十一、」正規化
    assert L.cite_item("十一、多款。") == "第十一款"


def test_cjk_label_up_to_ten():
    assert L._label_no("（十）") == "十"
    assert L._label_no("一、") == "一"
    assert L._label_no("1.") == "1"