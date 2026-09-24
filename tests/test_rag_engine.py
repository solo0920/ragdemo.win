"""RAG 引擎：本地 rerank / 法律語意訊號 / 信心分級 / answer 閘門（低相關不問 LLM）。"""
import pytest

from app import rag


def _hit(iid, dense=None, exact=False):
    h = {"id": iid, "score": 0.5, "payload": {"law_name": "民法", "article_no": "第1條",
                                              "chapter": "", "text": "一段。"}}
    if exact:
        h["_exact_rank"] = True
    if dense is not None:
        h["_dense"] = dense
    return h


# ---------- 純函式 ----------

def test_rerank_exact_first_then_preserve_fusion_order():
    hits = [_hit(2, exact=True), _hit(3), _hit(1)]
    out = rag.rerank("q", hits, dense_scores={1: 0.9, 2: 0.4, 3: 0.7}, top_k=3)
    assert [h["id"] for h in out] == [2, 3, 1]        # exact 領先；其餘保持輸入（融合）順序
    assert out[0]["_dense"] == 0.4
    assert out[1]["_dense"] == 0.7
    assert out[2]["_dense"] == 0.9


def test_rerank_top_k_cut():
    hits = [_hit(1), _hit(2), _hit(3), _hit(4)]
    out = rag.rerank("q", hits, dense_scores={1: 0.2, 2: 0.9, 3: 0.5, 4: 0.8}, top_k=2)
    assert [h["id"] for h in out] == [1, 2]
    assert [h["_dense"] for h in out] == [0.2, 0.9]


def test_legal_signal():
    assert rag._legal_signal("民法第259條之1返還義務") is True   # 條號
    assert rag._legal_signal("契約解除後當事人義務") is True      # 法律語彙
    assert rag._legal_signal("勞工特別休假怎麼算") is True        # 勞工/特別休假
    assert rag._legal_signal("今天天氣如何？") is False
    assert rag._legal_signal("煮咖哩的步驟與食材") is False
    assert rag._legal_signal("幫我訂一張到東京的機票") is False


def test_decide_levels():
    # 無候選 → no_match
    assert rag._decide("x", [], 0.0)[0] == "no_match"
    # 太弱（< 0.58）一律 no_match
    assert rag._decide("契約解除", [_hit(1, dense=0.30)], 0.30)[0] == "no_match"
    # 中間區間（0.60）：無法律語意 → no_match；有 → medium
    assert rag._decide("今天天氣如何", [_hit(1, dense=0.60)], 0.60)[0] == "no_match"
    assert rag._decide("契約解除", [_hit(1, dense=0.60)], 0.60)[0] == "medium"
    # >= 0.70 → high
    assert rag._decide("民法第259條", [_hit(1, dense=0.75)], 0.75)[0] == "high"
    # dense 偏低但「條號精準命中」→ 不誤判 no_match
    h = _hit(1, dense=0.30, exact=True)
    h["payload"]["article_no"] = "第259條"
    assert rag._decide("民法第259條", [h], 0.30)[0] == "medium"
    h2 = _hit(2, dense=0.30, exact=True)
    h2["payload"]["article_no"] = "第271條"
    assert rag._decide("民法第259條", [h2], 0.30)[0] == "no_match"   # 精準命中但條號不符 → 仍擋


# ---------- answer() 閘門（monkeypatch 網路） ----------

async def _patch(monkeypatch, dense_scores):
    async def fe(t):
        return [[0.0] * 16]
    async def fs(q, v, limit):
        return [_hit(1, exact=True)]
    async def fd(v, limit):
        return dense_scores
    def fr(q, h, d, topk):
        out = h[:topk]
        for x in out:
            x["_dense"] = d.get(x["id"])
        return out
    async def fprobe():
        return {}
    monkeypatch.setattr(rag, "embed", fe)
    monkeypatch.setattr(rag, "search", fs)
    monkeypatch.setattr(rag, "_dense_leg", fd)
    monkeypatch.setattr(rag, "rerank", fr)
    monkeypatch.setattr(rag, "_host_probe_log", fprobe)
    return monkeypatch


@pytest.mark.asyncio
async def test_answer_no_match_skips_llm(monkeypatch):
    called = {}
    async def fake_generate(q, c, cautious=False):
        called["llm"] = True
        return "LLM 產出"
    monkeypatch = await _patch(monkeypatch, {1: 0.20})
    monkeypatch.setattr(rag, "generate", fake_generate)
    r = await rag.answer("今天天氣如何？")
    assert r["ok"] is True
    assert r["no_match"] is True
    assert r["confidence"] == "no_match"
    assert "LLM 產出" not in r["answer"]      # 未呼叫 LLM
    assert "llm" not in called                 # generate 從未被呼叫
    assert "沒有符合比對" in r["answer"]


@pytest.mark.asyncio
async def test_answer_high_calls_llm(monkeypatch):
    called = {}
    async def fake_generate(q, c, cautious=False):
        called["cautious"] = cautious
        return "正常答案"
    monkeypatch = await _patch(monkeypatch, {1: 0.75})
    monkeypatch.setattr(rag, "generate", fake_generate)
    r = await rag.answer("契約解除後回復原狀之義務？")
    assert r["no_match"] is not True
    assert r["confidence"] == "high"
    assert "正常答案" in r["answer"]
    assert called.get("cautious") is False     # high 不需警示附註


@pytest.mark.asyncio
async def test_answer_medium_cautious_flag(monkeypatch):
    called = {}
    async def fake_generate(q, c, cautious=False):
        called["cautious"] = cautious
        return "謹慎答案"
    monkeypatch = await _patch(monkeypatch, {1: 0.60})
    monkeypatch.setattr(rag, "generate", fake_generate)
    r = await rag.answer("契約解除後回復原狀之義務？")   # 有法律語意
    assert r["confidence"] == "medium"
    assert called.get("cautious") is True      # medium 帶警示附註
    assert "謹慎答案" in r["answer"]