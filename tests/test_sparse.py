"""sparse tokenizer / 稀疏向量（純函式，Qdrant u32 限制）。"""
from app.common import sparse as S

MAX_U32 = 1 << 32


def test_token_index_within_u32():
    assert isinstance(S.token_index("第259條"), int)
    assert all(0 <= S.token_index(t) < MAX_U32 for t in ["第259條", "違約金", "a1", "TERM259"])


def test_tokens_article_number_special_token():
    toks = S.tokens("第259條違約金")
    assert "第259條" in toks
    assert "TERM259" in toks  # 純數字條號專用 token
    assert "違約" in toks and "約金" in toks


def test_tokens_latin_lowercased():
    toks = S.tokens("A123b 罰款")
    assert "a123b" in toks
    assert all("A123b" not in t for t in toks)


def test_tokens_cjk_bigram_and_single_run():
    assert S.tokens("民") == ["民"]  # 單 CJK 不成 bigram
    toks = S.tokens("民法")
    assert toks.count("民法") >= 1


def test_sparse_vector_shape_and_boost():
    v = S.sparse_vector("第259條 第259條 返還")
    assert len(v["indices"]) == len(v["values"])
    assert all(0 <= i < MAX_U32 for i in v["indices"])
    assert all(val > 0 for val in v["values"])
    # 條號 token 加權（×3）；出現兩次權重 min(tf,4)=2 → 2+3=5
    idx = S.token_index("第259條")
    assert v["values"][v["indices"].index(idx)] == 5.0


def test_sparse_vector_empty():
    assert S.sparse_vector("") == {"indices": [], "values": []}


def test_sparse_true_and_identity():
    a = S.sparse_vector("契約解除後回復原狀義務")
    assert a == S.sparse_vector("契約解除後回復原狀義務")  # 確定性