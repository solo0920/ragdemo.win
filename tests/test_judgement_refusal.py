"""找不到支持來源時必須**結構性拒絕**（spec 004 T020 / FR-020 / SC-005）。

## 這份測試的核心主張

> 「沒有支持這個問題的原文」是一個**結果**，不是錯誤，也不是讓模型用常識
> 補一段的時機。

`answer_judgments([])` 回傳一個明確標示 `no_match: True` 的回應，而不是：
* 拋例外（那會讓呼叫端只能選擇 500 或吞掉）
* 空字串（使用者看不出是「沒找到」還是「壞了」）
* 一段模型生成的概略法律說明（那是偽造）

## acceptance 的兩條硬性要求

FR-020 的 acceptance 寫得很具體：

1. 回應含 **0** statute text
2. 回應含 **0** case numbers

這兩條可以靜態檢查（regex 掃條號與案號模式），而它們正是「拒絕時不能偷渡
法律結論」的可執行版本。下面有專門的測試。

## 為什麼要一個「策展過的無答案問題集」

隨機問題可能**剛好**命中檢索。要驗證拒絕路徑，需要一組「在判決 corpus 裡
確定找不到」的問題。它們是**測試資料**，不是產品內容。
"""
from __future__ import annotations

import json
import re
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
ENTRY = "202607\\臺灣臺北地方法院民事\\TPDV,115,訴,2468,20260901,1.json"


# ── 策展的無答案問題集 ─────────────────────────────────────────────────────
#
# 每一題都刻意落在判決 corpus **之外**：
#   * 與判決無關的領域（天氣、食譜）
#   * 需要法律結論而非原文的問題（「我該不該告他」）
#   * 具體到某部法條但 corpus 沒有該判決的問題
#
# ⚠️ 這些問題**不是**產品文案，是測試資料。它們的存在是為了讓「找不到」
# 這條路徑有東西可測。
NO_ANSWER_QUESTIONS = (
    "今天天氣如何？",
    "請提供一份滷肉飯的食譜。",
    "我應該告他嗎？",
    "這份判決的法官是誰？",
    "股票的合理本益比是多少？",
    "請幫我寫一封存證信函。",
)


def make_doc() -> document.JudgmentDocument:
    return document.from_document(
        json.loads(CIVIL.read_text(encoding="utf-8")),
        entry_path=ENTRY,
        sha256="a" * 64,
    )


def hits_for(doc, *, size: int = 100) -> list[dict]:
    out = []
    for c in CH.chunk_document(doc, chunk_size=size):
        out.append({
            "id": c.point_id, "score": 0.9,
            "payload": {
                "entry_path": doc.entry_path, "jid": doc.jid,
                "chunk_index": c.chunk_index,
                "start_offset": c.start_offset, "end_offset": c.end_offset,
                "content_hash": c.sha256,
                "jyear": doc.jyear, "jdate": doc.jdate, "jcase": doc.jcase,
            },
        })
    out.sort(key=retrieve.judgment_sort_key)
    return out


# ── 零結果 → 明確拒絕，不是例外 ───────────────────────────────────────────

@pytest.mark.parametrize("question", NO_ANSWER_QUESTIONS)
def test_no_hits_produces_a_refusal_not_an_exception(question):
    r = rag.answer_judgments(question, [])
    assert r["ok"] is True
    assert r["no_match"] is True
    assert isinstance(r["answer"], str) and r["answer"]


def test_refusal_is_not_presented_as_success():
    """拒絕必須與成功回答**在結構上可區分**。

    acceptance：「the refusal is not presented as a successful answer」。
    做法：`no_match=True`、`confidence="no_source"`、`quoted_blocks=[]`、
    `citations=[]`。任何一個單獨看都不夠 —— 四個一起才讓「這是拒絕」不可能
    被誤讀。
    """
    r = rag.answer_judgments("今天天氣如何？", [])
    assert r["no_match"] is True
    assert r["confidence"] == "no_source"
    assert r["quoted_blocks"] == []
    assert r["citations"] == []


def test_refusal_does_not_claim_a_source_was_found():
    r = rag.answer_judgments("今天天氣如何？", [])
    assert "找不到" in r["answer"] or "沒有" in r["answer"]


# ── FR-020 acceptance：0 statute text、0 case numbers ─────────────────────

@pytest.mark.parametrize("question", NO_ANSWER_QUESTIONS)
def test_refusal_contains_zero_statute_text(question):
    """拒絕訊息不得含任何法條文字。

    「找不到資料，但依民法第184條你可能可以請求損害賠償」—— 那句話裡的
    法條文字會讓使用者以為系統查到了什麼。拒絕就只能是拒絕。
    """
    r = rag.answer_judgments(question, [])
    assert not re.search(r"第\s*[0-9〇零一二三四五六七八九十百千]+\s*條", r["answer"])


@pytest.mark.parametrize("question", NO_ANSWER_QUESTIONS)
def test_refusal_contains_zero_case_numbers(question):
    """拒絕訊息不得含案號。"""
    r = rag.answer_judgments(question, [])
    # 判決案號的形狀：XXX,115,訴,2468,... 或「訴字第2468號」
    assert not re.search(r"\d{2,3},\d+,\S+,\d+", r["answer"])
    assert not re.search(r"[字第]\s*第?\s*\d+\s*號", r["answer"])


def test_refusal_does_not_name_a_court():
    """拒絕訊息不得出現法院名。

    這條與 FR-015 的矛盾有關（見 retrieve.py 的說明）：source 沒有 court
    欄位，所以拒絕時更不該**生成**一個法院名。
    """
    r = rag.answer_judgments("我應該告他嗎？", [])
    for court in ("地方法院", "高等法院", "最高法院", "臺北地院", "板橋地院"):
        assert court not in r["answer"]


def test_refusal_does_not_give_a_legal_conclusion():
    """拒絕不得給法律結論。

    acceptance 說 "with no legal conclusion"。檢查方式是確認它沒有出現
    「你應該」「建議你」「構成」「違法」這類斷言語句。
    """
    r = rag.answer_judgments("我應該告他嗎？", [])
    for phrase in ("你應該", "建議你", "構成", "違法", "可以請求", "得請求", "應負"):
        assert phrase not in r["answer"], f"拒絕含法律結論：{phrase}"


def test_refusal_is_constant_not_question_dependent():
    """拒絕訊息**不隨問題變動**。

    若它會依問題拼字，那就有一個「從問題生成文字」的路徑 —— 而那條路徑遲早
    會被擴充成生成答案。常數訊息沒有那個空間。
    """
    answers = {rag.answer_judgments(q, [])["answer"] for q in NO_ANSWER_QUESTIONS}
    assert len(answers) == 1
    assert answers == {rag.JUDGMENT_NO_SOURCE_ANSWER}


def test_refusal_constant_is_the_only_thing_returned():
    r = rag.answer_judgments("任意問題", [])
    assert r["answer"] == rag.JUDGMENT_NO_SOURCE_ANSWER


# ── 有 hits 但無法引用 → 也是拒絕 ──────────────────────────────────────────

def test_hits_without_source_text_is_also_a_refusal():
    """有檢索結果，但原文不在手上 → 仍然拒絕，不編造。

    這與「零 hits」是不同的情況，但正確反應相同：沒有原文就沒有引用。
    """
    doc = make_doc()
    r = rag.answer_judgments(
        "q", hits_for(doc), jfull_by_entry={"完全不同的路徑.json": "別的內容"}
    )
    assert r["no_match"] is True
    assert r["quoted_blocks"] == []
    assert r["answer"] == rag.JUDGMENT_NO_SOURCE_ANSWER


def test_hits_without_source_text_still_has_zero_statute_text():
    doc = make_doc()
    r = rag.answer_judgments(
        "q", hits_for(doc), jfull_by_entry={"other.json": "x"}
    )
    assert not re.search(r"第\s*[0-9〇零一二三四五六七八九十百千]+\s*條", r["answer"])


# ── 有支持來源 → 不是拒絕 ─────────────────────────────────────────────────

def test_supported_question_is_not_a_refusal():
    """有原文可引用時，`no_match` 必須是 False。

    這條防止「一律拒絕」—— 一個永遠拒絕的系統也會通過上面所有測試。
    """
    doc = make_doc()
    r = rag.answer_judgments(
        "再審之訴", hits_for(doc), jfull_by_entry={doc.entry_path: doc.jfull}
    )
    assert r["no_match"] is False
    assert r["confidence"] == "verbatim"
    assert r["quoted_blocks"]
    assert r["answer"] != rag.JUDGMENT_NO_SOURCE_ANSWER


def test_supported_answer_quotes_the_source_verbatim():
    doc = make_doc()
    r = rag.answer_judgments(
        "再審之訴", hits_for(doc), jfull_by_entry={doc.entry_path: doc.jfull}
    )
    for q in r["quoted_blocks"]:
        v = q["view"]
        assert q["text"] == doc.jfull[v["start_offset"] : v["end_offset"]]


# ── 檢索層的零結果 ────────────────────────────────────────────────────────

def test_search_returns_empty_list_not_exception_for_zero_limit():
    """`limit <= 0` → `[]`，不拋錯。"""
    import asyncio

    async def run():
        return await retrieve.search_judgments("q", [0.0] * 1024, limit=0)

    assert asyncio.run(run()) == []


def test_refusal_requires_no_retrieval_at_all():
    """拒絕路徑不得先嘗試檢索或生成。

    靜態驗證：`answer_judgments` 的第一個分支（`if not hits`）在任何
    gateway / LLM 呼叫之前就 return。用 AST 確認 `answer_judgments` 內
    沒有任何 `await`。
    """
    import ast

    tree = ast.parse((ROOT / "backend" / "app" / "rag.py").read_text("utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "answer_judgments")
    awaits = [n for n in ast.walk(fn) if isinstance(n, ast.Await)]
    assert not awaits, "判決回答（含拒絕路徑）不得 await 任何東西"


def test_refusal_message_is_a_constant_not_built_from_question():
    """拒絕訊息是模組常數，不是 f-string。

    若它是 f-string，就有一個「把問題插進回答」的位置 —— 那條路徑會演化成
    生成。常數沒有那個位置。
    """
    import ast

    tree = ast.parse((ROOT / "backend" / "app" / "rag.py").read_text("utf-8"))
    assign = next(
        n for n in tree.body
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "JUDGMENT_NO_SOURCE_ANSWER" for t in n.targets)
    )
    assert isinstance(assign.value, ast.Constant)
    assert isinstance(assign.value.value, str)


# ── 這個模組不做的事 ───────────────────────────────────────────────────────

def test_refusal_does_not_call_an_llm():
    import ast

    tree = ast.parse((ROOT / "backend" / "app" / "rag.py").read_text("utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "answer_judgments")
    called = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not (called & {"generate", "_jev_verify", "chat", "completion"})


def test_refusal_does_not_resolve_citations():
    """拒絕路徑不得嘗試解析引用。

    T012–T014 建立了 candidate ≠ resolved。拒絕路徑連 candidate 都不該碰。
    """
    import ast

    tree = ast.parse((ROOT / "backend" / "app" / "rag.py").read_text("utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "answer_judgments")
    called = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not (called & {"detect", "detect_and_classify", "classify", "resolve"})
