#!/usr/bin/env python3
"""判決全文的 lossless 分塊（spec 004 T015 / FR-011, FR-012, FR-013）。

## 核心約束

> Chunk 是 authoritative JFULL 的 **derived representation**，不是新的 source
> of truth。它的每一個字元都能從原文切出來。

兩個必須同時成立的性質：

1. **切取正確** —— 對每個 chunk，`text == jfull[start_offset:end_offset]`。
2. **重組無損** —— 把所有 chunk 依序拼接，**逐 byte 等於**原文。

第 2 條比第 1 條更強：它保證「沒有任何字元被漏掉或重複」。實務上危險的不是
「chunk 內容錯了」（那是明顯的 bug），而是**某個字元被 chunk 邊界吞掉** ——
那會讓重組後的文本少一段，而每個 chunk 單獨看都完全正常。

## 分塊邊界怎麼選

目標是「可檢索的單位」，所以邊界優先落在**既有的文字斷點**上：換行之後、
section marker 之前。找不到接近的斷點時，退回硬切 —— 並且**記錄下來**
（`boundary_kind`）。

「記錄」是 tasks.md acceptance 的明確要求。理由：一次沒有落在自然邊界的硬切
是**資訊**，它告訴我們這份文件的結構沒被理解，而未來某個報告可以問「有多少
比例的 chunk 是硬切的」。把它藏起來等於丟掉這個訊號。

### Section marker 在這裡的角色

marker 只是**derived boundary signal** —— 讓切點落在語意段落之間。它**不是**
authoritative metadata，也不是 schema：

* 沒有 marker 的文件**照樣**能分塊（133/494 真實文件一個都沒有）。
* 不會因為缺少 marker 而拒絕整份文件。
* 不會補上 marker、不會修改原文。
* 不假設任何固定的段落順序或名稱。

若 marker 清單變了或消失，chunk 仍然產生，只是邊界品質下降 —— 而不會失敗。

## 為什麼不用 `\n` 當分隔

因為 `JFULL` 是 **CRLF**（493/494）。用 `"\n".split()` 會留下 5,310 個游離的
`\r`，然後任何「清理尾部 `\r`」的動作都會**改變字元數**，讓所有 offset 失效。

本模組直接用 `splitlines(keepends=True)` —— 它保留行尾資訊，且回傳的每個元素
都包含它自己的 `\r\n`。組合回去時不需要猜。

## 這個模組不做什麼

不產生 embedding、不摘要、不改寫、不呼叫任何模型（FR-012）。embedding 是
「定位原文的工具」，不是原文的替代品（FR-013），而那屬於 T017 之後。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from document import JudgmentDocument
from text import LosslessText

# 分塊的預設大小（字元）。
#
# 這個數字是**檢索單位**的選擇，不是對原文的判斷。它落在 700–800 之間是
# 因為判決的「主文」段落常在這個量級；它是可調參數，而無論設成多少，
# 重組性質都必須成立（由 `test_reconstruction_is_byte_exact` 對多組參數驗證）。
DEFAULT_CHUNK_SIZE = 750

# 分塊大小必須是這個值以上 —— 太小的 chunk 沒有意義，且會讓邊界數量爆炸。
# 唯一的例外是**最後一塊**：若剩下的尾巴比這個值短，它仍會成為一個 chunk
# （否則那個尾巴會遺失，而遺失違反重組性質）。
MIN_CHUNK_SIZE = 50


class BoundaryKind(str, Enum):
    """分塊邊界的來源。"""

    EXACT = "exact"              # 剛好在目標大小處有自然邊界
    NATURAL = "natural"          # 往回找到最近的自然邊界
    FORCED = "forced"            # 找不到自然邊界，強制切分
    SINGLE = "single"            # 整份文件只有一塊（比目標大小短）
    TAIL = "tail"                # 前面的塊都切完了，剩下的尾巴


@dataclass(frozen=True)
class Chunk:
    """一個 chunk。frozen。

    ## 欄位為什麼是這些

    * `text` —— chunk 的內容。保證 `== jfull[start_offset:end_offset]`。
    * `start_offset` / `end_offset` —— **字元** offset，指向 authoritative
      `JFULL`。不是位元組、不是行號。
    * `chunk_index` —— 0-based 序號，決定拼接順序。
    * `boundary_kind` —— 這次的切點品質（見上）。
    * `source_document` / `jid` —— document identity。
    * `sha256` —— chunk text 的 hash。用於增量比對（相同即跳過重新處理），
      與 `ingest/laws` 的 `content_hash` 同一個角色。

    **沒有** `embedding`、`score`、`summary`、`court`、`case_type` —— 前兩者
    屬於 T017 之後，後兩者會是 FR-016 禁止的推論。
    """

    text: str
    start_offset: int
    end_offset: int
    chunk_index: int
    boundary_kind: BoundaryKind
    source_document: str = ""
    jid: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        if not self.sha256:
            object.__setattr__(self, "sha256", self.compute_hash(self.text))

    @staticmethod
    def compute_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def length(self) -> int:
        return self.end_offset - self.start_offset

    @property
    def point_id(self) -> str:
        """Qdrant point ID 的**輸入**（deterministic string）。

        刻意回傳字串而非數字：T017 決定如何轉成 Qdrant 接受的型別，那是它
        的責任。在這裡轉換會讓 chunk.py 依賴 Qdrant 的 ID 規則。

        組成：`sha256(source_document|chunk_index)`。**不含**時間戳、不含
        亂數、不含行程 ID —— 同一份文件重跑必須得到同一個 ID。
        """
        basis = f"{self.source_document}|{self.chunk_index}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def verify_against(self, t: LosslessText | str) -> None:
        """確認 `t[start:end] == text`。不符就拋錯。

        由 `chunk_text()` 在函式**內部**對每個 chunk 呼叫 —— 只靠測試驗這個
        性質，等於假設測試涵蓋了所有輸入。
        """
        s = t.text if isinstance(t, LosslessText) else t
        actual = s[self.start_offset : self.end_offset]
        if actual != self.text:
            raise ChunkIntegrityError(
                f"chunk {self.chunk_index} 不符："
                f"text[{self.start_offset}:{self.end_offset}] = {actual!r} "
                f"但 chunk.text = {self.text!r}"
            )

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "chunk_index": self.chunk_index,
            "boundary_kind": self.boundary_kind.value,
            "source_document": self.source_document,
            "jid": self.jid,
            "sha256": self.sha256,
        }


class ChunkIntegrityError(AssertionError):
    """chunk 與 authoritative text 對不上 —— 重組性質被破壞。

    是 `AssertionError` 的子類，因為這是程式 bug（切分邏輯錯了），不是資料
    問題。切分正確時不可能發生。
    """


def _natural_boundaries(t: LosslessText) -> list[int]:
    """回傳所有自然邊界的 offset（升冪）。

    兩種邊界：

    * **換行之後** —— 每個 `\n` 的下一個位置
    * **section marker 之前** —— `observed_markers()` 找到的每個 marker 起始處

    刻意不在 marker **之後**切：marker 是段落的**標題**，切在標題之後會讓
    chunk 從標題開始，那對檢索沒幫助（使用者問的是內文）。

    全部用 `bisect` 的方式查，不做文字正規化。
    """
    s = t.text
    bounds: list[int] = []

    # 換行之後。逐字元掃描，用 `find` 而非 `splitlines` —— 這樣能拿到
    # **原始字串的 offset**，不需要事後重算。
    pos = s.find("\n")
    while pos != -1:
        bounds.append(pos + 1)
        pos = s.find("\n", pos + 1)

    # marker 之前。`observed_markers()` 在 `text.py`（T011），不是 `citations.py`
    # —— T012–T014 沒有改動它。
    from text import observed_markers

    for marker in observed_markers(t):
        start = 0
        while True:
            idx = s.find(marker, start)
            if idx == -1:
                break
            bounds.append(idx)
            start = idx + 1

    bounds.sort()
    # 去重（換行與 marker 起點可能重合）
    deduped: list[int] = []
    for b in bounds:
        if not deduped or deduped[-1] != b:
            deduped.append(b)
    return deduped


def chunk_text(
    text: LosslessText,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    source_document: str = "",
    jid: str = "",
) -> list[Chunk]:
    """把 authoritative text 切成 chunks。

    ## 參數

    * `text` —— `LosslessText`（authoritative）。**不做任何清理**。
    * `chunk_size` —— 目標大小（字元）。不是硬性上限。
    * `source_document` / `jid` —— document identity，寫進每個 chunk。

    ## 保證

    1. 對每個 chunk，`chunk.text == text.text[start:end]`（函式內部斷言）。
    2. `"".join(c.text for c in chunks) == text.text`（函式內部斷言）。
    3. offsets 嚴格遞增且不重疊。
    4. 沒有空 chunk（除非原文為空 —— 那時回傳空 list）。
    5. 零 LLM、零摘要、零改寫（FR-012）。
    """
    if chunk_size < MIN_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size 至少 {MIN_CHUNK_SIZE}，收到 {chunk_size}"
        )

    s = text.text
    n = len(s)

    # 空文件 → 沒有 chunk。這不是「產生一個空 chunk」的例外情況：
    # 那個 chunk 會是空的，而空 chunk 對檢索沒有任何意義。
    if n == 0:
        return []

    # 短於目標 → 單一 chunk
    if n <= chunk_size:
        c = Chunk(
            text=s,
            start_offset=0,
            end_offset=n,
            chunk_index=0,
            boundary_kind=BoundaryKind.SINGLE,
            source_document=source_document,
            jid=jid,
        )
        c.verify_against(text)
        return [c]

    bounds = _natural_boundaries(text)
    chunks: list[Chunk] = []
    start = 0
    index = 0

    while start < n:
        ideal_end = start + chunk_size
        if ideal_end >= n:
            # 最後一塊：吃掉剩下的全部
            c = Chunk(
                text=s[start:n],
                start_offset=start,
                end_offset=n,
                chunk_index=index,
                boundary_kind=BoundaryKind.TAIL,
                source_document=source_document,
                jid=jid,
            )
            c.verify_against(text)
            chunks.append(c)
            break

        end = _pick_boundary(bounds, start, ideal_end, chunk_size)

        kind = (
            BoundaryKind.EXACT if end == ideal_end
            else BoundaryKind.NATURAL if end is not None
            else BoundaryKind.FORCED
        )
        # 沒找到自然邊界 → 硬切（不記錄 offset 調整量；FORCED 本身就是記錄）
        cut = ideal_end if end is None else end

        c = Chunk(
            text=s[start:cut],
            start_offset=start,
            end_offset=cut,
            chunk_index=index,
            boundary_kind=kind,
            source_document=source_document,
            jid=jid,
        )
        c.verify_against(text)
        chunks.append(c)

        start = cut
        index += 1

    # 重組性質 —— 函式內部斷言，不只靠測試。
    # 這比「每個 chunk 的 substring 正確」更強：它保證沒有字元被漏掉。
    rebuilt = "".join(c.text for c in chunks)
    if rebuilt != s:
        raise ChunkIntegrityError(
            f"重組不等於原文：得到 {len(rebuilt)} 字元，原文 {len(s)} 字元"
        )
    for a, b in zip(chunks, chunks[1:]):
        if a.end_offset != b.start_offset:
            raise ChunkIntegrityError(
                f"chunk {a.chunk_index} 結尾 {a.end_offset} != "
                f"chunk {b.chunk_index} 開頭 {b.start_offset} —— 有字元被遺漏"
            )

    return chunks


def _pick_boundary(
    bounds: list[int],
    start: int,
    ideal_end: int,
    chunk_size: int,
) -> int | None:
    """挑一個最接近 `ideal_end` 的自然邊界；找不到回 None。

    ## 為什麼要「最接近」，而不是「第一個」

    第一版取的是 `start` 之後的**第一個**自然邊界。結果是：一份前兩行都很短
    的文件會產生一個只有 14 個字元的 chunk —— 因為第一行的 `\r\n` 結束就是
    一個邊界，而它離 `ideal_end` 還很遠。

    那個 chunk 對檢索幾乎沒用（使用者問「駁回再審之訴」不會命中它），而且它
    讓「強制硬切」的統計失真（真正該被記錄的 forced 反而被 natural 蓋掉）。

    正確的做法是：在可接受範圍內取**距離 `ideal_end` 最近**的那個。

    ## 可接受範圍

    * 下限：`start + chunk_size // 2` —— 不准為了落在自然邊界而讓 chunk 掉到
      目標的一半以下。
    * 上限：`ideal_end + chunk_size // 2` —— 也不准失控地變長。
    """
    import bisect

    lo_bound = start + chunk_size // 2
    hi_bound = ideal_end + chunk_size // 2

    lo = bisect.bisect_left(bounds, lo_bound)
    hi = bisect.bisect_right(bounds, hi_bound)
    if lo >= hi:
        return None

    candidates = bounds[lo:hi]
    return min(candidates, key=lambda b: (abs(b - ideal_end), b))


def chunk_document(
    doc: JudgmentDocument,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> list[Chunk]:
    """從 T009 的 `JudgmentDocument` 分塊。

    便利入口：包成 `LosslessText` 並帶入 document identity。回傳的每個
    chunk 都帶著 `source_document` 與 `jid`，所以 chunk 不會脫離它的來源
    （T015 acceptance 的追溯要求）。
    """
    t = LosslessText.from_string(doc.jfull)
    return chunk_text(
        t,
        chunk_size=chunk_size,
        source_document=doc.entry_path,
        jid=doc.jid,
    )


def reconstruct(chunks: list[Chunk]) -> str:
    """把 chunks 依序拼回原文。

    提供這個函式是為了讓「重組性質」成為**可被呼叫的能力**，而不是只存在於
    測試裡。驗證 chunking 正確性的最直接方式就是真的拼回去比對。
    """
    return "".join(c.text for c in sorted(chunks, key=lambda c: c.chunk_index))


def boundary_summary(chunks: list[Chunk]) -> dict[str, int]:
    """各邊界類型的筆數 —— 給 drift / 品質報告用。

    `forced` 的比例是個訊號：比例高代表分塊在硬切，文件的自然結構沒被利用。
    """
    counts: dict[str, int] = {}
    for c in chunks:
        k = c.boundary_kind.value
        counts[k] = counts.get(k, 0) + 1
    return dict(sorted(counts.items()))
