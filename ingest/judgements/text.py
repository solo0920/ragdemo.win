#!/usr/bin/env python3
"""判決全文的 lossless 表示（spec 004 T010 / FR-002, FR-003, FR-036）。

## 核心約束

> `JFULL` 是唯一 authoritative 全文來源。它到達儲存層時必須**逐 byte 相同**，
> 而 offset 必須指向那份原文。

這個模組的全部設計都是為了讓「不小心改到它」變得不可能，而不是讓人記得別改。

## 三件被明確禁止、而 Python 讓它很容易發生的事

1. **`.strip()` / `.strip("\r\n")`** —— spec 說 `JFULL` 的行尾是 CRLF
   （493/494）。對整份文字 strip 會動到開頭與結尾；對每一行 strip 會吃掉
   `　　主　　　文` 的前導全形空格，而那是**結構性縮排**。
2. **`.splitlines()` 再 `"\n".join()`** —— `splitlines()` 會把 CRLF、CR、LF、
   U+2028、U+2029 全部視為換行，重新 join 就把它們全部變成 LF。這會讓所有
   offset 失效，且**不會**報錯 —— 最危險的一類 bug。
3. **Unicode 正規化（NFC/NFKC）** —— 會改變字元序列。`　`（U+3000）與
   兩個半形空格在某些 NFKC 下會塌陷，而這是儲存層不該做的判斷。

## 為什麼同時提供 `raw` 與 `text`

- `raw`（bytes）—— 來源的 UTF-8 位元組。**hash 與 byte_len 都以它為準**，
  因為 hash 是對位元組算的。
- `text`（str）—— `raw.decode("utf-8")`。Python 字串以 code point 為單位，
  而 offset 在後續的 chunking（T015）與 citation detection（T012）需要的是
  Python 字串索引。兩者都指向同一份內容：`text` 是 `raw` 的決定性函數。

`slice()` 兩邊都支援：給定 **字元** offset 回傳字元 slice；給定 **位元組**
offset 回傳位元組 slice。兩者分開命名（`slice` / `slice_bytes`），因為把兩者
混為一談正是 offset 錯位的來源。

## 這個物件上刻意沒有的方法

`normalize()`、`strip()`、`collapse_whitespace()`、`to_lf()`、`clean()`
—— 一個都沒有。`ingest/laws/normalize.py::_clean` 對每一行做 `strip()`，
那對法規文字可接受，對判決文字會摧毀結構標記；這個模組的存在就是為了讓
那個函式**沒有機會**被用在判決上。

## section markers（T011）

`observed_markers()` 只回報**字面上真的出現**的標記，沒有固定詞彙表。
`corpus-schema-survey.md` 觀察到 46 種不同的組合、133 筆一個都沒有，
而 `主　　　文` 只出現在 48/494。任何「已知的 marker 集合」都會把大多數
真實判決判成「格式錯誤」，而那正是 FR-031 禁止的假設。
"""
from __future__ import annotations

import random
from dataclasses import dataclass


class TextIntegrityError(RuntimeError):
    """lossless 表示被破壞 —— 例如原文不是合法 UTF-8。"""


@dataclass(frozen=True)
class LosslessText:
    """`JFULL` 的 lossless 表示。frozen，且**沒有任何改寫方法**。"""

    raw: bytes    # 來源 UTF-8 位元組（authoritative）
    text: str     # raw.decode("utf-8")；與 raw 是同一份內容

    @classmethod
    def from_string(cls, s: str) -> "LosslessText":
        """從 str 建立。

        ⚠️ 這個 constructor **不做任何清理**。傳進來的空白、換行、全形空格
        全部原樣保留 —— 那是呼叫端（`document.from_document`）的責任，它
        從已驗證的 source 取值，不會清理。

        刻意不提供 `from_text_normalised` 之類的變體：一旦有那樣的名字，
        就會有人用它。
        """
        if not isinstance(s, str):
            raise TextIntegrityError(f"必須是 str，收到 {type(s).__name__}")
        return cls(raw=s.encode("utf-8"), text=s)

    @classmethod
    def from_bytes(cls, b: bytes) -> "LosslessText":
        """從位元組建立。

        這是 hash 比對用的路徑：`raw` 直接就是傳進來的 bytes，沒有任何
        round-trip，所以 hash 與來源完全一致。
        """
        if not isinstance(b, (bytes, bytearray)):
            raise TextIntegrityError(f"必須是 bytes，收到 {type(b).__name__}")
        raw = bytes(b)
        try:
            return cls(raw=raw, text=raw.decode("utf-8"))
        except UnicodeDecodeError as e:
            raise TextIntegrityError(f"不是合法的 UTF-8：{e}") from e

    # ── 唯讀量測 ────────────────────────────────────────────────────────────

    @property
    def byte_len(self) -> int:
        """UTF-8 位元組長度 —— hash 與儲存的依據。"""
        return len(self.raw)

    @property
    def char_len(self) -> int:
        """Python 字串長度（code points）。

        **不是** byte_len。混用這兩個是 offset 錯位的最常見來源，所以兩者
        都有明確名字，沒有任何捷徑讓它們看起來一樣。
        """
        return len(self.text)

    @property
    def sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self.raw).hexdigest()

    def count(self, needle: str) -> int:
        """子字串出現次數 —— 不正規化，直接數。"""
        return self.text.count(needle)

    @property
    def crlf_count(self) -> int:
        return self.text.count("\r\n")

    @property
    def bare_lf_count(self) -> int:
        """單獨的 LF（不是 CRLF 的一部分）。

        實測 corpus 是 0（493/494 全 CRLF，1 筆是無換行的單句判決）。
        這個屬性存在的目的是讓「有沒有人偷偷把 CRLF 轉成 LF」可以被看見。
        """
        return self.text.count("\n") - self.crlf_count

    @property
    def ideographic_space_count(self) -> int:
        """U+3000 全形空格的數量 —— 結構性縮排的指標。"""
        return self.text.count("　")

    # ── 切片 ────────────────────────────────────────────────────────────────

    def slice(self, start: int, end: int) -> str:
        """字元 offset 的切片 —— 回傳**完全相同**的子字串。"""
        return self.text[start:end]

    def slice_bytes(self, start: int, end: int) -> bytes:
        """位元組 offset 的切片 —— 回傳**完全相同**的位元組。

        給需要以 byte 為單位定位的呼叫端（例如重算某段內容的 hash）。
        與 `slice()` 分開命名，因為把 char offset 當 byte offset 用是
        offset 錯位的直接來源。
        """
        return self.raw[start:end]

    def char_to_byte_offset(self, char_offset: int) -> int:
        """字元 offset → 位元組 offset。

        多位元組字元（中文、全形空格）會讓兩者不同。T015 的 chunking 需要
        這個換算，而它必須是**明確的呼叫**，不能靠呼叫端自己猜。
        """
        return len(self.text[:char_offset].encode("utf-8"))

    def byte_to_char_offset(self, byte_offset: int) -> int:
        """位元組 offset → 字元 offset。落在多位元組字元中間時拋錯。

        拋錯而不是取捨：截斷一個多位元組字元會產生非法 UTF-8，而那正是
        「悄悄改到原文」的一種形式。
        """
        try:
            chunk = self.raw[:byte_offset].decode("utf-8")
        except UnicodeDecodeError as e:
            raise TextIntegrityError(
                f"byte offset {byte_offset} 落在多位元組字元中間"
            ) from e
        return len(chunk)

    def is_byte_exact(self, other: bytes | "LosslessText") -> bool:
        """與另一份位元組（或另一個 LosslessText）是否**逐 byte 相同**。

        給儲存層在寫入前後各呼叫一次。
        """
        if isinstance(other, LosslessText):
            return self.raw == other.raw
        return self.raw == other

    def assert_lossless(self) -> None:
        """自我驗證：`text` 必須是 `raw` 的決定性 decode 結果。

        這不是防禦性程式碼，是給「有人改過這個類別」的保險。若有人加了
        一個 `__post_init__` 做 normalize，這裡會立刻抓到。
        """
        if self.raw != self.text.encode("utf-8"):
            raise TextIntegrityError(
                "lossless 表示已被破壞：raw 與 text 不一致"
            )


# ── T011：section markers ───────────────────────────────────────────────────

# 觀察到的標記**前綴**。這不是一個詞彙表 —— 這是「筆者見過、想讓呼叫端能
# 問『這份文件有沒有主文段落』」的最小集合。
#
# 關鍵：判斷方式是「這段文字**出現**嗎」，不是「文件**符合**這組標記嗎」。
# 133/494 筆一個都沒有，那不是錯誤，那只是「這份判決的結構我們還沒看懂」。
#
# 刻意包含全形空格變體：`主　　　文`（三個 U+3000）與 `主　　文`（兩個）
# 與 `主文`（無空格）在真實 corpus 中都出現過。用固定字串比對而不是
# 正規化後比對，是因為 FR-036 禁止改變 offset，而正規化正是那種改變。
_MARKER_VARIANTS: tuple[str, ...] = (
    "主　　　文",
    "主　　文",
    "主文",
    "事　　實",
    "事實",
    "理　　由",
    "理由",
    "法律依據",
    "附錄",
)


def observed_markers(text: LosslessText | str) -> list[str]:
    """回報**字面上真的出現**的標記。

    沒有 marker → 回傳空清單。不拋錯、不猜、不回傳「預設值」。

    ## 為什麼空清單不是錯誤

    `corpus-schema-survey.md` 記錄：46 種不同的標記組合，133/494 筆完全沒有
    任何已辨識的標記。若這個函式對「沒有標記」丟出異常，等於宣稱 27% 的
    真實判決是壞的 —— 而那份證據恰恰說明「壞掉」的是我們的假設，不是資料
    （FR-031）。

    ## 不做正規化

    比對的是字面字串。`主　　文` 與 `主文` 是不同的標記，且這個差異本身
    就是觀察結果。若在這裡做 whitespace 正規化，我們就看不見「這個空格數
    目是可變的」這個事實了。

    回傳順序 = `_MARKER_VARIANTS` 的宣告順序，固定且 deterministic。
    """
    s = text.text if isinstance(text, LosslessText) else text
    if not isinstance(s, str):
        raise TextIntegrityError(f"必須是 str 或 LosslessText，收到 {type(s).__name__}")
    return [m for m in _MARKER_VARIANTS if m in s]


def marker_survey(text: LosslessText | str) -> dict:
    """把一份文件的結構觀察記錄成 dict（給 drift 報告用）。

    這是**測量**，不是解析：不改變原文，也不推論段落語意。回傳的每個數字
    都是直接數出來的。
    """
    s = text.text if isinstance(text, LosslessText) else text
    return {
        "markers": observed_markers(s),
        "has_marker": bool(observed_markers(s)),
        "crlf_count": s.count("\r\n"),
        "ideographic_space_count": s.count("　"),
        "char_len": len(s),
        "byte_len": len(s.encode("utf-8")),
        "line_count": s.count("\n") + (0 if s.endswith("\n") or not s else 1),
    }
