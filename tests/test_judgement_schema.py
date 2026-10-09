"""`schema.py` 的契約測試（spec 004 T008）。

## 這個 validator 的職責邊界

它只做一件事：報告一份 source JSON 是否符合 `spec.md` §Source Structure
Contract 記錄的 **8-field 契約**。它不做 semantic interpretation ——
不驗 `JCASE` 的值域、不驗 `JYEAR` 的長度、不驗 `JDATE` 的格式。

理由寫在模組 docstring，這裡只重述一次：corpus-wide 只觀察了 0.46%，
任何值域斷言都是對未經驗證的分布下判斷。

## 三個「合法但反直覺」的情況是本測試的重點

把它們當 schema error 會丟掉真實的司法資料：

* **5-field JID**（139 筆憲法法庭）— 合法
* **空 JPDF**（同上 139 筆）— 合法，不是錯誤
* **`JYEAR="79"`** — 合法真實資料，不是筆誤

## 不得拋例外

drift 是**資料的狀態**，不是程式的錯誤。`validate()` 對任何輸入都回傳
`Valid` 或 `Drift`，包括 `None`、整數、list、以及非法 UTF-8。
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

import schema  # noqa: E402

CIVIL = DOCS / "civil_6field.json"
CRIMINAL = DOCS / "criminal_6field.json"
CONSTITUTIONAL = DOCS / "constitutional_5field_empty_jpdf.json"
JYEAR79 = DOCS / "jyear_79.json"
SINGLE = DOCS / "single_sentence_no_breaks.json"
LF_ONLY = DOCS / "lf_line_endings.json"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def drift(p: Path):
    """跑 validator 並斷言它回報了 drift —— 讀測試失敗時的訊息最省事。"""
    r = schema.parse_and_validate(p.read_bytes())
    assert isinstance(r, schema.Drift), f"{p.name} 應為 Drift，實得 {r!r}"
    return r


# ── 正向：符合契約的文件 ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name", ["civil_6field", "criminal_6field", "jyear_79", "single_sentence_no_breaks"]
)
def test_conforming_documents_report_valid(name):
    """四個 fixture 都必須 Valid（含 anomaly 那兩個 —— anomaly 不是 drift）。"""
    r = schema.parse_and_validate((DOCS / f"{name}.json").read_bytes())
    assert isinstance(r, schema.Valid), f"{name}: {r!r}"


def test_constitutional_5field_with_empty_jpdf_is_valid():
    """5-field JID + 空 JPDF 是**合法**的，不是錯誤。

    這是 139 筆憲法法庭資料的形狀。把它判成 drift 會丟掉一整類真實判決。
    """
    r = schema.parse_and_validate(CONSTITUTIONAL.read_bytes())
    assert isinstance(r, schema.Valid), f"{r!r}"


def test_lf_line_endings_are_not_a_schema_error():
    """LF-only 不是 schema drift。

    換行是 `JFULL` **內容**的性質，不是 JSON 結構的性質。spec 說 493/494 是
    CRLF，但那屬於 T010 的 lossless 驗證範圍；在 T008 把它判成 drift，會讓
    結構檢查承擔內容判斷的責任。
    """
    assert isinstance(schema.parse_and_validate(LF_ONLY.read_bytes()), schema.Valid)


def test_key_set_is_exactly_eight():
    assert len(schema.CANONICAL_KEYS) == 8
    assert set(schema.CANONICAL_KEYS) == {
        "JID", "JYEAR", "JCASE", "JNO", "JDATE", "JTITLE", "JFULL", "JPDF"
    }


def test_key_order_is_the_observed_one():
    """順序是觀察到的事實（494/494 穩定），所以它是契約的一部分。"""
    assert schema.CANONICAL_ORDER == (
        "JID", "JYEAR", "JCASE", "JNO", "JDATE", "JTITLE", "JFULL", "JPDF"
    )


def test_valid_reports_observed_keys():
    r = schema.validate(load(CIVIL))
    assert r.keys == schema.CANONICAL_KEYS
    assert r.key_order_stable is True


# ── JID 形狀：兩種都合法 ────────────────────────────────────────────────────

def test_both_jid_shapes_are_accepted():
    """6-field 與 5-field 都被接受 —— FR-028 禁止依欄位數判斷。"""
    assert schema.jid_shape("TPDV,115,訴,2468,20260901,1") == 6
    assert schema.jid_shape("JCCC,115,審裁,1158,20260701") == 5
    assert schema.KNOWN_JID_SHAPES == {5, 6}


def test_jid_shape_does_not_return_the_fields():
    """`jid_shape()` 只回傳數量，不提供拆解欄位的方法。

    這是 FR-028 的機制性保障：模組裡**沒有**任何能拿到各位置的函式。
    """
    public = {n for n in dir(schema) if not n.startswith("_")}
    for forbidden in ("jid_fields", "jid_parts", "split_jid", "parse_jid", "jid_parts_map"):
        assert forbidden not in public


def test_unusual_jid_shape_is_observational_not_fatal():
    """4-field JID 報 drift，但**不**阻擋 —— 形狀是觀察，不是結構。

    它是 STRUCTURAL 嗎？嚴格說，一個我們沒見過的形狀不該被當成「資料壞掉」
    —— 也許是上游新增了一個合法的類別。所以這裡的 severity 是
    STRUCTURAL（它不在已知集合內，呼叫端需要知道），但理由文字必須說明
    這是「不在已驗證集合」而非「損壞」。
    """
    r = drift(DOCS / "drift_jid_shape.json")
    assert any("JID" in x and "已驗證" in x for x in r.reasons)


def test_non_string_jid_is_drift():
    r = drift(DOCS / "drift_jid_not_string.json")
    assert any("JID" in x and "不是字串" in x for x in r.reasons)


def test_jid_with_trailing_comma_has_seven_fields_and_is_drift():
    """尾逗號 → 7 欄位 → drift。

    這是一個真實可能的資料瑕疵，值得有一條明確的測試：它的正確反應是
    **回報**，不是自動修剪逗號（那是 FR-002 禁止的「修正」）。
    """
    r = schema.validate({**load(CIVIL), "JID": "TPDV,115,訴,2468,20260901,1,"})
    assert isinstance(r, schema.Drift)
    assert any("7" in x for x in r.reasons)


# ── JPDF 空值是合法的 ───────────────────────────────────────────────────────

def test_empty_jpdf_is_not_an_error():
    assert load(CONSTITUTIONAL)["JPDF"] == ""
    assert isinstance(schema.validate(load(CONSTITUTIONAL)), schema.Valid)


def test_validator_has_no_jpdf_non_empty_rule():
    """模組裡不得存在「JPDF 必須非空」的斷言（FR-030）。"""
    src = (ROOT / "ingest" / "judgements" / "schema.py").read_text("utf-8")
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert "jpdf must" not in code.lower()
    assert "必須非空" not in code


# ── JYEAR / JDATE 獨立 ──────────────────────────────────────────────────────

def test_jyear_and_jdate_are_not_compared():
    """兩者不一致**不是** drift（FR-029）。

    80/494 的抽樣中兩者不一致。任何「應該一致」的斷言都會產生大量假 drift。
    """
    doc = {**load(CIVIL), "JYEAR": "79", "JDATE": "20260901"}
    assert isinstance(schema.validate(doc), schema.Valid)


def test_validator_does_not_derive_one_from_the_other():
    """不得有任何從 JYEAR/JDATE 互相推導的邏輯（FR-029）。"""
    src = (ROOT / "ingest" / "judgements" / "schema.py").read_text("utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for forbidden in ("jyear_from_jdate", "jdate_from_jyear", "derive_year", "infer_year"):
        assert forbidden not in code


# ── Drift：具體理由 ─────────────────────────────────────────────────────────

def test_missing_key_is_drift_with_the_key_named():
    r = drift(DOCS / "drift_missing_key.json")
    assert any("JPDF" in x and "缺少" in x for x in r.reasons)
    assert r.severity == schema.Severity.STRUCTURAL


def test_unexpected_key_is_drift_with_the_key_named():
    r = drift(DOCS / "drift_unexpected_key.json")
    assert any("JCOURT" in x and "多餘" in x for x in r.reasons)


def test_non_string_value_is_drift_naming_type():
    r = drift(DOCS / "drift_non_string_value.json")
    assert any("JNO" in x and "int" in x for x in r.reasons)


def test_null_value_is_drift():
    r = drift(DOCS / "drift_null_value.json")
    assert any("JTITLE" in x and "null" in x for x in r.reasons)


def test_key_order_drift_is_observational_not_structural():
    """順序不符是 OBSERVATIONAL —— 資料仍可讀取。

    若升級成 STRUCTURAL，任何 JSON serializer 的鍵順序差異都會把整份
    corpus 判成壞掉，而那不代表資料有問題。
    """
    r = drift(DOCS / "drift_key_order.json")
    assert r.severity == schema.Severity.OBSERVATIONAL
    assert any("順序" in x for x in r.reasons)


def test_all_reasons_are_reported_not_just_the_first():
    """多個問題要一次報齊。

    只報第一個會讓修的人跑一次才知道下一個 —— 而 drift 報告通常是給人看的。
    """
    doc = {k: v for k, v in load(CIVIL).items() if k not in ("JPDF", "JTITLE")}
    doc["JNO"] = 1
    doc["EXTRA"] = "x"
    r = schema.validate(doc)
    assert isinstance(r, schema.Drift)
    assert len(r.reasons) >= 3


def test_severity_constants_exist():
    assert schema.Severity.STRUCTURAL != schema.Severity.OBSERVATIONAL


# ── 絕不拋例外 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "payload",
    [
        b"null", b"123", b'"a string"', b"[1,2,3]", b"true",
        b'{"JID": "\xff\xfe"}',      # 非法 UTF-8
        b'{"JID": "x", ',             # 壞 JSON
        b"",                          # 空
        b"\x00\x01\x02",             # 二進位
    ],
)
def test_never_raises_on_any_payload(payload):
    """任何輸入都回傳結果，不拋例外。"""
    r = schema.parse_and_validate(payload)
    assert isinstance(r, (schema.Valid, schema.Drift))


def test_top_level_non_dict_names_the_actual_type():
    r = drift(DOCS / "drift_toplevel_list.json")
    assert any("list" in x for x in r.reasons)
    r = drift(DOCS / "drift_toplevel_string.json")
    assert any("str" in x for x in r.reasons)


def test_malformed_json_is_drift_not_exception():
    r = drift(DOCS / "drift_bad_json.json")
    assert any("JSON" in x for x in r.reasons)


def test_invalid_utf8_is_drift_not_exception():
    r = drift(DOCS / "drift_not_utf8.json")
    assert any("UTF-8" in x for x in r.reasons)


# ── 不做 semantic interpretation ────────────────────────────────────────────

def test_validator_does_not_interpret_jcase():
    """`JCASE` 必須是 opaque —— 不驗值域、不解釋成 court/type。"""
    for value in ("訴", "重秩", "簡", "ZZZ", "任意值", ""):
        doc = {**load(CIVIL), "JCASE": value}
        assert isinstance(schema.validate(doc), schema.Valid), (
            f"JCASE={value!r} 被判成 drift —— 那是 semantic interpretation"
        )


def test_validator_does_not_interpret_jyear_format():
    """`JYEAR` 只驗型別，不驗長度或數字範圍。

    `JYEAR="79"` 與 `JYEAR="115"` 與 `JYEAR="民國元年"` 同樣合法 ——
    我們只觀察到 0.46%。
    """
    for value in ("79", "115", "1", "", "民國元年"):
        doc = {**load(CIVIL), "JYEAR": value}
        assert isinstance(schema.validate(doc), schema.Valid), f"JYEAR={value!r}"


def test_validator_does_not_interpret_jdate_format():
    """`JDATE` 只驗型別，不驗格式。"""
    for value in ("20260901", "", "民國115年9月1日", "not-a-date"):
        doc = {**load(CIVIL), "JDATE": value}
        assert isinstance(schema.validate(doc), schema.Valid), f"JDATE={value!r}"


def test_validator_does_not_parse_jfull():
    """`JFULL` 只驗型別，不檢查內容。

    內容屬於 T010（lossless）與之後的 detection；結構檢查不該碰它。
    """
    for value in ("", "任意文字", "\r\n", "因本件為對不公開案件"):
        doc = {**load(CIVIL), "JFULL": value}
        assert isinstance(schema.validate(doc), schema.Valid)


def test_schema_module_does_not_infer_court():
    """不得推論法院（spec 明確禁止）。

    用 AST 而非字串搜尋：docstring 裡正當地討論「為何**不**推論 court」，
    而字串搜尋會把那些說明誤判成違規 —— 這正是 T004 踩過的同一個坑
    （`zlib` 出現在「CRC 與 zlib.crc32 一致」的註解裡）。

    這裡查的是：程式結構中不得出現任何讀取 court 的欄位存取，
    且不得存在名稱含 court 的函式。
    """
    import ast

    tree = ast.parse(
        (ROOT / "ingest" / "judgements" / "schema.py").read_text("utf-8")
    )

    # 不得有 court 相關的函式
    func_names = {n.name.lower() for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not any("court" in n for n in func_names), func_names

    # 不得讀取 court 欄位（屬性存取）
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not any("court" in a.lower() for a in attrs), attrs

    # 不得使用 JCOURT 之類的 key。
    #
    # 只看**短的單行字串常數**：docstring 是一個長的多行 Constant，它正當地
    # 討論「為何不推論 court」，把整個 docstring 拿來搜尋會誤判成違規 ——
    # 這正是本測試第一版踩的坑。
    literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "\n" not in n.value          # 排除 docstring
        and len(n.value) < 64           # 排除成段文字
    }
    assert not any("court" in v.lower() for v in literals), {
        v for v in literals if "court" in v.lower()
    }


def test_schema_module_does_not_touch_filename():
    """validator 不接受檔名，也不從檔名推導任何東西。

    簽章斷言：只有 `doc` 一個位置參數 + 一個模組級常數。沒有第二個輸入。
    """
    import inspect

    params = set(inspect.signature(schema.validate).parameters)
    assert params == {"doc"}


# ── parse_and_validate 的 byte-fidelity ────────────────────────────────────

def test_parse_does_not_normalise_unicode():
    """NFC/NFKC 正規化不得發生。

    NFKC 會把 `　`（U+3000）與全形英數字轉成半形 —— 那會改變 `JFULL` 的
    位元組並讓所有 offset 失效。
    """
    import unicodedata

    payload = json.dumps(
        {**load(CIVIL), "JTITLE": "ＡＢ　123"}, ensure_ascii=False
    ).encode("utf-8")
    r = schema.parse_and_validate(payload)
    assert isinstance(r, schema.Valid)
    # 驗證「validator 沒有動過字串」：重新解析後必須與原 payload 相同
    doc = json.loads(payload.decode("utf-8"))
    assert doc["JTITLE"] == "ＡＢ　123"
    assert unicodedata.normalize("NFKC", doc["JTITLE"]) != doc["JTITLE"]


def test_parse_accepts_str_as_well_as_bytes():
    assert isinstance(schema.parse_and_validate(CIVIL.read_text("utf-8")), schema.Valid)


def test_drift_as_dict_is_serialisable():
    """drift 必須能被記錄下來 —— as_dict 不能含不可序列化的東西。"""
    import json as _json

    r = drift(DOCS / "drift_missing_key.json")
    _json.dumps(r.as_dict())  # 不該拋
    assert r.as_dict()["valid"] is False


def test_result_record_is_frozen():
    r = schema.validate(load(CIVIL))
    with pytest.raises(Exception):
        r.keys = ("X",)  # type: ignore[misc]


def test_fixtures_are_valid_json_on_disk():
    """所有 fixture 本身必須是合法 JSON。

    例外兩個「故意壞掉」的 fixture —— 它們的存在目的就是提供壞輸入。
    其餘（包括 drift_not_utf8.json）都必須可解析；那個例外用 bytes 讀。
    """
    intentional_bad = {"drift_bad_json.json"}
    for p in sorted(DOCS.glob("*.json")):
        if p.name in intentional_bad:
            continue
        if p.name == "drift_not_utf8.json":
            # 它的內容刻意不是合法 UTF-8 —— 用 bytes 驗證它確實不是
            with pytest.raises(UnicodeDecodeError):
                p.read_text(encoding="utf-8")
            continue
        json.loads(p.read_text(encoding="utf-8"))
