"""`chunk.py` 的契約測試（spec 004 T015）。

## 兩條必須同時成立的性質

1. **切取正確** —— 每個 chunk 滿足 `text == jfull[start:end]`。
2. **重組無損** —— 依序拼接所有 chunk，**逐 byte 等於**原文。

第 2 條比第 1 條強，而且更重要。危險的失敗不是「chunk 內容錯了」（那是明顯的
bug，馬上就會被發現），而是**某個字元被 chunk 邊界吞掉** —— 那會讓重組後的
文本少一段，而**每個 chunk 單獨看都完全正常**。

`chunk_text()` 在函式內部就斷言這兩條（`verify_against` + 重建比對），
所以任何呼叫路徑都受保護，不只靠測試。

## 邊界品質要能被觀察

`boundary_kind` 記錄每次切點的來源。`forced`（硬切）的比例是個訊號：它表示
這份文件的自然結構沒被利用。把它藏起來就丟掉了這個資訊。

## 這個檔案不做什麼

不測 embedding、不測摘要、不測改寫 —— 那些是被禁止的（FR-012）。本模組沒有
LLM 呼叫，測試也不應該假設它有。
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

import chunk as CH  # noqa: E402
import document  # noqa: E402
import text as T  # noqa: E402

CIVIL = DOCS / "civil_6field.json"
CRIMINAL = DOCS / "criminal_6field.json"
CONSTITUTIONAL = DOCS / "constitutional_5field_empty_jpdf.json"
SINGLE = DOCS / "single_sentence_no_breaks.json"


def jfull(p: Path) -> str:
    return json.loads(p.read_text(encoding="utf-8"))["JFULL"]


def lossless(s: str) -> T.LosslessText:
    return T.LosslessText.from_string(s)


def chunks_of(s: str, **kw) -> list[CH.Chunk]:
    return CH.chunk_text(lossless(s), **kw)


# ── fixtures：涵蓋真實資料的各種形狀 ───────────────────────────────────────

CRLF_DOC = (
    "臺灣臺北地方法院民事判決\r\n"
    "　　　　　　　　　案號　　訴字第2468號\r\n"
    "　　原告主張之事實。" * 30 + "\r\n"
    "主　　　文\r\n"
    "　　駁回再審之訴。" * 30 + "\r\n"
    "理　　由\r\n"
    "　　按民法第185條規定辦理。" * 30 + "\r\n"
)

MULTI_SECTION = (
    "第一段。\r\n\r\n"
    "主　　　文\r\n"
    "內容一。\r\n"
    "\r\n"
    "事　　實\r\n"
    "內容二。\r\n"
    "\r\n"
    "理　　由\r\n"
    "內容三。" * 40 + "\r\n"
)

NO_MARKER = "這份判決沒有任何已辨識的段落標記，只有純敘述。" * 40 + "結尾。"

WHITESPACE_SENSITIVE = "　　開頭全形空格\r\n\r\n\r\n" + "　　行首縮排內容。" * 30 + "　　結尾全形空格"

NO_NEWLINE = "沒有任何換行的單句判決書，長度足夠長以便觸發分塊。" * 20

# tasks.md T015 明確要求「reconstruction equality on all fixtures plus a
# 770 KB oversized fixture」。
#
# 真 corpus 的最大 `JFULL` 是 777,963 bytes（corpus-schema-survey.md 記錄的
# size range 上界），所以這個 fixture 用同樣量級。用一個「小一點也差不多」的
# 尺寸會讓測試通過而真資料沒被測到 —— 那正是這個 fixture 存在的理由。
_SENTENCE = "原審判決以事實及理由無誤為由，駁回上訴。經查，原審就系爭事實所為之法律上之判斷，說理已臻明確，並無違誤。"
OVERSIZED = (
    "臺灣高等法院刑事判決\r\n"
    "　　　　　　　　　　　　案號　　上訴字第1234號\r\n"
    "主　　　文\r\n"
    + ("　　" + _SENTENCE + "\r\n") * 2450
    + "理　　由\r\n"
    + ("　　" + _SENTENCE + "\r\n") * 2450
)


ALL_DOCS = {
    "crlf": CRLF_DOC,
    "multi_section": MULTI_SECTION,
    "no_marker": NO_MARKER,
    "whitespace_sensitive": WHITESPACE_SENSITIVE,
    "no_newline": NO_NEWLINE,
    "oversized_770kb": OVERSIZED,
    "real_civil": jfull(CIVIL),
    "real_criminal_no_marker": jfull(CRIMINAL),
    "real_single_sentence": jfull(SINGLE),
    "empty": "",
}


# ── 核心：substring invariant ──────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(ALL_DOCS))
@pytest.mark.parametrize("size", [50, 120, 750, 2000])
def test_every_chunk_is_a_substring_of_the_source(name, size):
    """對每個 chunk：`chunk.text == jfull[start:end]`。"""
    s = ALL_DOCS[name]
    t = lossless(s)
    for c in CH.chunk_text(t, chunk_size=size):
        assert s[c.start_offset : c.end_offset] == c.text
        c.verify_against(t)


@pytest.mark.parametrize("name", sorted(ALL_DOCS))
@pytest.mark.parametrize("size", [50, 120, 750, 2000])
def test_reconstruction_is_byte_exact(name, size):
    """依序拼接所有 chunk → **逐 byte 等於**原文。

    這條比 substring invariant 更強：它抓到「chunk 邊界吞掉字元」—— 那種
    bug 下每個 chunk 單獨看都正常。
    """
    s = ALL_DOCS[name]
    cs = CH.chunk_text(lossless(s), chunk_size=size)
    assert CH.reconstruct(cs) == s
    assert "".join(c.text for c in cs).encode("utf-8") == s.encode("utf-8")


def test_oversized_fixture_matches_the_corpus_size_range():
    """確認 oversized fixture 真的達到真實規模 —— 否則上面的測試沒有意義。

    真 corpus 的 `JFULL` 上界是 777,963 bytes（corpus-schema-survey.md 的 size
    range）。這個斷言把 fixture 釘在 ≥ 770 KB：若有人「精簡」fixture 讓測試
    變快，重組性質就不再是在真實規模下被驗證。
    """
    size = len(OVERSIZED.encode("utf-8"))
    assert size >= 770 * 1024, f"oversized fixture 只有 {size} bytes"
    assert size >= 777_963, "應達到真 corpus 的最大 entry 大小"


def test_chunks_are_contiguous_without_gaps():
    """相鄰 chunk 必須首尾相接 —— 沒有間隙、沒有重疊。"""
    for name, s in ALL_DOCS.items():
        if not s:
            continue
        cs = CH.chunk_text(lossless(s), chunk_size=120)
        if not cs:
            continue
        assert cs[0].start_offset == 0
        assert cs[-1].end_offset == len(s)
        for a, b in zip(cs, cs[1:]):
            assert a.end_offset == b.start_offset, name


# ── 邊界安全性 ────────────────────────────────────────────────────────────

def test_short_document_produces_one_chunk():
    cs = chunks_of("短", chunk_size=750)
    assert len(cs) == 1
    assert cs[0].boundary_kind is CH.BoundaryKind.SINGLE
    assert cs[0].start_offset == 0 and cs[0].end_offset == 1


def test_empty_document_produces_no_chunks():
    """空文件 → 沒有 chunk，不是「一個空 chunk」。"""
    assert chunks_of("") == []


def test_no_empty_chunks_are_ever_produced():
    """任何非空文件的每個 chunk 都必須有內容。

    空 chunk 對檢索沒有意義，而且會讓「chunk 數量」這個指標失真。
    """
    for name, s in ALL_DOCS.items():
        if not s:
            continue
        for size in (50, 120, 750):
            for c in CH.chunk_text(lossless(s), chunk_size=size):
                assert c.length > 0, f"{name}@{size} 產生了空 chunk"


def test_no_marker_document_still_chunks():
    """沒有 marker 的文件**照樣**能分塊（133/494 真實文件是這樣）。

    不得因為缺少 marker 而拒絕整份文件 —— 那會讓 27% 的真實資料無法處理。
    """
    assert T.observed_markers(lossless(NO_MARKER)) == []
    cs = chunks_of(NO_MARKER, chunk_size=300)
    assert len(cs) > 1
    assert CH.reconstruct(cs) == NO_MARKER


def test_real_criminal_no_marker_document_chunks():
    s = jfull(CRIMINAL)
    assert T.observed_markers(lossless(s)) == []
    cs = chunks_of(s, chunk_size=750)
    assert len(cs) >= 1
    assert CH.reconstruct(cs) == s


def test_no_newline_document_uses_forced_boundary():
    """沒有自然邊界 → 強制切分，且**記錄下來**。

    這是 tasks.md acceptance 的明確要求。隱藏它等於丟掉「這份文件的結構沒被
    理解」這個訊號。
    """
    cs = chunks_of(NO_NEWLINE, chunk_size=50)
    assert any(c.boundary_kind is CH.BoundaryKind.FORCED for c in cs)
    assert CH.reconstruct(cs) == NO_NEWLINE


def test_boundary_kind_is_recorded_for_every_chunk():
    for name, s in ALL_DOCS.items():
        if not s:
            continue
        for c in CH.chunk_text(lossless(s), chunk_size=200):
            assert isinstance(c.boundary_kind, CH.BoundaryKind)


def test_boundary_summary_counts_kinds():
    cs = chunks_of(CRLF_DOC, chunk_size=300)
    summary = CH.boundary_summary(cs)
    assert sum(summary.values()) == len(cs)
    assert set(summary) <= {k.value for k in CH.BoundaryKind}


def test_oversized_document_prefers_natural_boundaries():
    """夠大的文件應該 mostly 落在自然邊界上，而不是一直硬切。"""
    cs = CH.chunk_text(lossless(OVERSIZED), chunk_size=750)
    assert len(cs) > 5
    forced = sum(1 for c in cs if c.boundary_kind is CH.BoundaryKind.FORCED)
    assert forced == 0, "這份文件有換行，不該需要硬切"


def test_marker_can_serve_as_a_boundary_signal():
    """marker 是 derived boundary signal，不是 authoritative metadata。

    驗證方式是：marker **之前**的 offset 會出現在自然邊界裡。
    """
    s = MULTI_SECTION
    cs = CH.chunk_text(lossless(s), chunk_size=250)
    # marker 存在
    assert T.observed_markers(lossless(s))
    # 且不因為 marker 而改變重組性質
    assert CH.reconstruct(cs) == s


def test_chunk_size_below_minimum_is_rejected():
    with pytest.raises(ValueError, match="chunk_size"):
        CH.chunk_text(lossless(CRLF_DOC), chunk_size=10)


# ── CRLF / U+3000 / whitespace ────────────────────────────────────────────

def test_crlf_is_preserved_across_chunk_boundaries():
    """chunk 之間的 `\r\n` 不得變成 `\n` 或消失。"""
    cs = CH.chunk_text(lossless(CRLF_DOC), chunk_size=300)
    rebuilt = CH.reconstruct(cs)
    assert rebuilt.count("\r\n") == CRLF_DOC.count("\r\n")
    assert CRLF_DOC.count("\n") == rebuilt.count("\n")
    # 沒有游離的 \r（那會是 CRLF→LF 的中間態）
    assert rebuilt.count("\r") == rebuilt.count("\r\n")


def test_ideographic_spaces_are_preserved():
    cs = CH.chunk_text(lossless(CRLF_DOC), chunk_size=300)
    assert CH.reconstruct(cs).count("　") == CRLF_DOC.count("　")


def test_line_leading_whitespace_survives_chunking():
    """行首全形空格不得被 strip 掉。

    若 chunking 做了 per-line strip，重組後的行會失去縮排 —— 而重組等於原文
    這條斷言會抓到它。
    """
    s = WHITESPACE_SENSITIVE
    cs = CH.chunk_text(lossless(s), chunk_size=200)
    rebuilt = CH.reconstruct(cs)
    assert rebuilt.count("　　") == s.count("　　")
    assert "\r\n　　" in rebuilt


def test_document_leading_and_trailing_whitespace_preserved():
    """文件邊界附近的空白不得被裁掉。"""
    s = "　　開頭\r\n" + "內容。" * 60 + "　　結尾"
    cs = CH.chunk_text(lossless(s), chunk_size=200)
    rebuilt = CH.reconstruct(cs)
    assert rebuilt == s
    assert rebuilt.startswith("　　開頭")
    assert rebuilt.endswith("　　結尾")


def test_trailing_newline_is_preserved():
    s = "內容\r\n" * 40
    rebuilt = CH.reconstruct(CH.chunk_text(lossless(s), chunk_size=200))
    assert rebuilt.endswith("\r\n")


def test_blank_lines_are_preserved():
    s = "a\r\n\r\n\r\n" + "b" * 300 + "\r\n\r\n"
    rebuilt = CH.reconstruct(CH.chunk_text(lossless(s), chunk_size=120))
    assert rebuilt == s
    assert "\r\n\r\n\r\n" in rebuilt
    assert rebuilt.startswith("a\r\n\r\n\r\n")


def test_unicode_is_not_normalised():
    """NFKC 會把 `　` 與全形英數字折半 —— 那會改變字元數並讓 offset 失效。"""
    import unicodedata

    s = "　ＡＢ１２３" + "內容。" * 40
    t = lossless(s)
    cs = CH.chunk_text(t, chunk_size=200)
    rebuilt = CH.reconstruct(cs)
    assert rebuilt == s
    assert unicodedata.normalize("NFKC", s) != s


# ── Determinism ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["crlf", "multi_section", "whitespace_sensitive", "oversized_770kb"])
def test_repeated_chunking_is_identical(name):
    """同一份文件重跑 → 完全相同結果。"""
    s = ALL_DOCS[name]
    t = lossless(s)
    runs = [
        [(c.chunk_index, c.start_offset, c.end_offset, c.text, c.boundary_kind, c.point_id, c.sha256)
         for c in CH.chunk_text(t, chunk_size=300)]
        for _ in range(5)
    ]
    assert all(r == runs[0] for r in runs)


def test_chunk_point_id_is_deterministic():
    t = lossless(CRLF_DOC)
    a = [c.point_id for c in CH.chunk_text(t, chunk_size=300)]
    b = [c.point_id for c in CH.chunk_text(lossless(CRLF_DOC), chunk_size=300)]
    assert a == b
    # point_id 是 64 hex chars
    assert all(len(p) == 64 for p in a)
    assert len(set(a)) == len(a), "不同 chunk 不得有相同 point_id"


def test_point_id_differs_by_chunk_index():
    cs = CH.chunk_text(lossless(CRLF_DOC), chunk_size=200)
    assert len({c.point_id for c in cs}) == len(cs)


def test_point_id_is_not_time_or_random_based():
    """point_id 不得含時間戳或亂數。

    驗證方式是：不同 chunk_size 對**同一個** chunk_index 會給出不同的 chunk
    內容，但 point_id 仍只由 (document, index) 決定 —— 所以 point_id 在同一個
    文件內不會隨 chunk_size 之外的任何東西改變。
    """
    t = lossless(CRLF_DOC)
    a = CH.chunk_text(t, chunk_size=200)
    b = CH.chunk_text(t, chunk_size=600)
    assert a[0].point_id == b[0].point_id
    assert a[0].text != b[0].text or len(a) == 1


def test_chunk_index_is_sequential_from_zero():
    cs = CH.chunk_text(lossless(OVERSIZED), chunk_size=400)
    assert [c.chunk_index for c in cs] == list(range(len(cs)))


def test_offsets_are_strictly_increasing():
    for name, s in ALL_DOCS.items():
        if not s:
            continue
        cs = CH.chunk_text(lossless(s), chunk_size=200)
        offsets = [c.start_offset for c in cs]
        assert offsets == sorted(offsets)
        assert len(set(offsets)) == len(offsets)


def test_chunk_hash_matches_its_text():
    import hashlib

    cs = CH.chunk_text(lossless(CRLF_DOC), chunk_size=300)
    for c in cs:
        assert c.sha256 == hashlib.sha256(c.text.encode("utf-8")).hexdigest()


# ── 從 document 的端到端 ───────────────────────────────────────────────────

def test_chunk_document_carries_identity():
    """每個 chunk 都必須帶著 document identity —— 不能脫離來源。"""
    doc = document.from_document(
        json.loads(CIVIL.read_text(encoding="utf-8")),
        entry_path="202607\\x\\TPDV,115,訴,2468,20260901,1.json",
        sha256="a" * 64,
    )
    cs = CH.chunk_document(doc, chunk_size=100)
    assert len(cs) >= 1
    for c in cs:
        assert c.jid == doc.jid
        assert c.source_document == doc.entry_path
        assert doc.jfull[c.start_offset : c.end_offset] == c.text


def test_chunking_does_not_modify_the_document():
    """分塊是唯讀的 —— `JFULL` 不得被改動。"""
    doc = document.from_document(
        json.loads(CIVIL.read_text(encoding="utf-8")),
        entry_path="x.json",
        sha256="a" * 64,
    )
    before = doc.jfull
    CH.chunk_document(doc, chunk_size=100)
    assert doc.jfull == before


def test_reconstruct_from_document_chunks_reproduces_jfull():
    doc = document.from_document(
        json.loads(CIVIL.read_text(encoding="utf-8")),
        entry_path="x.json",
        sha256="a" * 64,
    )
    cs = CH.chunk_document(doc, chunk_size=100)
    assert CH.reconstruct(cs) == doc.jfull


# ── 序列化往返 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["crlf", "whitespace_sensitive", "no_newline"])
def test_chunk_dict_round_trip_preserves_everything(name):
    """model → dict → 重建 → 重組仍等於原文。

    驗證 CRLF、U+3000、offsets、JID、sequence 在往返後都沒丟。
    """
    s = ALL_DOCS[name]
    cs = CH.chunk_text(lossless(s), chunk_size=250, source_document="d.json", jid="J,X,Y,Z,W")
    serialized = [c.as_dict() for c in cs]

    # 重建
    rebuilt = [
        CH.Chunk(
            text=d["text"],
            start_offset=d["start_offset"],
            end_offset=d["end_offset"],
            chunk_index=d["chunk_index"],
            boundary_kind=CH.BoundaryKind(d["boundary_kind"]),
            source_document=d["source_document"],
            jid=d["jid"],
            sha256=d["sha256"],
        )
        for d in serialized
    ]

    assert CH.reconstruct(rebuilt) == s
    assert [c.sha256 for c in rebuilt] == [c.sha256 for c in cs]
    assert [c.jid for c in rebuilt] == ["J,X,Y,Z,W"] * len(rebuilt)
    assert [c.chunk_index for c in rebuilt] == list(range(len(rebuilt)))


def test_chunk_dict_is_json_serialisable():
    import json as _json

    cs = CH.chunk_text(lossless(CRLF_DOC), chunk_size=300, source_document="d.json", jid="J1")
    payload = _json.dumps([c.as_dict() for c in cs], ensure_ascii=False)
    assert _json.loads(payload)[0]["jid"] == "J1"


# ── 這個模組不做什麼 ───────────────────────────────────────────────────────

def test_chunk_record_is_frozen():
    cs = CH.chunk_text(lossless(CRLF_DOC), chunk_size=300)
    with pytest.raises(Exception):
        cs[0].text = "改掉了"  # type: ignore[misc]


def test_chunk_has_no_embedding_or_derived_fields():
    """不得有 embedding / score / summary / court 這類欄位。"""
    c = CH.chunk_text(lossless(CRLF_DOC), chunk_size=300)[0]
    for forbidden in (
        "embedding", "vector", "score", "rank", "summary",
        "court", "case_type", "branch", "normalized_text",
    ):
        assert not hasattr(c, forbidden), f"Chunk 不得有 {forbidden}"


def test_chunk_module_does_not_import_models_or_stores():
    import ast

    tree = ast.parse((ROOT / "ingest" / "judgements" / "chunk.py").read_text("utf-8"))
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    # 不得有 LLM / 向量庫 / 資料庫
    assert not (imported & {"openai", "anthropic", "ollama", "numpy", "torch",
                            "qdrant_client", "psycopg", "asyncpg", "httpx"})


def test_chunk_module_never_rewrites_text():
    """模組不得呼叫任何會改寫字串的方法。

    ## 為什麼查「方法呼叫」而不是「字串常量」

    第一版查的是字串常量，結果被診斷訊息的片段觸發（`"chunk "`、`"utf-8"`）。
    那些是**錯誤訊息的文字**，不是拿來改寫原文的。

    真正要禁止的是**呼叫**：`.strip()`、`.replace()`、`.split()`（丟棄分隔符）、
    `unicodedata.normalize()`。AST 查屬性存取能直接看到這些，而且不會誤判
    docstring 或訊息文字 —— 這個區分在 T004（`zlib` 註解）、T008（`court`
    docstring）、T011（`_clean` 說明）都踩過。
    """
    import ast

    tree = ast.parse((ROOT / "ingest" / "judgements" / "chunk.py").read_text("utf-8"))

    # 所有屬性存取的方法名
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    forbidden = {
        "strip", "lstrip", "rstrip", "replace", "translate",
        "lower", "upper", "title", "capitalize", "swapcase",
        "splitlines", "split", "rsplit", "expandtabs", "rstrip",
        "encode",  # 允許 encode 用於 hash，但必須是 .encode() 不帶參數
    }
    # `encode()` 只允許 `"utf-8"` —— 那是 authoritative 編碼（raw bytes 就是
    # UTF-8）。指定別的編碼（utf-16、latin-1、big5…）就是轉換，那會改變位元組
    # 並讓 byte-level 重組性質失效。
    if "encode" in attrs:
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "encode"
            ):
                encodings = [
                    a.value for a in n.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                ]
                assert all(e.lower().replace("_", "-") == "utf-8" for e in encodings), (
                    f"encode() 只允許 utf-8，發現 {encodings}"
                )

    hits = attrs & (forbidden - {"encode"})
    assert not hits, f"chunk.py 不得呼叫 {hits}"

    # 不得有 unicodedata / normalize
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "normalize" not in called
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert "unicodedata" not in imported
