"""廢止／中止條文：可以被查到，但必須明確標示（2026-10-05）。

## 為什麼反轉舊設計

舊版在 **ingest**（`qdrant_load.build_points`）與 **檢索**（`retrieve._BASE_FILTER`）
兩處都排除 `is_repealed` / `is_abandoned`。實測後果（證券交易法）：

    229 條 → qdrant 只有 209 條，少了 20 條全是「（刪除）」

而且**沒有任何徵兆**：使用者問「證券交易法第9條」得不到回答，看不出是
「這條已刪除」還是「系統沒收錄這部法」。後兩者都是錯的 —— 正確答案是
「第9條已刪除，無現行條文」。

更糟的是舊版 payload 把 `is_repealed` **寫死成 False**（因為只灌未廢止條文，
寫死也「看起來對」）。現在真的灌進去了，寫死會讓檢索過濾失效。

## 本組測試釘住三件事

1. ingest **不排除**廢止條文，且 payload 帶**真實**旗標值
2. 檢索層**不排除**廢止條文（可被查到）
3. 回答與前端**明確標示**已廢止，且 `_decide` 不給 high 信心
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingest" / "laws"))

from app import rag, retrieve, law_meta  # noqa: E402


def _flat(article_no="第 9 條", content="（刪除）", repealed=True, abandoned=False):
    return [{
        "pcode": "G0400001", "law_name": "證券交易法", "law_category": "行政",
        "article_seq": 9, "article_no": article_no, "chapter": "第一章",
        "article_content": content, "char_len": len(content),
        "is_repealed": repealed, "is_abandoned": abandoned,
    }]


# ── 1. ingest 不排除，且旗標是真實值 ─────────────────────────────────────

def test_ingest_keeps_repealed_articles() -> None:
    """廢止條文必須進 qdrant —— 三層內容一致是最高標準。"""
    import qdrant_load
    pts = qdrant_load.build_points(_flat(), limit=10)
    assert len(pts) == 1, f"廢止條文被丟掉了：{pts}"


def test_ingest_payload_carries_the_real_flag_not_hardcoded_false() -> None:
    """payload 的 is_repealed 必須是**真值**，不能寫死 False。

    寫死會讓檢索層的過濾失效，而且是靜默的 —— 而且讓 qdrant 與
    parquet／pg 不一致（那兩層是真值）。
    """
    import qdrant_load
    pts = qdrant_load.build_points(_flat(repealed=True), limit=10)
    assert pts[0]["payload"]["is_repealed"] is True, pts[0]["payload"]

    pts2 = qdrant_load.build_points(_flat(repealed=False, content="現行條文"), limit=10)
    assert pts2[0]["payload"]["is_repealed"] is False, pts2[0]["payload"]


def test_ingest_keeps_abandoned_articles() -> None:
    """已中止施行也一樣要留下（旗標不同，處理相同）。"""
    import qdrant_load
    pts = qdrant_load.build_points(
        _flat(article_no="第 1 條", content="本法中止施行", repealed=False, abandoned=True),
        limit=10)
    assert len(pts) == 1
    assert pts[0]["payload"]["is_abandoned"] is True


# ── 2. 檢索層不排除 ────────────────────────────────────────────────────

def test_search_does_not_filter_out_repealed_articles() -> None:
    """檢索層不可再過濾 is_repealed / is_abandoned。

    空過濾器是可以的（Qdrant 接受 `{"must": []}` ＝ 不過濾），
    但**不能**有那兩個條目 —— 有的話廢止條文永遠查不到。
    """
    conds = retrieve._BASE_CONDITIONS
    keys = {c.get("key") for c in conds if isinstance(c, dict)}
    assert "is_repealed" not in keys, f"檢索層仍在排除廢止條文：{conds}"
    assert "is_abandoned" not in keys, f"檢索層仍在排除中止條文：{conds}"


# ── 3. 回答與前端明確標示 ────────────────────────────────────────────────

def test_decide_never_returns_high_when_all_hits_are_repealed() -> None:
    """全部命中都是廢止條文時，不該給 high 信心。

    那是「查不到現行規定」的最後一道閘門。給 high 會讓一個沒有答案的
    問題看起來像「有答案、而且很確定」—— 在法律情境下這最危險。
    """
    hits = [{"payload": {"law_name": "民法", "article_no": "第 9 條",
                         "is_repealed": True, "is_abandoned": False}}]
    level, reason = retrieve._decide("民法第9條", hits, 0.95)
    assert level != "high", (level, reason)
    assert "repealed" in reason, reason


def test_decide_still_high_when_a_live_article_is_among_hits() -> None:
    """有現行條文在命中之內時，維持原本的判斷（不要誤傷）。"""
    hits = [
        {"payload": {"law_name": "民法", "article_no": "第 9 條",
                     "is_repealed": True, "is_abandoned": False}},
        {"payload": {"law_name": "民法", "article_no": "第 259 條",
                     "is_repealed": False, "is_abandoned": False}},
    ]
    level, _ = retrieve._decide("民法第259條", hits, 0.95)
    assert level == "high", level


def test_hit_view_exposes_repealed_flag_to_the_frontend() -> None:
    """_hit_view 要把旗標透出去，否則前端無法標示。"""
    v = rag._hit_view({"id": 1, "score": 1.0,
                       "payload": {"law_name": "證券交易法", "article_no": "第 9 條",
                                   "pcode": "G0400001", "text": "（刪除）",
                                   "is_repealed": True, "is_abandoned": False}})
    assert v["repealed"] is True
    assert v["abandoned"] is False

    v2 = rag._hit_view({"id": 2, "score": 1.0,
                        "payload": {"law_name": "證券交易法", "article_no": "第 1 條",
                                    "pcode": "G0400001", "text": "現行條文",
                                    "is_repealed": False, "is_abandoned": False}})
    assert v2["repealed"] is False


@pytest.mark.asyncio
async def test_exact_repealed_article_says_it_is_repealed(monkeypatch):
    """條號精準命中一條已刪除條文時，回答要說明「已刪除」。

    ⚠️ 不可只把「（刪除）」當答案回 —— 那四個字讀者完全看不懂。
    使用者問「第9條怎麼規定」，正確回答是「第9條已刪除，無現行條文」。
    """
    law_meta._LAW_NAMES = ["證券交易法"]

    async def fe(t):
        return [[0.0] * 16]
    async def fs(q, v, limit):
        return [{"id": 1, "score": 0.5, "_exact_rank": True,
                 "payload": {"pcode": "G0400001", "law_name": "證券交易法",
                             "article_no": "第 9 條", "chapter": "第一章",
                             "text": "（刪除）", "char_len": 4,
                             "is_repealed": True, "is_abandoned": False}}]
    async def fd(v, limit):
        return {1: 0.9}
    def fr(q, h, d, topk):
        return h[:topk]
    async def fprobe():
        return {}
    async def never(*a, **k):
        raise AssertionError("廢止條文不該呼叫 LLM")

    monkeypatch.setattr(rag.gateway, "embed", fe)
    monkeypatch.setattr(retrieve, "search", fs)
    monkeypatch.setattr(retrieve, "_dense_leg", fd)
    monkeypatch.setattr(retrieve, "rerank", fr)
    monkeypatch.setattr(rag.gateway, "_host_probe_log", fprobe)
    monkeypatch.setattr(rag, "generate", never)

    try:
        r = await rag.answer("證券交易法第9條")
    finally:
        law_meta._LAW_NAMES = []

    assert "刪除" in r["answer"], r["answer"]
    assert "無現行條文" in r["answer"], r["answer"]
    assert r["confidence"] == "rule"
    assert r["no_match"] is not True, "廢止條文應可查到（只是已刪除），不是查不到"


def test_frontend_marks_repealed_citations() -> None:
    """前端樣板要有廢止徽章 —— 否則「（刪除）」會被當成現行條文。"""
    page = (ROOT / "frontend" / "src" / "routes" / "+page.svelte").read_text(encoding="utf-8")
    assert "h.repealed" in page, "引用清單沒有廢止旗標的處理"
    assert "已刪除" in page
    assert "已中止" in page