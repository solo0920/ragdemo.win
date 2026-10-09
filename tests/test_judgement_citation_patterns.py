"""`citation_patterns.py` 的契約測試（spec 004 T012 / FR-033）。

## 兩套數字系統是強制要求，不是選項

FR-033 明寫：只認阿拉伯數字的偵測器**不合規**。survey 觀察到
`第四十四條` 出現 391 次、`第44條` 出現 4,919 次，且有 **38 份文件同時使用
兩者**。只支援阿拉伯會安靜地漏掉 7.4% —— 那是最危險的失敗形狀，因為它
不報錯。

## 這個模組不得有 gazetteer

`citation-pattern-survey.md` §4.3 的實測：repo 自己的 `laws_meta.jsonl`
（1,347 個名稱）只覆蓋觀察到的 1,629 個候選名稱中的 **63 個（3.9%）**，
而且缺了 corpus 最常被引用的 `犯詐欺犯罪危害防制條例`，連 `刑法` 都缺。

一份 3.9% 覆蓋率的名單當 oracle，會讓 96% 的引用拿到錯誤答案，而那些答案
看起來完全合理。所以這個模組**不含**任何名單，測試用可執行斷言守住。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import citation_patterns as P  # noqa: E402
import text as T  # noqa: E402


def match(s: str) -> list[str]:
    """回傳所有 pattern 在 `s` 上的匹配（依最長優先、重疊抑制後）。"""
    t = T.LosslessText.from_string(s)
    claimed: list[tuple[int, int]] = []
    out: list[str] = []
    for p in P.PATTERNS:
        for m in p.regex.finditer(t.text):
            if any(a < m.end() and m.start() < b for a, b in claimed):
                continue
            claimed.append((m.start(), m.end()))
            out.append(m.group(0))
    return out


# ── 兩套數字系統（FR-033）──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "第四十四條",          # survey：391 次
        "第44條",             # survey：4,919 次
        "第114條",
        "第一百一十四條",
        "第1條",
        "第一條",
        "第十條",
        "第二百三十四條",
    ],
)
def test_both_numeral_systems_match(text):
    """阿拉伯與中文數字都必須命中 `article`。"""
    assert "article" in {p.name for p in P.PATTERNS if p.regex.search(text)}


def test_numeral_systems_are_not_alternatives():
    """同一個 pattern 必須同時支援兩套系統（不是兩個獨立 pattern）。"""
    p = P.get("article")
    assert p.regex.search("第44條")
    assert p.regex.search("第四十四條")
    # 兩者是同一個 regex 的不同輸入
    assert p.regex.search("第44條").group(0) == "第44條"
    assert p.regex.search("第四十四條").group(0) == "第四十四條"


def test_arabic_and_chinese_never_produce_mixed_output():
    """`第4十四條` 這種混合寫法不該被當成一個候選的一部分。

    實務上不會出現這種寫法，而允許它寬鬆只會製造假的 `第4條` + `十四條`。
    驗證方式：混合字串不會被 `article` 完整匹配成一個 token。
    """
    assert P.get("article").regex.search("第4十四條") is None


# ── 結構後綴（survey §3.1）────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        ("第44條", "article"),
        ("第2項", "paragraph"),
        ("第3款", "subparagraph"),
        ("第44條第2項", "article_paragraph"),
        ("第44條第3款", "article_subparagraph"),
        ("第44條第2項第3款", "article_paragraph_subparagraph"),
        ("第2項第3款", "paragraph_subparagraph"),
        ("第185條之2", "article_added"),
        ("第55條前段", "article_first_part"),
        ("第2項前段", "paragraph_first_part"),
        ("第四十四條之三", "article_added"),
        ("第55條前段", "article_first_part"),
    ],
)
def test_structural_suffixes(text, expected):
    """survey §3.1 記錄的每個結構都要有對應 pattern。"""
    matched = {
        p.name for p in P.PATTERNS if p.regex.search(text)
    }
    assert expected in matched, f"{text}: 預期 {expected}，實得 {matched}"


def test_all_documented_structures_have_evidence_counts():
    """survey 量化過的結構必須帶著那個次數。

    這讓日後能回答「corpus 換版後結構分佈變了嗎」。
    """
    d = P.documented_coverage()
    assert d["article"] == 5310
    assert d["paragraph"] == 3411
    assert d["article_paragraph"] == 2131
    assert d["subparagraph"] == 1357
    assert d["paragraph_subparagraph"] == 767
    assert d["article_added"] == 760
    assert d["paragraph_first_part"] == 276
    assert d["article_first_part"] == 89


# ── 重疊抑制：最長優先 ─────────────────────────────────────────────────────

def test_longest_pattern_wins_over_its_prefix():
    """`第44條第2項` 必須是**一個**候選，不是兩個。

    若 `article` 先跑並「贏得」了前綴，剩下的 `第2項` 會被當成獨立候選 ——
    那會產生一個假的「引用第 2 項」，而實際上它只是同一個引用的後半段。
    """
    assert match("第44條第2項") == ["第44條第2項"]
    assert match("第44條第2項第3款") == ["第44條第2項第3款"]
    assert match("第185條之2") == ["第185條之2"]


def test_short_pattern_still_matches_when_alone():
    """抑制只在重疊時發生。"""
    assert match("第44條") == ["第44條"]
    assert match("第2項") == ["第2項"]


def test_patterns_are_ordered_longest_first():
    """由長到短的宣告順序是可預測行為的基礎。

    ## 為什麼用「最長的匹配」而不是「regex 字串最長」

    宣告順序真正要保證的是：**更完整的結構先跑**，所以 `第44條第2項` 不會先被
    `第44條` 吃掉。判斷依據是「這個 pattern 在典型輸入上會吃掉多長的文字」，
    不是 regex 字串的長度 —— `第X條` 的 regex 比 `第X條之Y` 短，但它吃的
    字數不一定較少。

    這裡的實際斷言：每個 pattern 的**結構複雜度**遞減（後綴越多越前），
    而真正的行為驗證在上面的 `test_longest_pattern_wins_over_its_prefix` ——
    那才是「重疊抑制正確」的證明。順序本身只是讓行為可預測。
    """
    # 依「有幾個結構關鍵字」排序：完整形式必須在它的前綴之前
    def complexity(p: P.Pattern) -> int:
        s = p.regex.pattern
        return sum(1 for k in ("條", "項", "款", "之", "前段") if k in s)

    complexities = [complexity(p) for p in P.PATTERNS]
    assert complexities == sorted(complexities, reverse=True), (
        P.pattern_names(),
        complexities,
    )
    # 最長的形式必須排在最前面
    assert P.PATTERNS[0].name == "article_paragraph_subparagraph"


# ── 文件內部引用也是候選 ───────────────────────────────────────────────────

def test_doc_internal_reference_matches_the_pattern():
    """`系爭契約第一條` 必須**匹配** article pattern。

    tasks.md acceptance 明確要求這一點：它是**候選**，不是法律引用。
    若偵測器因為「知道它不是法律」而不匹配，那是 classification 的工作，
    不是 detection 的。

    注意 `原審卷第371頁` **不會**被匹配：pattern 只認結尾是「條／項／款」的
    結構，而「頁」不在其中。survey §5 把 `原審卷第371頁` 列為 false positive
    類別，但它對應的 pattern（`第X頁`）並不在 FR-033 要求的結構集合裡 ——
    加它等於擴大 pattern 集合，那超出 T012 的範圍。
    """
    assert match("系爭契約第一條") == ["第一條"]
    # 「頁」不是條／項／款 —— 不匹配
    assert match("原審卷第371頁") == []


def test_pattern_has_no_document_semantics():
    """pattern 不得知道「契約」是什麼。

    它只認形狀（`第X條`）。加一個「排除契約」的白名單會讓偵測與分類混在
    一起，而 classification 必須能對同一個候選給出不同標籤。
    """
    src = (ROOT / "ingest" / "judgements" / "citation_patterns.py").read_text("utf-8")
    for word in ("契約", "原審", "卷", "判決"):
        # 只允許出現在 docstring / 註解（用 AST 排除）
        import ast

        tree = ast.parse(src)
        code_literals = {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and "\n" not in n.value
            and len(n.value) < 64
        }
        assert word not in code_literals, f"pattern 不得含 {word}"


# ── 不得有 gazetteer（survey §4.3 的直接後果）──────────────────────────────

def test_module_exports_no_gazetteer():
    assert P.has_gazetteer() is False
    assert P.gazetteer_size() == 0


def test_module_has_no_statute_name_list():
    """模組裡不得有法條名稱的常數或清單。

    用 AST 檢查字串常數：docstring 裡討論「為何沒有 gazetteer」是正常的，
    但一個真正的名單（例如 `"刑法"`、`"民法"`、`"中華民國刑法"`）會是短字串
    常數。
    """
    import ast

    tree = ast.parse(
        (ROOT / "ingest" / "judgements" / "citation_patterns.py").read_text("utf-8")
    )
    code_literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "\n" not in n.value       # 排除 docstring
        and len(n.value) < 64
    }
    # 已知會被誤判的字樣（出現在 regex 結構裡）
    allowed = {"條", "項", "款", "目", "前段", "之"}
    suspicious = {
        v for v in code_literals
        if v not in allowed
        and any(k in v for k in ("法", "刑法", "民法", "條例", "法規"))
        and not v.startswith("第")
    }
    assert not suspicious, f"citation_patterns 不得含法條名稱：{suspicious}"


def test_module_does_not_load_laws_metadata():
    """不得讀 `laws_meta.jsonl` 或任何外部名單。"""
    import ast

    src = (ROOT / "ingest" / "judgements" / "citation_patterns.py").read_text("utf-8")
    tree = ast.parse(src)

    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not (called & {"load_gazetteer", "read_gazetteer", "lookup_statute"})

    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert "json" not in imported, "不得讀外部名單檔"

    # 「不得出現 laws_meta」用 AST 查字串常數，不做整檔搜尋：模組 docstring
    # 正當地引用它來說明「為何不載入這份名單」。
    code_literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "\n" not in n.value
        and len(n.value) < 64
    }
    assert not any("laws_meta" in v for v in code_literals), code_literals


def test_module_has_no_resolution_function():
    """不得有把 pattern 轉成法條身分的函式。"""
    public = {n for n in dir(P) if not n.startswith("_")}
    for forbidden in (
        "resolve", "resolve_statute", "to_statute", "statute_name",
        "lookup", "lookup_law", "identify_statute",
    ):
        assert forbidden not in public, f"citation_patterns 不得提供 {forbidden}"


# ── pattern 物件本身 ───────────────────────────────────────────────────────

def test_pattern_records_are_frozen():
    p = P.get("article")
    with pytest.raises(Exception):
        p.name = "changed"  # type: ignore[misc]


def test_get_rejects_unknown_names():
    with pytest.raises(KeyError, match="未知的 pattern"):
        P.get("no_such_pattern")


def test_pattern_names_are_stable_and_unique():
    names = P.pattern_names()
    assert len(names) == len(set(names))
    for required in (
        "article", "paragraph", "subparagraph",
        "article_paragraph", "article_added",
        "article_first_part", "paragraph_first_part",
    ):
        assert required in names


def test_patterns_compile_and_are_re_pattern():
    for p in P.PATTERNS:
        assert isinstance(p.regex, re.Pattern)


def test_zero_observed_count_is_explicit():
    """未在 survey 中單獨量化的結構必須記 0，而不是拿一個猜的數字。

    `article_subparagraph`（第X條第X款）在 survey 的表格裡沒有獨立數字 ——
    那 767 次是「項第款」片段。把那個數字借給它會讓 coverage 報告說謊。
    """
    d = P.documented_coverage()
    assert d["article_subparagraph"] == 0
    # 有實測數字的那些必須非 0
    assert all(
        v > 0 for k, v in d.items() if k != "article_subparagraph"
    )


# ── 空輸入 ────────────────────────────────────────────────────────────────

def test_empty_and_markerless_text_matches_nothing():
    """零匹配是合法結果，不拋錯。"""
    assert match("") == []
    assert match("沒有任何引用的判決書") == []
    assert match("　　") == []


def test_patterns_do_not_match_bare_numerals():
    """單純的數字不是引用。"""
    assert match("2468") == []
    assert match("第") == []
