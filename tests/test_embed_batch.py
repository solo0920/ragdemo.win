"""`chunk_by_chars`：依總字元數切 embed 批次，不是依筆數。

背景（2026-09-29 實測）：ollama 0.34.4 對**單次請求的總字元數**有上限，
超過回 400，但錯誤訊息是誤導性的：

    Post "http://127.0.0.1:55765/tokenize": dial tcp ...: connection refused

那個 port 是 ollama 自己 worker 的臨時埠，所以看起來像「連不上 ollama」，
實際是 ollama 收下請求後自己爆掉。實測（bge-m3）：

    256 筆 × 短文本（5,120 字元）      → 200
    320 筆 × 短文本（6,400 字元）      → 400
    256 筆 × 法規文本（18,537 字元）   → 200
    512 筆 × 法規文本（41,584 字元）   → 400
    1024 筆 × 短文本（20,480 字元）    → 400   ← 筆數多但每筆很短，照樣炸

所以限制是**總量**。原來的 `EMB_BATCH=256` 只是碰巧安全（法規文本平均
70 字元 → 1.8 萬字元，剛好沒超過），換個資料集就會炸，而症狀是整條
sync_daily 管線在最後一階段崩、前面幾萬條都白算。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest" / "laws"))
import qdrant_load as Q  # noqa: E402


def test_split_keeps_every_item_exactly_once():
    texts = [f"第{i}條 內容" * (i % 7 + 1) for i in range(500)]
    batches = Q.chunk_by_chars(texts)
    assert [t for b in batches for t in b] == texts, "不能漏、不能重、不能改順序"


def test_each_batch_respects_char_limit():
    texts = [f"內容{i}" * 50 for i in range(300)]   # 每筆 ~150 字元
    for b in Q.chunk_by_chars(texts, limit=1000):
        assert sum(len(t) for t in b) <= 1000, f"這批超限：{sum(len(t) for t in b)}"


def test_oversized_single_item_gets_its_own_batch():
    """單筆就超過 limit 時，它自己成一批 —— 不能因為它而讓後面全部塞不進去。"""
    big = "巨" * 5000
    batches = Q.chunk_by_chars([big, "短", "短"], limit=1000)
    assert batches[0] == [big], "超長單筆應該自己成一批"
    assert batches[1] == ["短", "短"], "後續項目不該被超長單筆擠掉"


def test_batch_count_is_driven_by_chars_not_count():
    """關鍵性質：同樣筆數、每筆字元多 40 倍 → 批次數也要多約 40 倍。

    這是「依字元切」與「依筆數切」的區別。若實作退回依筆數切，
    兩個都會是 1 批，比例是 1 倍 → 這個測試會失敗。
    （要用足夠大的基準，否則兩者都只有 1 批、比不出來。）
    """
    # 1,200 筆 × 3 字元 ≈ 3,600 字元 → 1 批
    few = ["短文"] * 1200
    # 1,200 筆 × 120 字元 = 144,000 字元 → 12 批
    many = ["長" * 120] * 1200
    assert len(Q.chunk_by_chars(many)) >= 10
    assert len(Q.chunk_by_chars(many)) > len(Q.chunk_by_chars(few)) * 5


def test_real_law_corpus_stays_under_limit():
    """用真的法規資料驗證：每批總字元都在 EMB_CHARS 內。"""
    flat = Path(__file__).resolve().parents[1] / "data" / "laws" / "laws_flat.jsonl"
    if not flat.exists():
        pytest.skip("data/laws/laws_flat.jsonl 不存在（跑過 sync_daily 才有）")
    import json
    texts = [
        (json.loads(line).get("article_content") or "")[:Q.MAX_DOC]
        for line in flat.read_text(encoding="utf-8").splitlines() if line
    ]
    batches = Q.chunk_by_chars(texts)
    assert len([t for b in batches for t in b]) == len(texts)
    over = [sum(len(t) for t in b) for b in batches if sum(len(t) for t in b) > Q.EMB_CHARS]
    assert not over, f"有 {len(over)} 批超過 EMB_CHARS={Q.EMB_CHARS}"


def test_char_limit_is_below_ollama_observed_threshold():
    """把實測門檻寫成常數，避免後人把它調大到會炸的值。"""
    # 實測 20,480 字元會炸、18,537 可過。EMB_CHARS 必須明顯低於上限。
    assert Q.EMB_CHARS < 18_537, (
        f"EMB_CHARS={Q.EMB_CHARS} 太接近實測門檻，"
        f"ollama 版本升級收緊時會直接炸")
    assert Q.EMB_CHARS >= 6_400, "太小會讓 4.7 萬筆變成幾千個批次，灌不進去"
