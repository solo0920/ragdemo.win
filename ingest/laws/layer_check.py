"""三層條文稽核：parquet／PostgreSQL／Qdrant 的純比對邏輯。

FR-007／FR-020（spec 002）：可執行的格式檢查，單次執行能指出「哪一條、
哪一層、差在哪」。抓取層（fetch_*）與比對層（compare）分離：測試只測
比對（fake layers，不碰外部服務——沿用 test_three_layer_consistency 的
convention）；真正連線由 scripts/check-law-layers.py 做。
"""
from __future__ import annotations


def norm(text: str | None) -> str:
    """比對用正規化：壓掉一切空白（全形含在內）。只用於「內容相同」判定，
    不改任何存入值，也不用於顯示（顯示一律用原文）。"""
    return "".join(str(text or "").split())


def paras(text: str | None) -> int:
    """以換行計的段數（FR-002 的項分隔即換行）。空字串計 0 段。"""
    s = text or ""
    return 0 if not s else s.count("\n") + 1


def compare(law_name: str, article_no: str,
            layers: dict[str, str | None]) -> dict:
    """比一條三層。layers: {"parquet": text|None, "postgres": ..., "qdrant": ...}，
    None＝該層缺失。回傳 verdict ok / mismatch / missing 任一。"""
    present = {k: v for k, v in layers.items() if v is not None}
    if not present:
        return {"law": law_name, "article": article_no, "verdict": "missing",
                "detail": "三層皆無此條"}
    missing = sorted(k for k in ("parquet", "postgres", "qdrant")
                     if layers.get(k) is None)
    if missing:
        return {"law": law_name, "article": article_no, "verdict": "missing",
                "detail": f"缺失層：{','.join(missing)}"}
    norms = {k: norm(v) for k, v in present.items()}
    if len(set(norms.values())) != 1:
        segs = {k: paras(v) for k, v in present.items()}
        return {"law": law_name, "article": article_no, "verdict": "mismatch",
                "detail": f"內容不一致（段數 parquet/postgres/qdrant="
                          f"{segs['parquet']}/{segs['postgres']}/{segs['qdrant']}）"}
    return {"law": law_name, "article": article_no, "verdict": "ok",
            "detail": f"三層一致（{paras(next(iter(present.values())))} 段）"}
