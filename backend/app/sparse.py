#!/usr/bin/env python3
"""中文法規 sparse tokenizer（純 stdlib；qdrant_load 與 backend 查詢共用）。

策略：不共享 idf 檔——Qdrant sparse 用 `modifier:"idf"`，查詢時由 collection 統計
自動加 global IDF；這裡只算「條號/專有詞」的 TF 權重：
- 條號 → 單一 token（第X條），權重 ×3（精確命中加分）
- 拉丁/數字串 → 原樣 token（小寫）
- CJK → 2-gram token
- 權重 = min(tf,4)；稀疏 index = md5(token) 前 8 bytes（u64，collision 可忽略）
"""
import hashlib
import re

_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LATIN_RUN = re.compile(r"[a-z0-9]+", re.I)
_ART_NO = re.compile(r"第\s*([\d一二三四五六七八九十百千]+)[之0-9-／/]*\s*條")


def token_index(token: str) -> int:
    """Qdrant sparse indices 只收 u32（≤2^32-1），用 md5 前 4 bytes。"""
    return int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:4], "big")


def tokens(text: str) -> list[str]:
    """回傳帶重複的 token 序列（含整條內的 2-gram）。"""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        m = _ART_NO.match(text, i)
        if m:
            tok = re.sub(r"\s+", "", m.group(0))
            out.append(tok)
            out.append("TERM" + m.group(1))  # 純數字條號（如 259）也給獨立 token
            i = m.end()
            continue
        m = _LATIN_RUN.match(text, i)
        if m:
            out.append(m.group(0).lower())
            i = m.end()
            continue
        c = text[i]
        if _CJK_RUN.match(c):
            j = i + 1
            while j < n and _CJK_RUN.match(text[j]):
                j += 1
            run = text[i:j]
            if len(run) >= 2:
                for k in range(len(run) - 1):
                    out.append(run[k:k + 2])
            else:
                out.append(run)
            i = j
            continue
        i += 1
    return out


def sparse_vector(text: str, base: int = 1, art_boost: int = 3) -> dict:
    """doc 或 query 的稀疏向量：{indices:[u64], values:[float]}。"""
    tf: dict[str, int] = {}
    for tok in tokens(text):
        tf[tok] = tf.get(tok, 0) + 1
    indices: list[int] = []
    values: list[float] = []
    for tok, c in tf.items():
        w = min(c, 4) * base
        if tok.startswith("第") and tok.endswith("條"):
            w += art_boost
        indices.append(token_index(tok))
        values.append(float(w))
    return {"indices": indices, "values": values}