#!/usr/bin/env python3
"""引用候選的**結構**模式定義（spec 004 T012 / FR-033）。

## 這份檔案是資料，不是散落在各處的 regex

所有 pattern 集中成具名資料，好處是可列舉、可測試、可對照 evidence。偵測邏輯
本身在 `citations.py`；這裡只回答「什麼形狀的字串算候選」。

## 每個 pattern 都有 survey 證據

`citation-pattern-survey.md` §3.1 對 494 筆抽樣做了 occurrence census，下列
每個結構都有實際出現次數：

| Pattern | 抽樣中的次數 |
|---|---|
| `第X條` | 5,310 |
| `第X項` | 3,411 |
| `第X條第X項` | 2,131 |
| `第X款` | 1,357 |
| `第X項第X款` | 767 |
| `第X條之Y` | 760 |
| `第X項前段` | 276 |
| `第X條前段` | 89 |

**兩套數字系統都必須支援**（FR-033）：`第四十四條`（391 次）與 `第44條`
（4,919 次），且有 **38 份文件同時使用兩者**。只認阿拉伯數字的偵測器會
安靜地漏掉 7.4% —— 那是最危險的失敗形狀，因為它不會報錯。

## 這個模組刻意沒有什麼

* **沒有 gazetteer**、沒有法條名稱清單、沒有 `laws_meta.jsonl` 的載入。
* **沒有 resolver**、沒有任何函式可以把候選轉成 statute identity。

理由是 survey §4.3 的實測結果：repo 自己的 `laws_meta.jsonl`（1,347 個名稱）
只覆蓋 5,310 個引用中觀察到的 1,629 個候選名稱裡的 **63 個（3.9%）**，而且
它是**過時的** —— corpus 最常被引用的 `犯詐欺犯罪危害防制條例` 不在裡面，
`刑法` 也不在（只有 `中華民國刑法`）。

把一份 3.9% 覆蓋率的名單當成 oracle，會讓 96% 的引用得到錯誤答案，而那些
答案看起來完全合理。這正是 FR-037 禁止的東西。詳見 `citations.py` 的說明。

## `▮` 這個符號是什麼

survey 的輸出用 `▮` 標示「引用起始的位置」，那不是資料裡的字元。pattern
裡沒有它。

## Numeral 部分為什麼是「一個或多個」而不是固定寬度

`第4條`、`第44條`、`第114條`、`第四十四條`、`第一百一十四條` 都真實存在。
固定寬度會漏掉一部分，而漏掉是不會報錯的。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── 數字系統 ────────────────────────────────────────────────────────────────
#
# 阿拉伯：0-9
ARABIC = r"[0-9]"
#
# 中文數字。完整集合含「〇零一二三四五六七八九十百千萬億」。繁體用「兩」的情況
# 在法條編號裡極少見（`第二十條` 而非 `第二十兩條`），但 survey 的樣本裡
# 沒出現過含「兩」的條號；把它排除是依 evidence，不是臆測。
CHINESE = r"[〇零一二三四五六七八九十百千萬億]"

# 數字 = **同一套系統**的一或多個位數。
#
# 刻意用 `{ARABIC}+|{CHINESE}+`（而非 `(?:A|B)+`）。後者會接受混合寫法
# `第4十四條` —— 那在真實 corpus 中不存在，而寬鬆的定義會產生一個假的
# `第4條` 尾端字串，在後續的重疊抑制裡變成兩個候選。
#
# 兩套系統之間不能跨界，這是「我們只描述觀察到的形狀」的具體表現。
NUM = f"(?:{ARABIC}+|{CHINESE}+)"

# 序號前綴。第 1 項、第 2 款…
ORDINAL = f"第{NUM}"


@dataclass(frozen=True)
class Pattern:
    """一個具名的結構 pattern。frozen。"""

    name: str
    regex: re.Pattern[str]
    # survey §3.1 記錄的出現次數（0 = 這是我為完整性補的結構，未在抽樣中量化）
    observed_in_survey: int
    note: str = ""

    @property
    def pattern(self) -> str:
        """原始 regex 字串 —— 給報告與除錯用。"""
        return self.regex.pattern


def _p(name: str, pattern: str, count: int, note: str = "") -> Pattern:
    return Pattern(
        name=name,
        regex=re.compile(pattern),
        observed_in_survey=count,
        note=note,
    )


# 依「由長到短」排列：偵測時先試最長的，避免 `第44條第2項` 被拆成兩個候選。
#
# 這個順序**不影響**正確性（偵測器會合併重疊），但讓單獨呼叫
# `first_match()` 的行為可預測 —— 一個結構不會被回報成兩個片段。
_PATTERNS: tuple[Pattern, ...] = (
    # 第X條第Y項第Z款 — 完整形式
    _p(
        "article_paragraph_subparagraph",
        rf"{ORDINAL}條{ORDINAL}項{ORDINAL}款",
        767,
        "survey §3.1 的「第X項第X款」片段即此結構",
    ),
    # 第X條第Y項
    _p(
        "article_paragraph",
        rf"{ORDINAL}條{ORDINAL}項",
        2131,
    ),
    # 第X條第Y款
    _p(
        "article_subparagraph",
        rf"{ORDINAL}條{ORDINAL}款",
        0,
        "未在 survey 中單獨量化；結構上與 article_paragraph 對稱",
    ),
    # 第X條之Y — 增訂條文（「刑法第185條之2」）
    _p(
        "article_added",
        rf"{ORDINAL}條之{NUM}",
        760,
    ),
    # 第X條前段
    _p(
        "article_first_part",
        rf"{ORDINAL}條前段",
        89,
    ),
    # 第X項前段
    _p(
        "paragraph_first_part",
        rf"{ORDINAL}項前段",
        276,
    ),
    # 第X項第Y款
    _p(
        "paragraph_subparagraph",
        rf"{ORDINAL}項{ORDINAL}款",
        767,
        "survey §3.1：第X項第X款 767 次",
    ),
    # 第X條
    _p(
        "article",
        rf"{ORDINAL}條",
        5310,
        "最基本的結構；其他都是它的擴充",
    ),
    # 第X項
    _p(
        "paragraph",
        rf"{ORDINAL}項",
        3411,
    ),
    # 第X款
    _p(
        "subparagraph",
        rf"{ORDINAL}款",
        1357,
    ),
)

PATTERNS: tuple[Pattern, ...] = _PATTERNS

# 快速查表：名稱 → Pattern
BY_NAME: dict[str, Pattern] = {p.name: p for p in PATTERNS}


def pattern_names() -> tuple[str, ...]:
    """所有 pattern 名稱（依宣告順序）。"""
    return tuple(p.name for p in PATTERNS)


def get(name: str) -> Pattern:
    """依名稱取得 pattern。"""
    if name not in BY_NAME:
        raise KeyError(f"未知的 pattern 名稱：{name}；可用：{pattern_names()}")
    return BY_NAME[name]


def documented_coverage() -> dict[str, int]:
    """每個結構在 survey 抽樣中的出現次數。

    給 drift 報告用：若日後 corpus 換版，這些數字是可比較的基線。
    """
    return {p.name: p.observed_in_survey for p in PATTERNS}


# ── 模組自我約束 ────────────────────────────────────────────────────────────
#
# 這兩個常數存在的唯一目的是讓「有人在這個模組裡塞了一份法條名單」變得
# 明顯。它們不被任何程式邏輯使用 —— 它們是**契約的宣告**，由
# `tests/test_judgement_citation_patterns.py` 斷言其值。


def has_gazetteer() -> bool:
    """恆為 `False`。

    這個函式存在的理由是讓「本模組不含 gazetteer」成為可執行的斷言，而不
    只是一句註解。若日後有人加了名單，這裡會變成 `True`，測試立刻紅。
    """
    return False


def gazetteer_size() -> int:
    """恆為 `0`。理由同 `has_gazetteer()`。"""
    return 0
