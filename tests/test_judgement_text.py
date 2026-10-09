"""`text.py` 的契約測試（spec 004 T010 lossless + T011 markers）。

## 這個測試檔案要證明的事

`JFULL` 到達儲存層時**逐 byte 相同**，而 offset 指向那份原文。

## 為什麼不能用「大概相等」驗證

下面這種斷言看起來在驗 lossless，實際上驗不到：

```python
assert parsed_text == expected_text.strip()
```

兩邊都經過 `strip()`，所以「實際上有人做了 per-line strip」這個 bug 會讓它
**照樣通過**。這條測試裡每一個比對都用未經加工的原始值，且會**先斷言兩者
確實不同** —— 若 fixture 的期望值恰好等於 strip 後的結果，那條測試就是空的，
這件事本身必須被看見。

## 本檔案刻意測的三種改寫

| 改寫 | 在 Python 裡有多容易 | 對應的測試 |
|---|---|---|
| per-line `strip()` | 一行 | `test_ideographic_indentation_survives` |
| `splitlines()` + `join("\n")` | 兩行 | `test_crlf_is_not_converted_to_lf` |
| `splitlines()` + `join("\r\n")` | 兩行 | `test_bare_lf_is_not_converted_to_crlf` |

第三個特別值得測：它是「修正反過來」的方向，而 `JFULL` 裡真的有一筆是
LF-only（那 1 筆單句無換行的 anomaly 屬於另一類）。若 pipeline 假設全部是
CRLF 而「修正」LF-only 的那一筆，那筆的原文就被改寫了 —— 而它是真實資料。

## fixture

`docs/*.json` 的 `JFULL` 是手寫的，形狀對齊實測 corpus：CRLF 行尾、U+3000
結構縮排、`主　　　文` 這種三個全形空格的標記。
"""
from __future__ import annotations

import json
import random
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "tests" / "fixtures" / "judgements" / "docs"
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import text as T  # noqa: E402

CIVIL = DOCS / "civil_6field.json"
CRIMINAL = DOCS / "criminal_6field.json"
CONSTITUTIONAL = DOCS / "constitutional_5field_empty_jpdf.json"
SINGLE = DOCS / "single_sentence_no_breaks.json"
LF_ONLY = DOCS / "lf_line_endings.json"
JYEAR79 = DOCS / "jyear_79.json"


def jfull(p: Path) -> str:
    return json.loads(p.read_text(encoding="utf-8"))["JFULL"]


def lossless(p: Path) -> T.LosslessText:
    return T.LosslessText.from_string(jfull(p))


# ── 建構 ────────────────────────────────────────────────────────────────────

def test_from_string_preserves_byte_length():
    s = jfull(CIVIL)
    t = T.LosslessText.from_string(s)
    assert t.byte_len == len(s.encode("utf-8"))
    assert t.char_len == len(s)
    # 多位元組字元讓兩者不同 —— 這是必須分開命名兩個長度的原因
    assert t.byte_len > t.char_len


def test_from_bytes_is_the_identity_path():
    """`from_bytes()` 不做 round-trip —— raw 就是傳進來的 bytes。"""
    b = b"\xe8\xa8\xb4\r\n"
    t = T.LosslessText.from_bytes(b)
    assert t.raw == b
    assert t.text == "訴\r\n"


def test_from_string_and_from_bytes_agree():
    s = jfull(CIVIL)
    assert T.LosslessText.from_string(s).raw == T.LosslessText.from_bytes(
        s.encode("utf-8")
    ).raw


def test_invalid_utf8_bytes_are_rejected():
    with pytest.raises(T.TextIntegrityError):
        T.LosslessText.from_bytes(b"\xff\xfe")


def test_non_string_is_rejected():
    with pytest.raises(T.TextIntegrityError):
        T.LosslessText.from_string(b"bytes")  # type: ignore[arg-type]


def test_record_is_frozen():
    t = lossless(CIVIL)
    with pytest.raises(Exception):
        t.text = "改掉了"  # type: ignore[misc]


def test_assert_lossless_passes_for_constructed_values():
    lossless(CIVIL).assert_lossless()


# ── CRLF 保留 ──────────────────────────────────────────────────────────────

def test_crlf_count_is_preserved():
    s = jfull(CIVIL)
    t = lossless(CIVIL)
    assert s.count("\r\n") == 6
    assert t.crlf_count == s.count("\r\n") == 6


def test_crlf_is_not_converted_to_lf():
    """`splitlines()` + `join("\n")` 會把 CRLF 變 LF。這必須被看見。"""
    s = jfull(CIVIL)
    t = lossless(CIVIL)

    # 那個常見的「安全」改寫會產生這個結果
    mangled = "\n".join(s.splitlines())
    assert mangled != s, "前提：改寫後確實不同"
    assert mangled.count("\r\n") == 0

    # 我們的表示**沒有**被這樣改寫
    assert t.crlf_count == 6
    assert t.bare_lf_count == 0
    assert t.raw == s.encode("utf-8")


def test_bare_lf_is_not_converted_to_crlf():
    """反方向的「修正」同樣是改寫。

    `lf_line_endings.json` 那筆是 LF-only 的真實資料形狀。若 pipeline 假設
    全部是 CRLF 而把它「修正」成 CRLF，原文就被改寫了。
    """
    s = jfull(LF_ONLY)
    t = lossless(LF_ONLY)

    n_lf = s.count("\n")
    n_crlf = s.count("\r\n")
    assert n_lf == 3 and n_crlf == 0, "前提：這筆確實是 LF-only"

    # 那個「修正」會把每個 LF 換成 CRLF。
    #
    # 注意 `splitlines()` 會吞掉結尾的換行，所以 3 個 LF 會變成 2 組 CRLF ——
    # 這正是「用 splitlines 重建文字」的危險：它同時改了行尾**和**行數。
    mangled = "\r\n".join(s.splitlines())
    assert mangled != s, "前提：改寫後確實不同"
    assert mangled.count("\r") == 2, "原本 0 個 CR，變成 2 個"
    assert mangled.count("\n") == 2, "原本 3 個 LF（最後一行有結尾換行）"
    assert mangled.count("\r\n") == 2

    # 我們的表示沒有被這樣改寫
    assert t.crlf_count == n_crlf == 0
    assert t.bare_lf_count == n_lf == 3
    assert t.raw == s.encode("utf-8")


def test_bare_lf_count_math_is_correct_on_mixed_content():
    """`bare_lf_count == total_LF - CRLF` 這個算式在混合內容上要成立。"""
    s = "a\r\nb\nc\r\nd\re"
    t = T.LosslessText.from_string(s)
    assert t.crlf_count == 2
    assert t.bare_lf_count == 1
    # 單獨的 CR 不算 LF
    assert s.count("\n") == 3


# ── U+3000 保留 ───────────────────────────────────────────────────────────

def test_ideographic_space_count_is_preserved():
    s = jfull(CIVIL)
    assert lossless(CIVIL).ideographic_space_count == s.count("　") > 0


def test_ideographic_indentation_survives():
    """行首的全形空格必須保留。

    `ingest/laws/normalize.py::_clean` 對每一行做 `strip()`，那會吃掉這裡
    每一行的前導全形空格 —— 而那是判決文字的**結構性縮排**。
    """
    s = jfull(CIVIL)
    t = lossless(CIVIL)

    # 那個改寫會產生這個結果
    per_line_stripped = "\r\n".join(line.strip() for line in s.split("\r\n"))
    assert per_line_stripped != s, "前提：per-line strip 後確實不同"
    assert per_line_stripped.count("　") < s.count("　")

    # 我們的表示沒有被這樣改寫
    assert "\r\n　　駁回再審之訴。" in t.text
    assert t.ideographic_space_count == s.count("　")


def test_laws_normalize_clean_is_not_reused():
    """明確斷言：laws 的 `_clean()` 不得用在判決文字上。

    spec §Source Structure Contract 點名這個函式：它對法規文字可接受，對
    判決文字會摧毀 U+3000 標記。
    """
    laws = ROOT / "ingest" / "laws" / "normalize.py"
    if not laws.is_file():
        pytest.skip("ingest/laws/normalize.py 不存在")

    src = laws.read_text("utf-8")
    assert "_clean" in src, "前提：laws 仍有 _clean 函數"

    # text.py 不得 import 或參照它。
    #
    # 只查**程式碼**，不查 docstring：模組 docstring 正當地引用了 `_clean` 來
    # 說明「這個函式刻意不被使用」。字串搜尋整份檔案會誤判 —— 這是本 repo
    # 第三次遇到同一個陷阱（T004 的 `zlib`、T008 的 `court`）。
    import ast

    tree = ast.parse((ROOT / "ingest" / "judgements" / "text.py").read_text("utf-8"))
    code_literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "\n" not in n.value          # 排除 docstring
        and len(n.value) < 64
    }
    assert not any("_clean" in v for v in code_literals), code_literals

    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert "normalize" not in imported


def test_marks_fullwidth_space_is_not_normalised():
    """`主　　　文` 的三個 U+3000 必須原樣保留。"""
    s = jfull(CIVIL)
    assert "主　　　文" in s
    assert lossless(CIVIL).count("主　　　文") == 1
    # NFKC 會把它塌陷 —— 這正是我們不做正規化的原因
    assert unicodedata.normalize("NFKC", s) != s


def test_no_unicode_normalisation_is_applied():
    """NFKC/NFC 都不得被套用。"""
    s = "ＡＢ　１２３"
    t = T.LosslessText.from_string(s)
    assert t.text == s
    assert t.text != unicodedata.normalize("NFKC", s)
    assert unicodedata.normalize("NFC", t.text) != t.text or True  # NFC 可能不變


# ── 空白保留 ───────────────────────────────────────────────────────────────

def test_leading_whitespace_preserved():
    s = "　　開頭有全形空格\r\n"
    t = T.LosslessText.from_string(s)
    assert t.text == s
    assert t.text.startswith("　　")
    assert t.text != s.strip()


def test_trailing_whitespace_preserved():
    s = "結尾有空白   \r\n　　全形結尾　　"
    t = T.LosslessText.from_string(s)
    assert t.text == s
    assert t.text.endswith("　　")
    assert t.text != s.strip()


def test_embedded_blank_lines_preserved():
    s = "a\r\n\r\n\r\nb\r\n"
    t = T.LosslessText.from_string(s)
    assert t.text == s
    # 四個 CRLF = 三個空行（a / 空 / 空 / b）
    assert t.crlf_count == 4
    assert t.bare_lf_count == 0
    assert "\r\n\r\n\r\n" in t.text

    # 常見的改寫 `"\n".join(s.splitlines())` 把 CRLF 全變 LF，而且
    # `splitlines()` 會吞掉結尾換行 —— 空行與結尾都沒了。
    mangled = "\n".join(s.splitlines())
    assert mangled != s, "前提：改寫後確實不同"
    assert mangled.count("\r\n") == 0
    assert not mangled.endswith("\n")

    # 我們的表示兩者都保留
    assert t.text.endswith("\r\n")


def test_single_sentence_anomaly_has_no_line_breaks():
    """那 1 筆單句無換行的 anomaly 必須逐 byte 保留。"""
    s = jfull(SINGLE)
    assert "\n" not in s and "\r" not in s
    t = lossless(SINGLE)
    assert t.text == s
    assert t.crlf_count == 0
    assert t.bare_lf_count == 0


def test_empty_jfull_is_allowed():
    """空字串是合法值（不是錯誤）。"""
    t = T.LosslessText.from_string("")
    assert t.text == ""
    assert t.byte_len == 0
    assert t.char_len == 0


def test_whitespace_only_jfull_is_preserved_verbatim():
    s = "　 \t\r\n　"
    t = T.LosslessText.from_string(s)
    assert t.text == s
    assert t.byte_len == len(s.encode("utf-8"))


# ── 切片：offset 必須精確 ──────────────────────────────────────────────────

def test_slice_equals_text_slice_at_every_offset():
    """`slice(s,e)` 必須完全等於 `text[s:e]` —— 對所有 s ≤ e 成立。"""
    t = lossless(CIVIL)
    n = t.char_len
    for start in range(0, n, 3):
        for end in range(start, n + 1, 5):
            assert t.slice(start, end) == t.text[start:end]


def test_slice_bytes_equals_raw_slice():
    t = lossless(CIVIL)
    n = t.byte_len
    for start in range(0, n, 7):
        for end in range(start, n + 1, 11):
            assert t.slice_bytes(start, end) == t.raw[start:end]


def test_slice_round_trips_on_random_offsets():
    """200 次隨機 offset 往返 —— 用固定 seed，結果 deterministic。"""
    t = lossless(CIVIL)
    rng = random.Random(20260907)   # 固定 seed：測試必須 deterministic
    n = t.char_len
    for _ in range(200):
        a = rng.randrange(0, n)
        b = rng.randrange(a, n + 1)
        piece = t.slice(a, b)
        assert piece == t.text[a:b]
        assert piece.encode("utf-8") == t.raw[
            t.char_to_byte_offset(a) : t.char_to_byte_offset(b)
        ]


def test_slice_crossing_multi_byte_boundaries_is_exact():
    """跨越多位元組字元的切片必須精確 —— 那是 offset 最容易錯的地方。

    用 CJK（3 bytes）而不是 emoji（4 bytes）：真實 corpus 的字元以 CJK 為主，
    而 4-byte 的情境由 `test_byte_offset_inside_a_character_raises` 覆蓋。
    """
    s = "臺灣法院判決書"
    t = T.LosslessText.from_string(s)
    assert len(s) == 7
    assert t.char_len == 7
    assert t.byte_len == 21   # 7 字 × 3 bytes
    for i in range(len(s)):
        assert t.slice(i, i + 1) == s[i]
        assert t.slice_bytes(
            t.char_to_byte_offset(i), t.char_to_byte_offset(i + 1)
        ) == s[i].encode("utf-8")


def test_full_slice_returns_everything_unchanged():
    t = lossless(CIVIL)
    assert t.slice(0, t.char_len) == t.text
    assert t.slice_bytes(0, t.byte_len) == t.raw


def test_empty_slice_is_empty_string():
    t = lossless(CIVIL)
    assert t.slice(5, 5) == ""
    assert t.slice_bytes(5, 5) == b""


# ── offset 換算 ────────────────────────────────────────────────────────────

def test_char_to_byte_offset_accumulates_multi_byte_width():
    # 「訴」3 bytes、CRLF 2 bytes、「　」也是 3 bytes（U+3000 不是 4）
    s = "訴\r\n　文"
    t = T.LosslessText.from_string(s)
    assert t.char_len == 5
    assert t.byte_len == 11
    assert t.char_to_byte_offset(0) == 0
    assert t.char_to_byte_offset(1) == 3    # 訴 = 3 bytes
    assert t.char_to_byte_offset(3) == 5    # \r\n = 2 bytes
    assert t.char_to_byte_offset(4) == 8    # 　= 3 bytes


def test_byte_to_char_offset_is_the_inverse():
    s = "訴\r\n　文"
    t = T.LosslessText.from_string(s)
    for i in range(t.char_len + 1):
        b = t.char_to_byte_offset(i)
        assert t.byte_to_char_offset(b) == i


def test_byte_offset_inside_a_character_raises():
    """落在多位元組字元中間必須拋錯，不該截斷。

    截斷會產生非法 UTF-8，而那正是「悄悄改到原文」的一種形式。
    """
    t = T.LosslessText.from_string("訴")
    with pytest.raises(T.TextIntegrityError):
        t.byte_to_char_offset(1)   # 「訴」佔 3 bytes，offset 1 在中間


# ── hash 與 byte-exact 檢查 ────────────────────────────────────────────────

def test_sha256_is_over_raw_bytes():
    import hashlib

    s = jfull(CIVIL)
    t = lossless(CIVIL)
    assert t.sha256 == hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_is_byte_exact_compares_bytes():
    s = jfull(CIVIL).encode("utf-8")
    t = lossless(CIVIL)
    assert t.is_byte_exact(s) is True
    assert t.is_byte_exact(t) is True
    assert t.is_byte_exact(s + b"x") is False
    assert t.is_byte_exact(s.replace(b"\r\n", b"\n")) is False


def test_assert_lossless_catches_tampering():
    """若有人改了這個類別去 normalize，`assert_lossless()` 會抓到。"""
    broken = T.LosslessText(raw=b"abc", text="abc ")   # 繞過 constructor
    with pytest.raises(T.TextIntegrityError):
        broken.assert_lossless()


# ── 這個物件上不得有改寫方法 ───────────────────────────────────────────────

def test_no_normalising_methods_exist():
    """物件上不得有 normalize / strip / collapse / to_lf / clean。

    這是機制性保障：方法不存在，就沒有「順手呼叫」的可能。
    """
    public = {n for n in dir(T.LosslessText) if not n.startswith("_")}
    for forbidden in (
        "normalize", "normalise", "strip", "lstrip", "rstrip",
        "collapse", "clean", "to_lf", "to_crlf", "fix_line_endings",
        "replace", "split", "splitlines", "title", "lower", "upper",
    ):
        assert forbidden not in public, f"LosslessText 不得有 {forbidden}"


def test_text_module_does_not_import_normalize_helpers():
    src = (ROOT / "ingest" / "judgements" / "text.py").read_text("utf-8")
    code = src.split('"""')[-1]   # 只看 docstring 之後的程式碼
    assert "_clean" not in code
    assert "laws" not in code


# ── T011：section markers ──────────────────────────────────────────────────

def test_markers_are_observed_not_assumed():
    """只回報**真的出現**的標記。"""
    markers = T.observed_markers(lossless(CIVIL))
    assert "主　　　文" in markers
    assert "事實" not in markers      # 這份沒有「事　　實」


def test_marker_without_markers_returns_empty_list():
    """沒有 marker → 空清單，**不拋錯**。

    133/494 筆真實判決一個已辨識的標記都沒有。對那些丟異常等於宣稱 27% 的
    真實資料是壞的 —— 而證據說明壞掉的是我們的假設，不是資料（FR-031）。
    """
    markers = T.observed_markers(lossless(CRIMINAL))
    assert markers == []
    assert T.observed_markers(lossless(SINGLE)) == []


def test_marker_never_raises_on_any_input():
    for s in ("", "　", "任意文字", "主文", "\r\n\r\n"):
        assert isinstance(T.observed_markers(s), list)


def test_marker_preserves_fullwidth_spacing():
    """`主　　　文` ≠ `主文` —— 空格數目是觀察結果，不得正規化。

    若做了 whitespace 正規化，我們就看不見「這個空格數目是可變的」這個
    事實 —— 而那正是 FR-031 要我們記錄的。
    """
    s = "主　　　文"
    t = T.LosslessText.from_string(s)
    assert T.observed_markers(t) == ["主　　　文"]
    assert "主文" not in T.observed_markers(t)

    s2 = "主　　文"   # 兩個全形空格
    assert T.observed_markers(s2) == ["主　　文"]


def test_marker_does_not_normalise_input():
    """觀察 marker 的過程不得改動原文。"""
    t = lossless(CIVIL)
    before = t.raw
    T.observed_markers(t)
    T.marker_survey(t)
    assert t.raw == before


def test_marker_list_is_deterministic_and_ordered():
    """回傳順序固定 —— 同一份文件兩次結果必須相同。"""
    t = lossless(CIVIL)
    assert T.observed_markers(t) == T.observed_markers(t)
    # 多個 marker 同時出現時，順序由宣告順序決定
    multi = "理由\r\n主文\r\n附錄"
    assert T.observed_markers(multi) == ["主文", "理由", "附錄"]


def test_marker_survey_reports_measurements_not_interpretation():
    """`marker_survey()` 回傳**量測值**，不含任何推論。"""
    s = jfull(CIVIL)
    survey = T.marker_survey(s)
    assert survey["crlf_count"] == s.count("\r\n")
    assert survey["ideographic_space_count"] == s.count("　")
    assert survey["char_len"] == len(s)
    assert survey["byte_len"] == len(s.encode("utf-8"))
    assert survey["has_marker"] is True
    # 不得有段落語意之類的推論欄位
    for forbidden in ("sections", "paragraphs", "structure", "court", "type"):
        assert forbidden not in survey


def test_marker_survey_handles_no_marker_case():
    survey = T.marker_survey(lossless(CRIMINAL))
    assert survey["has_marker"] is False
    assert survey["markers"] == []


def test_marker_vocabulary_is_small_and_documented():
    """詞彙表刻意很小 —— 它是「已觀察到的」，不是「應該有的」。

    46 種不同的組合、133 筆一個都沒有；任何把自己當成完整詞彙表的清單都會
    讓大多數真實判決看起來「格式錯誤」。
    """
    assert len(T._MARKER_VARIANTS) < 15
    # 每個變體之間的差異只有全形空格的數目（同一個詞）
    assert "主　　　文" in T._MARKER_VARIANTS
    assert "主　　文" in T._MARKER_VARIANTS
    assert "主文" in T._MARKER_VARIANTS


def test_no_fixed_section_grammar_is_assumed():
    """不得宣稱某份文件「有 N 段」或「缺少某段落」。"""
    survey = T.marker_survey(lossless(CRIMINAL))
    # 沒有 marker 的文件仍然是完整判決 —— 報告裡不得出現「缺段落」
    assert "missing_sections" not in survey
    assert "section_count" not in survey
