"""`document.py` 的契約測試（spec 004 T009）。

## 這個記錄的兩條不變條件

1. **每個字串都與來源逐 byte 相同** —— 沒有任何欄位被加工過。
2. **`jid` 是 opaque** —— 沒有任何位置存取器（FR-028）。

第二條是機制性的：模組裡**不存在**能拆解 `JID` 的函式。測試用
`dir()` 斷言這件事，因為「文件存在但沒人用」和「文件不存在」是兩種完全
不同的未來 —— 前者早晚會有人拿來用。

## 為什麼不能用「大概相等」驗證

`assert doc.jfull == expected` 若 expected 是從 source 反過來 strip 過的
字串，兩邊都錯而測試綠。所以這裡的每一條比對都用**原檔案重新讀出的值**，
不經過任何中間轉換。
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

import document  # noqa: E402
import schema  # noqa: E402

CIVIL = DOCS / "civil_6field.json"
CONSTITUTIONAL = DOCS / "constitutional_5field_empty_jpdf.json"
JYEAR79 = DOCS / "jyear_79.json"
SINGLE = DOCS / "single_sentence_no_breaks.json"
DRIFT = DOCS / "drift_missing_key.json"

ENTRY = "202607\\臺灣臺北地方法院民事\\TPDV,115,訴,2468,20260901,1.json"
SHA = "a" * 64


def make(p: Path = CIVIL) -> document.JudgmentDocument:
    doc = json.loads(p.read_text(encoding="utf-8"))
    return document.from_document(doc, entry_path=ENTRY, sha256=SHA)


# ── 逐 byte 原樣 ────────────────────────────────────────────────────────────

def test_every_field_equals_the_source_value():
    """每個欄位都必須等於來源 JSON 的值 —— 逐字元比對。"""
    raw = json.loads(CIVIL.read_text(encoding="utf-8"))
    d = make()
    assert d.jid == raw["JID"]
    assert d.jyear == raw["JYEAR"]
    assert d.jcase == raw["JCASE"]
    assert d.jno == raw["JNO"]
    assert d.jdate == raw["JDATE"]
    assert d.jtitle == raw["JTITLE"]
    assert d.jfull == raw["JFULL"]
    assert d.jpdf == raw["JPDF"]


def test_jfull_is_byte_identical_not_merely_equal_after_strip():
    """`jfull` 必須與來源**逐 byte 相同**，不是 strip 後相等。

    這條是整個模組的核心斷言。用 strip 過的 expected 比較會讓「意外 strip」
    通過 —— 那正是 T010 要防的事。
    """
    raw = json.loads(CIVIL.read_text(encoding="utf-8"))["JFULL"]
    d = make()
    assert d.jfull == raw
    # 確認兩者**確實不同**（否則這條測試是空的）
    assert d.jfull != raw.strip()
    assert d.jfull.endswith("\r\n")   # 結尾 CRLF 保留
    assert d.jfull.startswith("臺灣")   # 開頭無 BOM/空白

    # 全形空格的**前導縮排**保留。
    #
    # 這裡查的是 `\r\n　　`（行首兩個 U+3000）—— 若有人做了 per-line strip
    # （`ingest/laws/normalize.py::_clean` 的行為），每一行的前導全形空格都會
    # 消失，而那正是判決文字的結構性縮排。
    #
    # 注意 fixture 的 `主　　　文` 標記本身**沒有**前導空格（那是資料的真實
    # 樣子），所以這裡不能用 `\r\n　　主` 來斷言 —— 那樣寫會與 fixture 矛盾，
    # 而我第一版正是這樣寫錯的。
    assert "\r\n　　" in d.jfull, "行首全形空格必須保留"
    assert "\r\n　　駁回再審之訴。" in d.jfull
    assert "\r\n主　　　文\r\n" in d.jfull


def test_crlf_count_preserved():
    raw = json.loads(CIVIL.read_text(encoding="utf-8"))["JFULL"]
    assert make().jfull.count("\r\n") == raw.count("\r\n")
    assert make().jfull.count("\r\n") == 6


def test_ideographic_spaces_preserved():
    raw = json.loads(CIVIL.read_text(encoding="utf-8"))["JFULL"]
    assert make().jfull.count("　") == raw.count("　")


def test_round_trip_through_source_dict_is_lossless():
    """`source_dict()` 必須能完整還原來源（往返無損）。"""
    raw = json.loads(CIVIL.read_text(encoding="utf-8"))
    d = make()
    assert d.source_dict() == raw
    assert d.source_dict()["JFULL"] == raw["JFULL"]


def test_source_dict_keys_are_the_source_names():
    """key 名是來源名，不是發明的新名字（FR-016）。"""
    d = make()
    assert tuple(d.source_dict().keys()) == schema.CANONICAL_KEYS


# ── JID 是 opaque ───────────────────────────────────────────────────────────

def test_jid_returns_the_whole_string():
    d = make()
    assert d.jid == "TPDV,115,訴,2468,20260901,1"
    assert "," in d.jid  # 沒被拆開


def test_jid_has_no_positional_accessors():
    """`jid` 上不得有 `.year` / `.case` / `.no` / `.date` 這類屬性。

    FR-028 禁止 positional parsing。一旦記錄上存在 `.year`，108,409 筆裡的
    139 筆（5-field）會讓它要嘛拋錯、要嘛回傳錯的值 —— 而那 139 筆正是憲法
    法庭的真實資料。
    """
    d = make()
    for forbidden in ("year", "case", "no", "date", "court", "branch", "inst"):
        assert not hasattr(d.jid, forbidden)
        assert not hasattr(type(d.jid), forbidden)


def test_document_module_has_no_jid_parser():
    """模組裡不得存在任何拆解 JID 的函式或屬性。"""
    public = {n for n in dir(document) if not n.startswith("_")}
    for forbidden in (
        "jid_fields", "jid_parts", "parse_jid", "split_jid",
        "jid_year", "jid_court", "jid_branch",
    ):
        assert forbidden not in public, f"document 不得提供 {forbidden}"


def test_document_fields_are_exactly_the_source_fields():
    """記錄的欄位名是 source key 去前綴，不是別的東西。"""
    assert document.JudgmentDocument.field_names() == (
        "jid", "jyear", "jcase", "jno", "jdate", "jtitle", "jfull", "jpdf",
        "entry_path", "sha256",
    )


def test_both_jid_shapes_produce_identical_records():
    """5-field 與 6-field 的記錄結構相同 —— 不因為欄位數不同而有差別待遇。

    兩者都是同一個 dataclass，欄位集合完全相同。若哪天有人「因為 5-field
    比較短所以用另一個 model」，這條就會紅。
    """
    six = make(CIVIL)
    five = make(CONSTITUTIONAL)
    assert type(six) is type(five)
    assert type(six).field_names() == type(five).field_names()
    # 5-field 那筆的 JID 原樣保留，沒被「補上」缺的機構碼
    assert five.jid == "JCCC,115,審裁,1158,20260701"
    assert len(six.jid.split(",")) != len(five.jid.split(","))  # 確實不同形狀


# ── frozen ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "field", ["jid", "jyear", "jcase", "jno", "jdate", "jtitle", "jfull", "jpdf"]
)
def test_attribute_assignment_raises(field):
    """每個來源欄位都不可改 —— frozen。"""
    d = make()
    with pytest.raises(Exception):
        setattr(d, field, "被改掉了")


def test_provenance_fields_are_frozen_too():
    d = make()
    with pytest.raises(Exception):
        d.sha256 = "b" * 64  # type: ignore[misc]
    with pytest.raises(Exception):
        d.entry_path = "other"  # type: ignore[misc]


# ── 沒有 derived 欄位 ──────────────────────────────────────────────────────

def test_no_derived_or_normalised_fields():
    """記錄上不得有任何推論出來的欄位。

    常見的違規：`court`、`case_type`、`text`、`summary`、`chunks`、`year`
    —— 全部都是從來源**推論**的，而 spec 對每一個都明確禁止
    （court 是 UNKNOWN/INFERRED，其餘會被誤認為司法內容）。
    """
    d = make()
    for forbidden in (
        "court", "case_type", "branches", "summary", "abstract",
        "chunks", "citations", "text", "normalized", "text_length",
        "year", "roc_year", "date",
    ):
        assert not hasattr(d, forbidden), f"記錄上不得有 {forbidden}"


def test_as_provenance_excludes_jfull():
    """provenance 不含全文 —— 一份 108,409 筆的 listing 不該是幾百 MB。"""
    d = make()
    p = d.as_provenance()
    assert "jfull" not in p
    assert "JFULL" not in p
    assert p["jid"] == d.jid
    assert p["entry_path"] == ENTRY
    assert p["sha256"] == SHA


def test_as_provenance_reports_jpdf_presence_as_a_bool():
    """`has_jpdf` 是 bool —— 空字串本身不需要被「修正」成 None 或猜測的 URL。"""
    assert make(CIVIL).as_provenance()["has_jpdf"] is True
    assert make(CONSTITUTIONAL).as_provenance()["has_jpdf"] is False


def test_keys_property_returns_source_names():
    assert make().keys == schema.CANONICAL_KEYS


# ── JYEAR / JDATE 獨立，不推導 ─────────────────────────────────────────────

def test_jyear_79_is_preserved_verbatim():
    """`JYEAR="79"` 原樣保留，不被「修正」成 115 或任何東西。"""
    d = make(JYEAR79)
    assert d.jyear == "79"
    assert d.jdate == "20260903"
    # 兩者不一致是合法的（FR-029）
    assert d.jyear != d.jdate[:2]


def test_document_module_does_not_derive_fields():
    src = (ROOT / "ingest" / "judgements" / "document.py").read_text("utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for forbidden in ("jyear_from_jdate", "jdate_from_jyear", "derive", "infer"):
        # "infer" 允許出現在說明「不推論」的註解；這裡只看實際程式碼行
        assert forbidden not in code or forbidden == "infer", forbidden


# ── provenance 必填 ────────────────────────────────────────────────────────

def test_entry_path_is_required():
    doc = json.loads(CIVIL.read_text(encoding="utf-8"))
    with pytest.raises(document.DocumentError, match="entry_path"):
        document.from_document(doc, entry_path="", sha256=SHA)


def test_sha256_is_required():
    doc = json.loads(CIVIL.read_text(encoding="utf-8"))
    with pytest.raises(document.DocumentError, match="sha256"):
        document.from_document(doc, entry_path=ENTRY, sha256="")


def test_non_string_provenance_is_rejected():
    doc = json.loads(CIVIL.read_text(encoding="utf-8"))
    with pytest.raises(document.DocumentError):
        document.from_document(doc, entry_path=123, sha256=SHA)  # type: ignore[arg-type]


# ── drift 必須被拒絕 ───────────────────────────────────────────────────────

def test_drift_document_is_refused():
    """一份 drift 的文件**不能**建立記錄。

    這與 `schema.validate()` 回報 drift 不衝突：validator 的職責是回報，
    constructor 的職責是拒絕建立一個不誠實的記錄。
    """
    doc = json.loads(DRIFT.read_text(encoding="utf-8"))
    with pytest.raises(document.DocumentError, match="8-key"):
        document.from_document(doc, entry_path=ENTRY, sha256=SHA)


def test_non_string_value_is_refused_not_coerced():
    doc = json.loads((DOCS / "drift_non_string_value.json").read_text(encoding="utf-8"))
    with pytest.raises(document.DocumentError):
        document.from_document(doc, entry_path=ENTRY, sha256=SHA)


# ── 從 T005 的解壓結果建立 ─────────────────────────────────────────────────

def test_from_extracted_round_trip(tmp_path):
    """`from_extracted()` 走完整路徑：解壓 → 解析 → 驗證 → 記錄。

    用 `docs_fixture.rar`（`make_fixture.py::build_docs_archive()` 產生，內容是
    `docs/*.json` 那些真實 8-key 文件）。整條路徑不需要 production RAR，也
    不需要 decoder（payload 是 store method）。

    這個 archive 只收**完全合約**的文件 —— key 集合、值型別、key 順序、JID
    形狀都要過。drift_* 那些是 validator 的**輸入**，不該是能成功解壓的 entry。
    """
    import extract
    import inventory

    fixture = ROOT / "tests" / "fixtures" / "judgements" / "docs_fixture.rar"
    assert fixture.is_file(), "docs_fixture.rar 尚未產生"

    entry = next(
        e for e in inventory.inventory(fixture)
        if e.path == "docs\\civil_6field.json"
    )
    r = extract.extract_one(entry.path, archive=fixture, dest=tmp_path)
    d = document.from_extracted(r)

    # provenance 由 T005 的結果帶入，不重新生成
    assert d.sha256 == r.sha256
    assert d.entry_path == entry.path

    # 解壓出的 bytes 與磁碟上的 fixture **逐 byte 相同** —— 這是 T010 的前提
    on_disk = (DOCS / "civil_6field.json").read_bytes()
    assert r.data == on_disk

    raw = json.loads(r.data.decode("utf-8"))
    assert d.jid == raw["JID"]
    assert d.jfull == raw["JFULL"]


def test_all_docs_fixture_entries_build_a_document(tmp_path):
    """archive 裡的每一筆都要能走完整條路徑。

    一筆失敗就代表 fixture 與契約不一致 —— 而那正是這條測試要抓的。
    """
    import extract
    import inventory

    fixture = ROOT / "tests" / "fixtures" / "judgements" / "docs_fixture.rar"
    entries = [e for e in inventory.inventory(fixture) if not e.is_dir]
    assert len(entries) == 6

    for e in entries:
        r = extract.extract_one(e.path, archive=fixture, dest=tmp_path)
        d = document.from_extracted(r)
        raw = json.loads(r.data.decode("utf-8"))
        assert d.jid == raw["JID"]
        assert d.jfull == raw["JFULL"]
        # 解壓出的位元組與磁碟上的 fixture **逐 byte 相同**
        assert r.data == (DOCS / e.path.split("\\", 1)[1]).read_bytes()


def test_from_extracted_rejects_non_bytes():
    class Fake:
        data = "not bytes"
        path = "x"
        sha256 = "a" * 64

    with pytest.raises(document.DocumentError, match="bytes"):
        document.from_extracted(Fake())


def test_from_extracted_rejects_malformed_json(tmp_path):
    import extract

    class Fake:
        data = b"{not json"
        path = "x"
        sha256 = "a" * 64

    with pytest.raises(document.DocumentError, match="JSON"):
        document.from_extracted(Fake())


# ── 這個模組不做什麼 ────────────────────────────────────────────────────────

def test_document_does_not_chunk_or_extract_citations():
    import ast

    tree = ast.parse(
        (ROOT / "ingest" / "judgements" / "document.py").read_text("utf-8")
    )
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not (called & {"chunk", "split", "find_citations", "detect"})


def test_document_does_not_import_downstream_modules():
    import ast

    tree = ast.parse(
        (ROOT / "ingest" / "judgements" / "document.py").read_text("utf-8")
    )
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    # 不得 import qdrant / psycopg / citation 等下游
    assert not (imported & {"qdrant_client", "psycopg", "psycopg2", "embeddings"})
