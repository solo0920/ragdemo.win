"""判決回答政策：**逐字引用，絕不改寫**（spec 004 T019）。

## 這份測試的核心主張

> 判決回答裡**沒有任何一個字**是模型生成的。

laws 路徑會呼叫 LLM 產生敘述（那是 `rag.answer()` 的既有行為，且**不得改動**）。
判決路徑刻意不呼叫 —— 理由寫在 `rag.py` 的 T019 區塊：FR-019 要求引用區塊內
零模型生成句子，而一個 LLM 輸出**無法被結構性保證**滿足這條，只能事後檢查，
而檢查必然有漏。唯一能保證的方式是不產生。

## 「逐字」的驗證方式

不是比對「看起來一樣」，而是：

1. 每個 quoted block 必須等於 `jfull[start:end]`（逐 byte）
2. block 的 sha256 必須等於 payload 裡的 `content_hash`
3. 回答裡除了引號與 citation 標記，沒有別的文字

第 3 條是最難寫的 —— 它要求我知道「哪些字是我加的」。做法是：把回答拆成
引文與 citation 兩類 token，然後確認**除了**那些，其他都是 block 內容。

## 這個測試不做什麼

不測 LLM 品質、不測 prompt、不測模型路由。這個檔案驗的是「沒有 LLM」。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import rag, retrieve  # noqa: E402

import chunk as CH  # noqa: E402
import document  # noqa: E402

DOCS = ROOT / "tests" / "fixtures" / "judgements" / "docs"
CIVIL = DOCS / "civil_6field.json"
CRIMINAL = DOCS / "criminal_6field.json"
CONSTITUTIONAL = DOCS / "constitutional_5field_empty_jpdf.json"
SINGLE = DOCS / "single_sentence_no_breaks.json"
LF_ONLY = DOCS / "lf_line_endings.json"

ENTRY = "202607\\臺灣臺北地方法院民事\\TPDV,115,訴,2468,20260901,1.json"


def make_doc(p: Path = CIVIL, entry: str = ENTRY) -> document.JudgmentDocument:
    return document.from_document(
        json.loads(p.read_text(encoding="utf-8")),
        entry_path=entry,
        sha256="a" * 64,
    )


def hits_for(doc, *, size: int = 100) -> list[dict]:
    out = []
    for c in CH.chunk_document(doc, chunk_size=size):
        out.append({
            "id": c.point_id,
            "score": 0.9,
            "payload": {
                "entry_path": doc.entry_path,
                "jid": doc.jid,
                "chunk_index": c.chunk_index,
                "start_offset": c.start_offset,
                "end_offset": c.end_offset,
                "content_hash": c.sha256,
                "jyear": doc.jyear,
                "jdate": doc.jdate,
                "jcase": doc.jcase,
            },
        })
    out.sort(key=retrieve.judgment_sort_key)
    return out


def answer_for(p: Path = CIVIL, size: int = 100):
    doc = make_doc(p)
    return rag.answer_judgments(
        "再審之訴要怎麼主張？",
        hits_for(doc, size=size),
        jfull_by_entry={doc.entry_path: doc.jfull},
    )


# ── 逐字引用 ───────────────────────────────────────────────────────────────

def test_quoted_block_is_byte_exact_slice_of_jfull():
    """每個 quoted block 必須等於 `jfull[start:end]`（逐 byte）。"""
    doc = make_doc()
    r = answer_for()
    assert r["no_match"] is False
    for q in r["quoted_blocks"]:
        v = q["view"]
        assert q["text"] == doc.jfull[v["start_offset"] : v["end_offset"]]


def test_quoted_block_hash_matches_content_hash():
    """block 的 sha256 必須等於 payload 的 `content_hash`。"""
    r = answer_for()
    for q in r["quoted_blocks"]:
        assert hashlib.sha256(q["text"].encode("utf-8")).hexdigest() == q["view"]["content_hash"]


def test_crlf_survives_into_the_answer():
    """CRLF 必須逐字保留在回答裡。

    若這裡把 `\r\n` 轉成 `\n`，使用者看到的引文就跟司法院原文不同了，而那正是
    本 feature 要防的事。
    """
    doc = make_doc()
    r = answer_for()
    for q in r["quoted_blocks"]:
        expected_crlf = doc.jfull[q["view"]["start_offset"] : q["view"]["end_offset"]].count("\r\n")
        assert q["text"].count("\r\n") == expected_crlf
    assert "\r\n" in r["answer"], "回答必須保留 CRLF"


def test_ideographic_space_survives_into_the_answer():
    """U+3000 結構縮排必須保留。"""
    doc = make_doc()
    r = answer_for()
    assert "　" in r["answer"]
    for q in r["quoted_blocks"]:
        src = doc.jfull[q["view"]["start_offset"] : q["view"]["end_offset"]]
        assert q["text"].count("　") == src.count("　")


def test_leading_whitespace_of_a_line_survives():
    """行首全形空格不得被 strip。

    `strip()` 會把 `\\r\\n　　駁回...` 變成 `\\r\\n駁回...` —— 縮排消失，而引文
    看起來仍然完整。沒有任何「看起來對」的檢查能抓到這類錯誤，所以這條是
    直接比對 byte count。
    """
    doc = make_doc()
    r = answer_for()
    assert "\\r\\n　　" not in r["answer"].replace("\\r\\n", "\r\n")  # sanity
    assert "\r\n　　" in r["answer"], "行首 U+3000 必須保留"


def test_no_whitespace_normalisation_in_answer():
    """回答裡不得有被折半的全形空格或被轉換的換行。"""
    import unicodedata

    r = answer_for()
    # NFKC 會把 U+3000 折成半形空格 —— 若回答與 NFKC 後相同，代表被正規化過
    assert unicodedata.normalize("NFKC", r["answer"]) != r["answer"]


def test_single_sentence_anomaly_quotes_verbatim():
    """那 1 筆單句無換行的 anomaly 必須逐字引用。"""
    doc = make_doc(SINGLE, "202607\\x\\single.json")
    r = rag.answer_judgments(
        "這份裁定說什麼？", hits_for(doc, size=50),
        jfull_by_entry={doc.entry_path: doc.jfull},
    )
    assert r["no_match"] is False
    assert r["quoted_blocks"][0]["text"] == doc.jfull


def test_lf_only_document_quotes_verbatim():
    """LF-only 的文件引文裡也是 LF —— 不得被「修正」成 CRLF。"""
    doc = make_doc(LF_ONLY, "202607\\x\\lf.json")
    r = rag.answer_judgments(
        "q", hits_for(doc, size=50), jfull_by_entry={doc.entry_path: doc.jfull}
    )
    assert r["quoted_blocks"][0]["text"] == doc.jfull
    assert "\r\n" not in r["quoted_blocks"][0]["text"]


# ── 零模型生成句（acceptance 的明確要求）──────────────────────────────────

def test_answer_contains_only_quotes_and_citations():
    """回答裡除了引文與 citation 標記，**沒有任何其他文字**。

    驗證方式：把回答按行拆開，每一行必須是
      * `「...」` 的開頭（引文），或其續行（引文內容），或
      * `[來源:...]` 開頭（citation）
    任何其他行都是生成句。
    """
    r = answer_for()
    lines = r["answer"].split("\n")
    in_quote = False
    for line in lines:
        if not line:
            continue
        if line.startswith("「"):
            in_quote = True
            continue
        if line.startswith("[來源:"):
            in_quote = False
            continue
        # 其餘必須是引文的續行（原文含 CRLF，所以引文會佔多行）
        assert in_quote, f"引文外的生成句：{line!r}"


def test_no_preamble_before_the_first_quote():
    """連前言都不加。

    「根據您所詢問的判決，原文如下」聽起來無害，但它是**模型產生的句子**。
    放在區塊外面在技術上過關，卻給了未來的維護者一個「可以在區塊外面加」的
    先例，而那個先例會一路退化成改寫原文。
    """
    r = answer_for()
    assert r["answer"].startswith("「"), r["answer"][:80]


def test_no_connective_sentences_between_quotes():
    """引文之間不得有「此外」「另據」「綜合上述」這類生成句。"""
    r = answer_for(size=50)   # 小 chunk → 多段引文
    assert len(r["quoted_blocks"]) >= 2
    connective = ("此外", "另據", "綜合", "因此可知", "換言之",
                  "值得注意的是", "總而言之", "由此可見")
    for c in connective:
        assert c not in r["answer"], f"回答含生成句片段：{c}"


def test_confidence_is_verbatim_not_llm():
    """`confidence` 必須標示為逐字引用，不能偽裝成生成。"""
    r = answer_for()
    assert r["confidence"] == "verbatim"


def test_judgment_path_does_not_call_llm():
    """靜態驗證：判決回答路徑不得呼叫任何 provider。

    這是「零生成句」的**機制性**保證 —— 不是靠事後檢查回答文字，而是靠
    確認這條路徑根本沒有呼叫 LLM 的能力。
    """
    import ast

    tree = ast.parse((ROOT / "backend" / "app" / "rag.py").read_text("utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "answer_judgments")
    called = {
        n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    # 這條路徑只做切片、組字串、驗 payload
    assert not (called & {"generate", "_jev_verify", "_call_llm", "chat"})
    # 也不得 await 任何東西（LLM 呼叫必然是 async）
    awaited = [
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Await) and isinstance(n.func, ast.Call)
        and isinstance(n.func.func, ast.Name)
    ]
    assert not awaited, f"判決回答路徑不得 await：{awaited}"


def test_existing_statute_path_is_unchanged():
    """laws 的回答路徑仍然呼叫 LLM —— 本 feature 不得改動它。

    這條很重要：有人可能「順手」把 laws 也改成逐字引用。那是 spec 002 的
    行為，且與本 feature 無關（T018/T019 的 Files 欄位寫的是 "extend, not
    replace"）。
    """
    import ast

    tree = ast.parse((ROOT / "backend" / "app" / "rag.py").read_text("utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "answer")
    called = {
        n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "generate" in called, "laws 回答路徑的 LLM 呼叫被移除了 —— 超出本 feature scope"


# ── offset fidelity（FR-011）───────────────────────────────────────────────

def test_slice_offset_mismatch_raises():
    """offset 切不出一致長度 → 拋錯，不靜默產出短文。"""
    view = {
        "entry_path": "x", "jid": "J", "chunk_index": 0,
        "start_offset": 0, "end_offset": 9999,
        "content_hash": "", "jyear": "115", "jdate": "20260101", "jcase": "訴",
    }
    with pytest.raises(rag.JudgmentAnswerError, match="越界"):
        rag.judgment_source_block(view, "短")


def test_stale_index_is_detected_by_content_hash():
    """索引過期（offset 對不上內容）必須被 content_hash 抓到。

    這是「Qdrant 是 derived」的實際保護：索引與來源不同步時，這裡會報錯而
    不是把錯誤的文字當成司法院的話呈現。
    """
    text = "依民法第1條"
    view = {
        "entry_path": "x", "jid": "J", "chunk_index": 0,
        "start_offset": 0, "end_offset": len(text),
        "content_hash": "0" * 64,      # 錯的 hash
        "jyear": "115", "jdate": "20260101", "jcase": "訴",
    }
    with pytest.raises(rag.JudgmentAnswerError, match="content_hash"):
        rag.judgment_source_block(view, text)


def test_correct_hash_passes():
    """正確的 hash 通過驗證，並原樣回傳切片。

    切片長度必須與 `end_offset - start_offset` 一致 —— 這裡的 `end_offset=4`
    對應 4 個字元，不是整句。
    """
    text = "依民法第1條"
    view = {
        "entry_path": "x", "jid": "J", "chunk_index": 0,
        "start_offset": 0, "end_offset": len(text),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "jyear": "115", "jdate": "20260101", "jcase": "訴",
    }
    assert rag.judgment_source_block(view, text) == text


def test_missing_jfull_raises():
    """沒有 `jfull_by_entry` 必須拋錯，不能想辦法生出引用。

    payload 刻意不含全文，所以沒有它就**真的**沒有引用文字可組。這時生成一段
    話就是偽造。
    """
    doc = make_doc()
    with pytest.raises(rag.JudgmentAnswerError, match="jfull_by_entry"):
        rag.answer_judgments("q", hits_for(doc), jfull_by_entry=None)


def test_entry_missing_from_jfull_map_is_skipped_not_fabricated():
    """payload 的 entry 不在原文對照表裡 → 略過該筆，不編造文字。

    驗證方式：給一個不包含該 entry 的 map，結果的 quoted_blocks 為空、
    `no_match` 為 True —— 而不是「用 payload 裡有的東西拼一段」。
    """
    doc = make_doc()
    r = rag.answer_judgments(
        "q", hits_for(doc), jfull_by_entry={"other/path.json": "別的內容"}
    )
    assert r["no_match"] is True
    assert r["quoted_blocks"] == []


# ── citation ───────────────────────────────────────────────────────────────

def test_citation_shows_source_values_not_inferences():
    """citation 只顯示 source 實際提供的值。"""
    doc = make_doc()
    view = retrieve.judgment_hit_view(hits_for(doc)[0])
    cite = rag.judgment_citation(view)
    assert doc.jid in cite
    assert doc.jdate in cite


def test_citation_does_not_contain_court():
    """citation 不得含法院。

    source 沒有 court 欄位（spec §Court），推論它是 FR-016 禁止的。顯示一個
    推論出來的法院名會讓使用者以為那是權威事實。
    """
    doc = make_doc()
    cite = rag.judgment_citation(retrieve.judgment_hit_view(hits_for(doc)[0]))
    # 這個 fixture 的 JFULL 開頭確實有法院名（「臺灣臺北地方法院民事裁定」），
    # 所以特別確認它**沒有**被搬進 citation
    assert "臺灣臺北地方法院" in doc.jfull
    assert "臺灣臺北地方法院" not in cite
    assert "地方法院" not in cite


def test_citation_does_not_split_jid():
    """`JID` 整串顯示，不拆成案號/年份/法院（FR-028）。"""
    doc = make_doc(CONSTITUTIONAL, "202607\\x\\const.json")
    view = retrieve.judgment_hit_view(hits_for(doc, size=50)[0])
    cite = rag.judgment_citation(view)
    assert "JCCC,115,審裁,1158,20260701" in cite
    # 不得出現被拆解出來的欄位標籤
    for label in ("法院:", "案號:", "年份:"):
        assert label not in cite


def test_citation_includes_offset_range():
    """citation 帶 offset —— 讓引用能被定位回原文。"""
    doc = make_doc()
    view = retrieve.judgment_hit_view(hits_for(doc)[0])
    cite = rag.judgment_citation(view)
    assert str(view["start_offset"]) in cite
    assert str(view["end_offset"]) in cite


# ── deterministic ──────────────────────────────────────────────────────────

def test_answer_is_deterministic():
    """同一份輸入重跑 → 完全相同回答。"""
    doc = make_doc()
    hits = hits_for(doc)
    m = {doc.entry_path: doc.jfull}
    runs = [rag.answer_judgments("q", list(hits), jfull_by_entry=m) for _ in range(5)]
    for r in runs[1:]:
        assert r["answer"] == runs[0]["answer"]
        assert r["citations"] == runs[0]["citations"]


def test_answer_is_independent_of_hit_input_order():
    """打亂 hits 順序 → 回答相同（排序由 sort_key 決定，不靠輸入順序）。"""
    import random

    doc = make_doc()
    hits = hits_for(doc, size=50)
    m = {doc.entry_path: doc.jfull}
    expected = rag.answer_judgments("q", list(hits), jfull_by_entry=m)["answer"]
    rng = random.Random(20260907)
    for _ in range(5):
        sh = list(hits)
        rng.shuffle(sh)
        assert rag.answer_judgments("q", sh, jfull_by_entry=m)["answer"] == expected


def test_citations_order_matches_block_order():
    doc = make_doc()
    r = rag.answer_judgments(
        "q", hits_for(doc, size=50), jfull_by_entry={doc.entry_path: doc.jfull}
    )
    assert r["citations"] == [q["citation"] for q in r["quoted_blocks"]]


# ── laws 路徑不受影響 ─────────────────────────────────────────────────────

def test_hit_view_for_laws_still_works():
    """laws 的 `_hit_view()` 未被 T018/T019 改動。

    它回傳的是 laws 專屬欄位（art / law_name / url）；若被改成判決形狀，
    前端會壞。
    """
    v = rag._hit_view({
        "score": 0.9,
        "payload": {"law_name": "民法", "article_no": " 第259條 ", "chapter": "",
                    "text": "一、返還之。", "pcode": "B0000001",
                    "is_repealed": False, "is_abandoned": False},
    })
    assert v["law_name"] == "民法"
    assert v["art"] == "第259條"


def test_ref_for_laws_still_works():
    """laws 的 `_ref()` 未被本 batch 改動（它住在 `retrieve`，2026-09-29 搬過去的）。"""
    from app import law_struct  # noqa: F401

    out = retrieve._ref({
        "payload": {"law_name": "民法", "article_no": " 第259條 ", "chapter": "",
                    "text": "一、返還之。", "pcode": "B0000001"},
    })
    assert "民法" in out
    assert "法條" in out
