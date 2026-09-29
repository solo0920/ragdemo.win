"""中文條號解析：從查詢抽出「條」的標準格式。

純函式、無相依。抽成獨立模組的理由：這是整條檢索鏈的入口常數 —— 抽出來的
「第 N 條」拿去和 Qdrant payload 的 article_no 做去空白完全比對（見
`retrieve._exact_match`），所以它的格式就是比對基準，改動等同換掉基準。
"""
import re

_CN_DIG = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn2num(s: str) -> int:
    """中文數字→int（支援至千位）：十一=11、二十三=23、一百零五=105、兩百=200。"""
    s = s.replace("兩", "二").replace("零", "")
    total = cur = 0
    for ch in s:
        if ch == "千":
            total += (cur or 1) * 1000; cur = 0
        elif ch == "百":
            total += (cur or 1) * 100; cur = 0
        elif ch == "十":
            total += (cur or 1) * 10; cur = 0
        elif ch in _CN_DIG:
            cur = cur * 10 + _CN_DIG[ch]
    return total + cur


_ART_RE = re.compile(
    r"第\s*(?:(?P<ab>[0-9]+(?:\s*-\s*[0-9]+)?)|(?P<cn>[一二三四五六七八九十百零兩]+(?:之[一二三四五六七八九十零兩]+)?))\s*條"
)


def _bigrams(s: str) -> set[str]:
    """字串的 CJK bigram 集合（去掉空白），用以比對法名與 query 的重疊。"""
    s = "".join(s.split())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()


def extract_article_no(question: str) -> str | None:
    """從查詢抽出「條」的標準格式（例:「第20條」→「第 20 條」、「第10條之1」→「第 10-1 條」）。"""
    m = _ART_RE.search(question)
    if not m:
        return None
    if m.group("cn"):
        raw = m.group("cn")
        if "之" in raw:
            head, tail = raw.split("之", 1)
            s = f"{_cn2num(head)}-{_cn2num(tail)}"
        else:
            s = str(_cn2num(raw))
    else:
        s = m.group("ab").strip()
    return f"第 {s.strip()} 條"
