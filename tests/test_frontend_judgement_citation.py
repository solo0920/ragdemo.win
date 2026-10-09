"""判決引用在前端的呈現（spec 004 T022）。

## 這份測試的核心主張

> 判決原文與它的來源標示必須**視覺上分離**，而且來源標示只能使用 source
> 實際提供的欄位 —— 不得推論法院。

這是 source-level 靜態測試，因為壞掉的是「原始碼長什麼樣」（例如有人把
`court` 寫進 citation），不是執行期輸入能測的。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "src" / "routes" / "+page.svelte"


def _code_only(path: Path) -> str:
    """去掉註解，只留真正會執行的程式碼（同 test_frontend_hosts.py）。"""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\{/\*.*?\*/\}", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return text


CODE = _code_only(PAGE)


def _judgment_span(code: str) -> str:
    """取出判決引用區段的原始碼（從 `judgmentBlocks` 到它結束）。"""
    start = code.index("function judgmentBlocks")
    # 找到第一個 `{:else if verbatimQuote(result)}` 之前，那是判決區段的結尾
    marker = "{:else if verbatimQuote(result)}"
    end = code.index(marker, start)
    return code[start:end]


# ── 基本存在 ───────────────────────────────────────────────────────────────


def test_judgment_rendering_function_exists():
    assert "function judgmentBlocks" in CODE, "需要 judgmentBlocks() 來識別判決回答"


def test_judgment_branch_uses_quoted_blocks():
    span = _judgment_span(CODE)
    assert "quoted_blocks" in span, "判決區段必須 render quoted_blocks"
    assert "q.text" in span, "必須顯示原文"
    assert "q.citation" in span, "必須顯示 citation"


def test_judgment_branch_is_triggered_by_verbatim_confidence():
    span = _judgment_span(CODE)
    assert "r.confidence !== 'verbatim'" in span, "判決分支必須由 verbatim confidence 觸發"


# ── 不推論法院 ─────────────────────────────────────────────────────────────


def test_judgment_citation_does_not_use_court():
    """判決 citation 不得使用 / 推論法院。"""
    span = _judgment_span(CODE)
    assert "court" not in span.lower(), "判決區段不得出現 court（source 沒有這個欄位）"


def test_judgment_citation_uses_source_fields_only():
    """來源標示只能使用 source 實際提供的欄位：jid / jdate / offset。"""
    span = _judgment_span(CODE)
    # citation 由後端 `rag.judgment_citation()` 組成，前端直接顯示 q.citation。
    # 這條確保前端沒有再把 q.citation 拆開重組、加入推論欄位。
    assert "q.citation" in span
    # 沒有試圖從 JFULL line 0 或 JID 位置解析法院
    for forbidden in ("split(',')", "地方法院", "高等法院", "最高法院"):
        assert forbidden not in span, f"判決區段不得進行法院推論：{forbidden}"


# ── 視覺分離 ───────────────────────────────────────────────────────────────


def test_judgment_quote_is_visually_distinct():
    """原文與 citation 必須有不同的 CSS class，讀者能區分兩者。"""
    assert ".judgment-block" in CODE
    assert ".judgment-text" in CODE
    assert ".judgment-cite" in CODE


def test_judgment_text_preserves_whitespace():
    """.judgment-text 必須用 white-space: pre-wrap 保留 CRLF / U+3000。"""
    code = CODE
    m = re.search(r"\.judgment-text\s*\{([^}]*)\}", code, re.S)
    assert m, "找不到 .judgment-text 的樣式"
    assert "white-space: pre-wrap" in m.group(1), "必須保留原文空白與換行"


# ── 不與 laws 分支混淆 ─────────────────────────────────────────────────────


def test_judgment_branch_does_not_override_laws_verbatim_quote():
    """laws 的 `verbatimQuote(result)` 分支必須保留。"""
    assert "verbatimQuote(result)" in CODE
    assert ".law-no" in CODE
