#!/usr/bin/env python3
"""引用候選偵測與狀態分類（spec 004 T013 / T014 / FR-034…FR-039）。

# ⚠️ 這個模組**刻意沒有** statute resolution

這段話放在最前面，因為它是最容易被後人「補上」的東西，而補上之後會造成
實質的傷害。

**本模組不會、也不該被擴充成能把引用解析成法條身分的模組。** 理由是實測：

* 抽樣中 5,310 個 `第X條` 引用裡，**610 個（11%）無法解析**。
* repo 自己的 `laws_meta.jsonl`（1,347 個法條名稱）只覆蓋觀察到的 1,629 個
  候選名稱中的 **63 個（3.9%）**，而且它**過時**：corpus 最常被引用的
  `犯詐欺犯罪危害防制條例` 不在其中，連 `刑法` 都不在（只有 `中華民國刑法`）。

若在這裡加一個「用 laws_meta 查名字」的函式，96% 的查詢會落空，而落空的
那 96% 會怎麼被處理？兩個選項都很糟：靜默回傳 `None`（使用者以為沒有
引用），或回傳一個猜的名字（**那就是偽造司法內容**，FR-002 / FR-037 禁止）。

**Stage B（resolution）是刻意延後的，不是遺漏的。** 見 spec FR-B05。

## Detection ≠ Resolution

```
JFULL
  ↓
候選（形狀符合結構 pattern）
  ↓
分類標籤（candidate | statute-citation | ambiguous | non-statute）
```

每一層都只是**對文字形狀的觀察**。任何一層都不會告訴你「這引用的是哪部
法律」—— 那需要一個我們沒有的 oracle。

## 分類標籤為什麼預設是 `candidate`

FR-039 規定預設狀態是 `candidate`。若預設是 `statute-citation`，那每一個
`系爭契約第一條`（抽樣中 87 次）都會被記成法律引用。

`statute-citation` 這個標籤**可以**被標上，但只能由**看見了完整法條名稱**
的呼叫端標 —— 而那需要呼叫端自己有那份名單。本模組提供 `classify()` 做純
文字形狀的判斷，並且**永遠不主動**回傳 `statute-citation`。

## offset 是字元 offset，指向 authoritative text

`start_offset` / `end_offset` 是**字元**（Python code point）索引，直接對應
`LosslessText.text`。不是位元組 offset，不是行號。

FR-036 規定偵測不得改變 offset。實作的保證方式是**根本不做任何轉換** ——
regex 直接跑在 `LosslessText.text` 上，沒有 strip、沒有換行正規化、沒有
Unicode 正規化。這比「轉換之後把 offset 轉回去」可靠得多：後者需要一個
雙向映射，而那個映射本身就是 bug 的來源。

### 為什麼不做「先 strip 再找位置」

那個做法在 `str.strip()` 上會安靜地錯開：原文開頭若有空白，所有 offset 都
會少掉那些字元數，而結果文字看起來完全正常。

`tests/test_judgement_citations.py::test_offsets_survive_leading_whitespace`
專門驗這個 —— fixture 的 `JFULL` 開頭就是 `　　`，任何 strip 都會讓 offset
錯位，而 substring invariant 會**同時**失敗（因為那其實是個好事：invariant
抓住了它）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

import citation_patterns as _patterns
from document import JudgmentDocument
from text import LosslessText


class Classification(str, Enum):
    """候選的分類狀態（FR-039）。

    繼承 `str` 讓它可以直接序列化成 JSON 而不需要自訂 encoder。
    """

    CANDIDATE = "candidate"                # 預設；只知道「長得像引用」
    STATUTE_CITATION = "statute-citation"  # 看見了完整法條名稱
    AMBIGUOUS = "ambiguous"                # 有線索但不足以判定
    NON_STATUTE = "non-statute"            # 確認是文件內部引用


# 從 classification 抽出的不可避免的對應：Python 的 Enum 無法直接把
# `statute-citation` 當成 Python 標識符，所以列出來。這是**呈現層**的
# 對應，不是規則。
_ALL = tuple(c.value for c in Classification)


@dataclass(frozen=True)
class CitationCandidate:
    """一個引用候選。

    ## 這個物件上**沒有** statute_name 欄位

    這是刻意的，不是遺漏。FR-037 規定偵測階段 MUST NOT assign a statute
    identity。若這裡有一個 `statute_name: str | None` 欄位，有人會填它，
    而那個值只能來自猜測。

    分類標籤（`classification`）裡的 `statute-citation` 是**形狀觀察**，
    由看見完整名稱的呼叫端設定 —— 不是這個模組推斷出來的。
    """

    source_document: str      # document identity（entry_path）
    jid: str                  # opaque JID，與來源一致
    matched_text: str         # == authoritative_text[start:end]（FR-035）
    start_offset: int         # 字元 offset，指向 authoritative text
    end_offset: int
    pattern_name: str         # 哪個結構 pattern 命中（FR-034 的 evidence）
    classification: Classification = Classification.CANDIDATE
    # evidence：為什麼是這個分類。刻意是一段**可讀的事實描述**，不是一個
    # 信心分數 —— 後者會被當成可以調高的參數。
    evidence: tuple[str, ...] = ()
    # provenance：追溯回 archive 的鏈
    provenance: dict = field(default_factory=dict)

    @property
    def length(self) -> int:
        return self.end_offset - self.start_offset

    @property
    def statute_name(self) -> None:
        """恆為 `None` —— 這個模組不做 resolution。

        提供這個 property 是為了讓「取得法條名稱」這件事有一個明確的
        回傳值，而不是 `AttributeError`。呼叫端若寫
        `candidate.statute_name` 會得到 `None`，並且**看得到**那是什麼。

        ⚠️ 若日後有人把它改成回傳真實名字，這就是偽造的起點。
        `tests/test_judgement_citations.py::test_statute_name_is_always_none`
        會紅。
        """
        return None

    @property
    def article_number(self) -> None:
        """恆為 `None` —— 同 `statute_name`。

        刻意不從 `matched_text` 解析出條號。`第44條` 的「44」在孤立存在時
        沒有確定意義（要看是哪部法律），而把字面數字當成 `article_number`
        會讓下游以為那是一個已確認的條號。
        """
        return None

    def as_dict(self) -> dict:
        return {
            "source_document": self.source_document,
            "jid": self.jid,
            "matched_text": self.matched_text,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "pattern_name": self.pattern_name,
            "classification": self.classification.value,
            "evidence": list(self.evidence),
            "provenance": dict(self.provenance),
        }


class SubstringInvariantError(AssertionError):
    """`matched_text != text[start:end]` —— FR-035 被破壞。

    這是 `AssertionError` 的子類，因為它**不該**發生：偵測器是唯一寫出
    offset 的地方，而它直接從 `match.start()`/`match.end()` 取值。若這個
    invariant 破了，那是程式 bug，不是資料問題 —— 所以要讓它炸。

    為什麼在函式**內部**斷言（FR-034 acceptance 明確要求）：只靠測試驗這個
    性質，等於假設「測試涵蓋了所有輸入」。內部斷言的成本是一次字串比較，
    而它保證**任何**呼叫路徑都受保護。
    """


def _check_substring(text: str, cand: CitationCandidate) -> None:
    actual = text[cand.start_offset : cand.end_offset]
    if actual != cand.matched_text:
        raise SubstringInvariantError(
            f"FR-035 被破壞：text[{cand.start_offset}:{cand.end_offset}] "
            f"= {actual!r}，但 matched_text = {cand.matched_text!r}"
            f"（{cand.source_document}）"
        )


def detect(
    text: LosslessText | str,
    *,
    document: JudgmentDocument | None = None,
    entry_path: str = "",
    jid: str = "",
    provenance: dict | None = None,
) -> list[CitationCandidate]:
    """在 authoritative text 中偵測引用候選。

    ## 參數

    * `text` —— `LosslessText` 或 str。給 str 會先包成 `LosslessText`，
      **不做任何清理**。
    * `document` —— 若提供，`entry_path` / `jid` 會從它取。兩者都提供時，
      `document` 優先。
    * `provenance` —— 附加的追溯資訊（artifact sha256 等）。

    ## 保證

    1. 每個 candidate 滿足 `matched_text == text[start:end]`（內部斷言）。
    2. offset 是**字元** offset，指向傳入的那份 text，未做任何轉換。
    3. 沒有任何欄位帶法條名稱。
    4. **零候選是合法結果** —— 回傳空 list，不拋錯。
    """
    t = text if isinstance(text, LosslessText) else LosslessText.from_string(text)
    s = t.text

    if document is not None:
        entry_path = document.entry_path
        jid = document.jid

    prov: dict = dict(provenance or {})

    found: list[CitationCandidate] = []
    claimed: list[tuple[int, int]] = []

    for pattern in _patterns.PATTERNS:
        for m in pattern.regex.finditer(s):
            span = (m.start(), m.end())
            # 重疊抑制：較長的 pattern 先跑，先到先得。
            # 沒有這一條，`第44條第2項` 會同時被 article_paragraph 與
            # article 命中，產生一個重複的子字串。
            if any(s < span[1] and span[0] < e for s, e in claimed):
                continue
            claimed.append(span)

            cand = CitationCandidate(
                source_document=entry_path,
                jid=jid,
                matched_text=m.group(0),
                start_offset=span[0],
                end_offset=span[1],
                pattern_name=pattern.name,
                provenance=prov,
            )
            # FR-035：內部斷言，不是只在測試裡驗
            _check_substring(s, cand)
            found.append(cand)

    found.sort(key=lambda c: (c.start_offset, c.end_offset))
    return found


# ── T014：分類 ──────────────────────────────────────────────────────────────

# 文件內部引用的標記詞。
#
# 這些**不是**「不是法律的詞彙表」，而是觀察到出現在文件內部引用裡的字樣
# （`citation-pattern-survey.md` §5 的 false positive 表：`系爭契約第一條`、
# `原審卷第371頁`）。標成 `non-statute` 的意思是「這個候選指向的是本文
# 內的另一份文件，不是法律」—— 一個**形狀觀察**，不是法律判斷。
_DOC_INTERNAL_MARKERS: tuple[str, ...] = (
    "契約",
    "判決",
    "裁定",
    "原審",
    "卷",
    "書記官",
    "訴狀",
)

# 相對引用的標記詞（`同法第X條`、`準用同第X條`）。
#
# 這些是**ambiguous** 而非 resolved：它們確實指向某部法律，但那是**前面**
# 提到的某部，而「前面是哪一部」需要跨段落狀態 —— 那屬於 Stage B。
_ELIDED_MARKERS: tuple[str, ...] = (
    "同法",
    "準用同",
    "同條",
)


def classify(candidate: CitationCandidate, text: LosslessText | str) -> CitationCandidate:
    """對一個候選做分類，回傳**新的**（frozen）候選。

    這是純文字形狀的判斷，**不做**任何法條身分解析。

    ## 分類規則（全部可從文字直接看出）

    | 條件 | 結果 |
    |---|---|
    | matched_text 前方緊鄰處出現文件標記詞（`契約`…） | `non-statute` |
    | matched_text 前方緊鄰處出現相對引用標記（`同法`…） | `ambiguous` |
    | 其餘 | `candidate`（預設） |

    ## 永遠不會回傳 `statute-citation`

    那是刻意的。`statute-citation` 意味著「確認這是某部法律的引用」，
    而確認需要知道那個名字確實是一部法律 —— 需要 oracle，而 oracle 不可得
    （survey §4.3：本地 gazetteer 覆蓋率 3.9%，且缺最常被引用的那一部）。

    呼叫端若**自己**有可信名單，可以自行設定該標籤；那個判斷屬於呼叫端，
    不屬於這個模組。

    ## 看的窗口有多大

    只看 matched_text **之前**的一小段（`_CONTEXT_WINDOW` 個字元）。這是
    survey §4.1 記錄的規則 1（名稱緊鄰）的保守版本。窗口大小的選擇有
    取捨：太大會把無關的前文納進來（產生假 `non-statute`），太小會漏掉
    有前導修飾詞的引用。
    """
    t = text if isinstance(text, LosslessText) else LosslessText.from_string(text)
    s = t.text

    # 只看 match 之前的內容 —— 不含 matched_text 本身
    before = s[max(0, candidate.start_offset - _CONTEXT_WINDOW) : candidate.start_offset]
    evidence: list[str] = []

    # 先查文件內部標記：它們更明確（「這指的是契約，不是法律」）
    for marker in _DOC_INTERNAL_MARKERS:
        if before.endswith(marker):
            evidence.append(f"緊鄰前文以「{marker}」結尾 → 指向文件內部")
            return _replace(candidate, Classification.NON_STATUTE, evidence)

    for marker in _ELIDED_MARKERS:
        if before.endswith(marker):
            evidence.append(
                f"緊鄰前文以「{marker}」結尾 → 相對引用，"
                "所指之法條未在此出現"
            )
            return _replace(candidate, Classification.AMBIGUOUS, evidence)

    evidence.append("無足夠線索判定法條身分 → 保持候選狀態")
    return _replace(candidate, Classification.CANDIDATE, evidence)


# 相對引用的上下文窗口。這個數字是**實測**的折衷：
# survey §4.1 說 1,057/5,310（19%）的引用在前 30 個字元內沒有法條名稱，
# 而其中 128 例是跨 CRLF 的。窗口必須夠大才能看到名字，但太大會把無關
# 前文納進來。30 與 survey 的規則 1 一致。
_CONTEXT_WINDOW = 30


def _replace(
    candidate: CitationCandidate,
    classification: Classification,
    evidence: Iterable[str],
) -> CitationCandidate:
    """回傳帶新分類的候選（frozen dataclass 不可變更欄位）。"""
    return CitationCandidate(
        source_document=candidate.source_document,
        jid=candidate.jid,
        matched_text=candidate.matched_text,
        start_offset=candidate.start_offset,
        end_offset=candidate.end_offset,
        pattern_name=candidate.pattern_name,
        classification=classification,
        evidence=tuple(evidence),
        provenance=candidate.provenance,
    )


def detect_and_classify(
    text: LosslessText | str,
    *,
    document: JudgmentDocument | None = None,
    entry_path: str = "",
    jid: str = "",
    provenance: dict | None = None,
) -> list[CitationCandidate]:
    """偵測 + 分類的便利入口。

    分類是對每個候選**各自**判斷，不做跨候選的狀態追蹤 —— 段落層級的
    「目前法律」推播屬於 Stage B。
    """
    t = text if isinstance(text, LosslessText) else LosslessText.from_string(text)
    candidates = detect(
        t, document=document, entry_path=entry_path, jid=jid, provenance=provenance
    )
    return [classify(c, t) for c in candidates]
