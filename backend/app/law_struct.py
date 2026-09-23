"""法條結構解析：把條文 text 拆出「款/項」，供引用標示與回答時列舉結構。"""
import re

# 款開頭：一、／（一）／1.／1、
_ITEM_RE = re.compile(r"^\s*(?P<label>[一二三四五六七八九十百零]+、|（[一二三四五六七八九十百零]+）|\d+[\.、])\s*(?P<content>.*)$")
_CJK = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
        "七": 7, "八": 8, "九": 9, "十": 10}


def structure(text: str) -> dict:
    """粗略解析條文：項＝非款開頭的行數，款＝以「一、」等開頭的行。"""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    items, paras = [], []
    for ln in lines:
        m = _ITEM_RE.match(ln)
        if m:
            items.append((m.group("label").strip(), m.group("content").strip()))
        else:
            paras.append(ln)
    para = len(paras) or (1 if lines else 0)
    return {"para": para, "items": items}


def summarize(text: str) -> str:
    """結構摘要（例：「1項6款」）；單段無款回空字串。"""
    s = structure(text)
    if s["para"] <= 1 and not s["items"]:
        return ""
    return f"{s['para']}項{len(s['items'])}款"


def _label_no(label: str) -> str:
    """款標籤正規化成中文序數，例「一、」→「一」、「（三）」→「三」。"""
    s = re.sub(r"[（）.、)]", "", label).strip()
    return s or ""


def cite_item(text: str) -> str:
    """cite 標示：若 hit 開頭是款則回「第X款」，否則依段落回「全文」/「第N項」。"""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    if not lines:
        return "全文"
    m = _ITEM_RE.match(lines[0])
    if m:
        return f"第{_label_no(m.group('label'))}款"
    return "全文" if len(lines) <= 1 else "第1項"