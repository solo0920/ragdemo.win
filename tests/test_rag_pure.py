"""rag 的純渲染函式（_ref/_hit_view）——不觸發任何網路。"""
from types import SimpleNamespace

import pytest

# import 觸發後端設定載入；_ref 已於 2026-09-29 隨檢索層搬去 retrieve。
from app import rag, law_struct  # noqa: F401  # import 觸發後端設定載入
from app.common import sparse  # noqa: F401


def _law_hit(payload_extra=None, text="一、返還之。\n二、利息。", score=0.9):
    p = {
        "law_name": "民法", "article_no": " 第259條 ", "chapter": "",
        "text": text, "pcode": "B0000001", "is_repealed": False, "is_abandoned": False,
    }
    p.update(payload_extra or {})
    return {"score": score, "payload": p}


def test_hit_view_law_fields():
    v = rag._hit_view(_law_hit(score=0.92))
    assert v["score"] == 0.92
    assert v["art"] == "第259條"                      # article_no 去空白
    assert v["law_name"] == "民法"
    assert v["item"] == "第一款"
    assert v["para_count"] == 1
    assert v["item_count"] == 2


def test_hit_view_case_fallback():
    p = {"case_no": "臺灣高等法院123", "law": "民法第259條", "text": "判決理由。\n續。", "law_name": ""}
    v = rag._hit_view({"score": 0.5, "payload": p})
    assert v["art"] == "民法第259條"                  # law 欄位作為 art 後備
    assert v["law_name"] == ""


def test_ref_law_with_structure_suffix():
    h = _law_hit(text="一、返還之。\n二、利息。")
    r = rag._ref(h)
    assert r.startswith("[法條:民法 第259條")
    assert "1項2款" in r  # summarize 摘要帶上


def test_ref_law_single_paragraph_no_suffix():
    h = _law_hit(text="唯一一段。")
    r = rag._ref(h)
    assert not r.rstrip("]").endswith("｜")   # 無 ｜ 尾巴
    assert "[法條:民法" in r


def test_ref_case():
    p = {"case_no": "案號123", "law": "刑法", "law_name": "", "text": "x"}
    r = rag._ref({"payload": p})
    assert r == "[案號:案號123 法條:刑法]"


def test_ref_chapter_included():
    h = _law_hit(payload_extra={"chapter": "第二章"}, text="一段。")
    assert "（第二章）" in rag._ref(h)


def test_structure_integration_with_real_law():
    # 可見的已知案例：多款組合必須被計成「N項M款」
    s = law_struct.structure("一、給付物返還。\n二、利息附加。\n（三）使用代價。")
    assert s["para"] == 1 and len(s["items"]) == 3