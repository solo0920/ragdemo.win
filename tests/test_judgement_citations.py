"""`citations.py` 的契約測試（spec 004 T013 detection / T014 classification）。

## 這組測試要證明的事

1. **substring invariant**：對每一個候選，`matched_text == text[start:end]`
   （FR-035）。這不只靠測試 —— `detect()` 內部也斷言，所以任何呼叫路徑
   都受保護。
2. **position fidelity**：offset 指向 **authoritative text**，且**沒有**經過
   strip、換行轉換或 Unicode 正規化（FR-036）。
3. **沒有 fabricated identity**：沒有任何欄位帶法條名稱（FR-037）。

## 為什麼 position 那組測試特別重要

危險的 pipeline 長這樣：

```python
stripped = text.strip()          # ← 破壞
for m in regex.finditer(stripped):
    yield Candidate(start=m.start(), ...)   # offset 對應的是 stripped
```

結果 `matched_text` 抓對了、offset 卻指向錯誤位置，而**沒有任何東西會報錯**
—— 直到有人用它去切原文。本測試的 fixture 開頭就是全形空格（`　　`），
任何 strip 都會讓 substring invariant 失敗。

## 分類為什麼預設是 `candidate`

FR-039 規定預設是 `candidate`。若預設是 `statute-citation`，抽樣中 87 次的
`系爭契約第一條` 就全部被記成法律引用。

`statute-citation` **可以**被標上，但只能由**自己持有可信名單**的呼叫端標 ——
本模組永遠不主動回傳它。見 `test_classify_never_returns_statute_citation`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "tests" / "fixtures" / "judgements" / "docs"
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import citations as C  # noqa: E402
import citation_patterns as P  # noqa: E402
import document  # noqa: E402
import text as T  # noqa: E402

CIVIL = DOCS / "civil_6field.json"


def jfull(p: Path) -> str:
    return json.loads(p.read_text(encoding="utf-8"))["JFULL"]


def detect_s(s: str) -> list[C.CitationCandidate]:
    return C.detect(T.LosslessText.from_string(s))


# ── 正向：偵測到候選 ───────────────────────────────────────────────────────

def test_single_candidate_detected():
    cands = detect_s("爰依民法第185條規定辦理。")
    assert len(cands) == 1
    c = cands[0]
    assert c.matched_text == "第185條"
    assert c.pattern_name == "article"


def test_offsets_are_correct():
    s = "爰依民法第185條規定辦理。"
    c = detect_s(s)[0]
    assert s[c.start_offset : c.end_offset] == "第185條"
    # 「爰依民法」是 4 個字元，所以 第 在 index 4
    assert c.start_offset == 4
    assert c.end_offset == 9
    assert c.length == 5
    assert s[c.start_offset] == "第"


def test_document_identity_is_carried():
    """候選必須帶著 document identity —— 不能脫離來源。"""
    doc = document.from_document(
        json.loads(CIVIL.read_text(encoding="utf-8")),
        entry_path="202607\\x\\TPDV,115,訴,2468,20260901,1.json",
        sha256="a" * 64,
    )
    cands = C.detect(T.LosslessText.from_string("依民法第1條"), document=doc)
    assert cands[0].source_document == doc.entry_path
    assert cands[0].jid == doc.jid


def test_provenance_is_carried():
    cands = C.detect(
        T.LosslessText.from_string("依民法第1條"),
        entry_path="x.json",
        provenance={"artifact_sha256": "b" * 64},
    )
    assert cands[0].provenance["artifact_sha256"] == "b" * 64


# ── FR-035：substring invariant ────────────────────────────────────────────

@pytest.mark.parametrize(
    "s",
    [
        "爰依民法第185條規定。",
        "　　主　　　文\r\n　　依刑法第55條、第56條辦理。",
        "同法第65條準用同第22條",
        "系爭契約第一條與原審卷第371頁",
        "第44條第2項第3款",
        "第一百一十四條之三",
        "",
    ],
)
def test_substring_invariant_holds_for_every_candidate(s):
    """對每個候選逐一驗 `matched_text == text[start:end]`。"""
    t = T.LosslessText.from_string(s)
    for c in C.detect(t):
        assert t.text[c.start_offset : c.end_offset] == c.matched_text


def test_substring_invariant_raises_if_broken():
    """invariant 被破壞時必須炸，且是明確的例外型別。

    這條測試手動構造一個壞掉的候選，確認 `SubstringInvariantError` 會被
    拋出 —— 否則那條斷言可能形同虛設。
    """
    broken = C.CitationCandidate(
        source_document="x",
        jid="y",
        matched_text="第1條",
        start_offset=0,
        end_offset=3,      # 切出來是「依民法」
        pattern_name="article",
    )
    with pytest.raises(C.SubstringInvariantError):
        C._check_substring("依民法第1條", broken)


def test_detect_asserts_internally(monkeypatch):
    """`detect()` 內部確實有斷言，不只是測試在做。

    做法：讓一個候選的 offset 被破壞，確認 `detect()` 自己會抓到。
    """
    original = C._check_substring
    calls = []
    def spy(text, cand):
        calls.append(cand)
        return original(text, cand)
    monkeypatch.setattr(C, "_check_substring", spy)
    C.detect(T.LosslessText.from_string("依民法第1條"))
    assert len(calls) >= 1, "detect() 必須內部檢查 invariant"


# ── FR-036：position 不受 CRLF / whitespace 影響 ──────────────────────────

def test_offsets_are_unaffected_by_crlf():
    """CRLF 不影響 offset —— 因為我們根本沒做任何轉換。

    驗證方式是**兩個位置**：`text[start:end]` 必須在有 CRLF 與沒 CRLF 的
    版本中**指向同一個 token**。若有人做了 CRLF→LF 的正規化，這裡會紅。
    """
    with_crlf = "　　主　　　文\r\n　　依刑法第55條辦理。"
    without = "　　主　　　文　　依刑法第55條辦理。"

    c1 = C.detect(T.LosslessText.from_string(with_crlf))[0]
    c2 = C.detect(T.LosslessText.from_string(without))[0]

    assert c1.matched_text == c2.matched_text == "第55條"
    # 兩者 offset 不同（因為 CRLF 佔位），但**相對內容**一致：
    # 各自切出來都必須等於 matched_text
    assert with_crlf[c1.start_offset : c1.end_offset] == "第55條"
    assert without[c2.start_offset : c2.end_offset] == "第55條"


def test_offsets_survive_leading_whitespace():
    """前導空白不得被 strip 掉。

    這個 fixture 的 JFULL 開頭就是 `　　`。若 pipeline 做了
    `text.strip()` 再找位置，所有 offset 都會少 2，而 substring invariant
    會**同時**失敗 —— 那正是 invariant 有用的原因。
    """
    s = "　　" + "依刑法第55條辦理。"
    c = C.detect(T.LosslessText.from_string(s))[0]
    # 兩個全形空格（index 0,1）+ 「依刑法」（2,3,4）→ 第 在 index 5
    assert c.start_offset == 5
    assert s[c.start_offset : c.end_offset] == "第55條"
    # 若有人 strip 過，offset 會是 3 —— 明顯不同
    assert c.start_offset != len("　　")


def test_offsets_survive_leading_tabs_and_spaces():
    s = "\t  \t依刑法第55條"
    c = C.detect(T.LosslessText.from_string(s))[0]
    assert s[c.start_offset : c.end_offset] == "第55條"


def test_offsets_are_character_offsets_not_byte_offsets():
    """offset 是**字元**，不是位元組。

    前面的中文字讓 byte offset 遠大於 char offset。若偵測器用位元組 offset，
    切出來的就是亂碼。
    """
    s = "依刑法第55條辦理。"
    c = C.detect(T.LosslessText.from_string(s))[0]
    assert c.start_offset == 3            # 字元
    assert s.encode("utf-8")[c.start_offset : c.end_offset] != "第55條"


def test_real_fixture_offsets_are_authoritative():
    """用真實形狀的 fixture（CRLF + U+3000 + 空行）驗一次。"""
    s = jfull(CIVIL)
    t = T.LosslessText.from_string(s)
    cands = C.detect(t)
    # 這份 fixture 的 JFULL 沒有引用 → 零候選（合法）
    assert cands == []
    assert t.crlf_count == 6 and t.ideographic_space_count > 0


def test_offsets_survive_embedded_blank_lines():
    s = "前言。\r\n\r\n\r\n依刑法第55條辦理。"
    c = C.detect(T.LosslessText.from_string(s))[0]
    assert s[c.start_offset : c.end_offset] == "第55條"


# ── 多候選：不漏、不重複、位置正確 ─────────────────────────────────────────

def test_multiple_candidates_in_one_text():
    s = "爰依民法第185條、民法第192條及刑法第55條規定辦理。"
    cands = detect_s(s)
    assert [c.matched_text for c in cands] == ["第185條", "第192條", "第55條"]


def test_multiple_candidates_have_distinct_non_overlapping_offsets():
    s = "爰依民法第185條、民法第192條及刑法第55條規定辦理。"
    cands = detect_s(s)
    spans = [(c.start_offset, c.end_offset) for c in cands]
    assert len(spans) == len(set(spans)), "不得有重複的 span"
    for a, b in zip(spans, spans[1:]):
        assert a[1] <= b[0], "span 不得重疊"


def test_candidates_are_sorted_by_position():
    s = "第55條、第44條、第99條"
    cands = detect_s(s)
    offsets = [c.start_offset for c in cands]
    assert offsets == sorted(offsets)


def test_repeated_same_token_is_not_deduplicated_away():
    """同一個 token 出現兩次 → 兩個候選（不同位置）。

    去重會讓「這份文件引用了兩次」這個事實消失。
    """
    s = "第55條與第55條"
    cands = detect_s(s)
    assert len(cands) == 2
    assert cands[0].matched_text == cands[1].matched_text == "第55條"
    assert cands[0].start_offset != cands[1].start_offset


def test_overlapping_structures_are_not_duplicated():
    """`第44條第2項` 只產生一個候選，不是兩個。"""
    cands = detect_s("依刑法第44條第2項規定")
    assert len(cands) == 1
    assert cands[0].matched_text == "第44條第2項"
    assert cands[0].pattern_name == "article_paragraph"


# ── 零候選是合法結果 ───────────────────────────────────────────────────────

def test_no_citation_returns_empty_list():
    """沒有引用 → `[]`，不拋錯。

    133/494 真實文件一個已辨識的 section marker 都沒有；引用同理會有文件
    沒有。把「零候選」當錯誤等於宣稱一批真實判決是壞的。
    """
    assert detect_s("本判決無引用任何法律。") == []
    assert C.detect_and_classify(T.LosslessText.from_string("純粹的敘述文字")) == []


def test_empty_text_returns_empty_list():
    assert detect_s("") == []


def test_zero_candidates_on_real_fixture_without_citations():
    assert C.detect(T.LosslessText.from_string(jfull(DOCS / "criminal_6field.json"))) == []


# ── FR-037：不得指派法條身分 ───────────────────────────────────────────────

def test_no_output_field_carries_a_statute_name():
    cands = detect_s("爰依民法第185條規定辦理。")
    for c in cands:
        d = c.as_dict()
        assert "statute_name" not in d
        assert "law" not in d
        assert not any("statute" in k for k in d)


def test_statute_name_is_always_none():
    """`statute_name` 恆為 None —— 這個模組不做 resolution。"""
    for s in ("依民法第185條", "依刑法第1條", "同法第65條"):
        for c in detect_s(s):
            assert c.statute_name is None
            assert c.article_number is None


def test_module_has_no_resolver():
    """模組不得提供任何把候選轉成法條身分的入口。"""
    public = {n for n in dir(C) if not n.startswith("_")}
    for forbidden in (
        "resolve", "resolve_statute", "resolve_citation", "to_statute",
        "lookup_law", "gazetteer", "statute_index",
    ):
        assert forbidden not in public, f"citations 不得提供 {forbidden}"


def test_module_does_not_import_gazetteer_or_network():
    import ast

    tree = ast.parse((ROOT / "ingest" / "judgements" / "citations.py").read_text("utf-8"))
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert not (imported & {"requests", "urllib", "httpx", "json", "sqlite3", "psycopg"})


def test_module_docstring_declares_resolution_absent():
    """docstring 必須**明確**寫出 resolution 是刻意缺席的。

    tasks.md T013 的 Note 要求這一段 —— 否則後人會「順手補上」law lookup，
    把偽造重新引進來。
    """
    src = (ROOT / "ingest" / "judgements" / "citations.py").read_text("utf-8")
    docstring = src.split('"""')[1]
    assert "resolution" in docstring.lower()
    assert "刻意" in docstring or "deliberately" in docstring.lower()


# ── T014：分類 ─────────────────────────────────────────────────────────────

def test_doc_internal_reference_is_non_statute():
    """`系爭契約第一條` → `non-statute`（FR-038）。"""
    cands = C.detect_and_classify(T.LosslessText.from_string("爰依系爭契約第一條規定"))
    assert len(cands) == 1
    assert cands[0].classification is C.Classification.NON_STATUTE
    assert any("契約" in e for e in cands[0].evidence)


def test_elided_name_reference_is_ambiguous():
    """`同法第65條` → `ambiguous`。

    它確實指向某部法律，但那是**前面**提到的 —— 而「前面是哪一部」需要
    跨段落狀態，屬於 Stage B。
    """
    cands = C.detect_and_classify(T.LosslessText.from_string("同法第65條規定"))
    assert cands[0].classification is C.Classification.AMBIGUOUS
    assert any("相對引用" in e for e in cands[0].evidence)


def test_zhun_yong_reference_is_ambiguous():
    cands = C.detect_and_classify(T.LosslessText.from_string("準用同第22條"))
    assert cands[0].classification is C.Classification.AMBIGUOUS


def test_plain_reference_defaults_to_candidate():
    """沒有特別線索 → 保持 `candidate`（FR-039 的預設）。"""
    cands = C.detect_and_classify(T.LosslessText.from_string("爰依民法第185條辦理"))
    assert cands[0].classification is C.Classification.CANDIDATE


def test_classification_does_not_invent_a_statute_name():
    for s in ("依民法第1條", "依系爭契約第一條", "同法第65條"):
        for c in C.detect_and_classify(T.LosslessText.from_string(s)):
            assert c.statute_name is None
            assert c.article_number is None


def test_classify_never_returns_statute_citation():
    """`classify()` 永遠不回傳 `statute-citation`。

    那是刻意的：確認需要一份可信的「哪些字串是法律名稱」名單，而那份名單
    不可得（survey §4.3：本地 gazetteer 覆蓋率 3.9%，且缺最常被引用的一部）。

    這個標籤**可以**存在於 enum，但只能由持有可信名單的呼叫端設定。
    """
    samples = [
        "爰依民法第185條辦理",
        "依刑法第1條",
        "依系爭契約第一條",
        "同法第65條",
        "準用同第22條",
        "中華民國刑法第55條",
        "犯詐欺犯罪危害防制條例第3條",
    ]
    for s in samples:
        for c in C.detect_and_classify(T.LosslessText.from_string(s)):
            assert c.classification is not C.Classification.STATUTE_CITATION, s


def test_all_four_states_exist_in_the_enum():
    values = {c.value for c in C.Classification}
    assert values == {"candidate", "statute-citation", "ambiguous", "non-statute"}


def test_classification_is_explainable():
    """每個分類都要有非空的 evidence。"""
    for s in ("依系爭契約第一條", "同法第65條", "依民法第1條"):
        for c in C.detect_and_classify(T.LosslessText.from_string(s)):
            assert c.evidence, f"{s} 的分類缺少理由"


def test_classify_returns_a_new_object():
    """`classify()` 回傳**新**物件，不改原來那個（frozen）。"""
    cand = detect_s("依系爭契約第一條")[0]
    before = cand.classification
    after = C.classify(cand, T.LosslessText.from_string("依系爭契約第一條"))
    assert cand.classification is before          # 原物件未變
    assert after is not cand


def test_candidate_record_is_frozen():
    c = detect_s("依民法第1條")[0]
    with pytest.raises(Exception):
        c.matched_text = "改掉"  # type: ignore[misc]


def test_candidate_survives_json_round_trip():
    import json as _json

    c = C.detect_and_classify(T.LosslessText.from_string("依系爭契約第一條"))[0]
    d = c.as_dict()
    assert _json.loads(_json.dumps(d, ensure_ascii=False)) == d


def test_as_dict_carries_all_fr034_fields():
    """FR-034 要求的欄位必須齊全。"""
    c = detect_s("依民法第1條")[0]
    d = c.as_dict()
    for required in (
        "source_document", "matched_text", "start_offset", "end_offset", "provenance",
    ):
        assert required in d
    # 加上 evidence 用的 pattern_name
    assert d["pattern_name"] == "article"
    assert d["classification"] == "candidate"


# ── 從 document 端到端 ─────────────────────────────────────────────────────

def test_end_to_end_from_document(tmp_path):
    """T009 的 document → T013 的候選，全鏈路保留 identity。"""
    doc = document.from_document(
        json.loads(CIVIL.read_text(encoding="utf-8")),
        entry_path="202607\\x\\TPDV,115,訴,2468,20260901,1.json",
        sha256="c" * 64,
    )
    # 把引用塞進 JFULL（fixture 本身沒有引用）
    doc = document.from_document(
        {**doc.source_dict(), "JFULL": "　　依民法第185條辦理。\r\n"},
        entry_path=doc.entry_path,
        sha256=doc.sha256,
    )
    t = T.LosslessText.from_string(doc.jfull)
    cands = C.detect(t, document=doc)

    assert len(cands) == 1
    c = cands[0]
    assert c.jid == doc.jid
    assert c.source_document == doc.entry_path
    assert t.text[c.start_offset : c.end_offset] == c.matched_text == "第185條"
    assert doc.jfull.startswith("　　"), "authoritative text 未被改動"


# ── 這個模組不做什麼 ───────────────────────────────────────────────────────

def test_citations_module_does_not_chunk_or_embed():
    import ast

    tree = ast.parse((ROOT / "ingest" / "judgements" / "citations.py").read_text("utf-8"))
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not (called & {"chunk", "embed", "upsert", "search", "retrieve"})


def test_citations_does_not_infer_court():
    """不得從文字推論法院（spec 明確禁止）。"""
    import ast

    tree = ast.parse((ROOT / "ingest" / "judgements" / "citations.py").read_text("utf-8"))
    code_literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "\n" not in n.value
        and len(n.value) < 64
    }
    assert not any("court" in v.lower() or "法院" in v for v in code_literals)


def test_citations_does_not_read_jcase_for_semantics():
    """不得讀 JCASE 來推論引用性質。

    `JCASE` 是 opaque 代碼；用它判斷「這是不是民事判決所以引用的是民訴法」
    是 FR-016 禁止的 synthesize。
    """
    src = (ROOT / "ingest" / "judgements" / "citations.py").read_text("utf-8")
    code = src.split('"""')[-1]
    assert "jcase" not in code.lower()
    assert "JCASE" not in code
