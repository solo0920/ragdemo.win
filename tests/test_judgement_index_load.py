"""`index_load.py` 的契約測試（spec 004 T017 / FR-013, FR-014）。

## 這支測試不做什麼

**不連 Qdrant。** repo 既有慣例是資料庫測試只做靜態斷言（`ingest/laws` 的
`ingest` 測試也是）。payload 建構是純函式，所以可以在沒有服務的情況下完整
驗證。

## 三條必須成立的契約

1. **point ID deterministic** —— 重跑覆寫而非重複（acceptance 的原文）。
2. **payload 欄位集合固定** —— 多一個或少一個都算違約。
3. **payload 不含法條身分與推論欄位** —— FR-037 / FR-016。

第 3 條特別重要：payload 是檢索時**不必回頭 join** 就能用的 metadata。
若裡面有一個 `statute_name`，檢索端就會直接拿它去顯示，而那個值只會是猜的。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JDIR = ROOT / "ingest" / "judgements"
if str(JDIR) not in sys.path:
    sys.path.insert(0, str(JDIR))

import chunk as CH  # noqa: E402
import document  # noqa: E402
import index_load as Q  # noqa: E402

DOCS = ROOT / "tests" / "fixtures" / "judgements" / "docs"
CIVIL = DOCS / "civil_6field.json"
CONSTITUTIONAL = DOCS / "constitutional_5field_empty_jpdf.json"


def make_doc(p: Path = CIVIL) -> document.JudgmentDocument:
    return document.from_document(
        json.loads(p.read_text(encoding="utf-8")),
        entry_path="202607\\x\\TPDV,115,訴,2468,20260901,1.json",
        sha256="a" * 64,
    )


def first_chunk(doc=None, size: int = 200) -> CH.Chunk:
    d = doc or make_doc()
    return CH.chunk_document(d, chunk_size=size)[0]


# ── payload 欄位集合 ───────────────────────────────────────────────────────

def test_payload_fields_match_tasks_md_exactly():
    """payload 欄位必須是 tasks.md 列出���那一組。

    tasks.md T017 Output：`{entry_path, jid, chunk_index, start_offset,
    end_offset, content_hash, jyear, jdate, jcase}`。
    """
    assert set(Q.PAYLOAD_FIELDS) == {
        "entry_path", "jid", "chunk_index", "start_offset", "end_offset",
        "content_hash", "jyear", "jdate", "jcase",
    }


def test_build_payload_has_exactly_the_declared_fields():
    p = Q.build_payload(first_chunk(), make_doc())
    assert set(p) == set(Q.PAYLOAD_FIELDS)


def test_payload_contract_assertion_passes():
    Q.assert_payload_contract(Q.build_payload(first_chunk(), make_doc()))


def test_payload_contract_rejects_extra_field():
    p = {**Q.build_payload(first_chunk(), make_doc()), "extra": 1}
    with pytest.raises(Q.PayloadContractError):
        Q.assert_payload_contract(p)


def test_payload_contract_rejects_missing_field():
    p = Q.build_payload(first_chunk(), make_doc())
    del p["jyear"]
    with pytest.raises(Q.PayloadContractError):
        Q.assert_payload_contract(p)


def test_payload_contract_rejects_forbidden_field():
    """加一個 `statute_name` 必須被擋。"""
    p = {**Q.build_payload(first_chunk(), make_doc()), "statute_name": "刑法"}
    with pytest.raises(Q.PayloadContractError, match="禁止欄位"):
        Q.assert_payload_contract(p)


@pytest.mark.parametrize("field", Q.FORBIDDEN_FIELDS)
def test_each_forbidden_field_is_named(field):
    """每個禁止欄位都必須在清單裡（逐一驗證，不只驗集合非空）。"""
    assert field in Q.FORBIDDEN_FIELDS


# ── FR-037：不得含法條身分 ────────────────────────────────────────────────

def test_payload_has_no_statute_identity():
    """payload 不得帶法條名稱或條號（FR-037）。"""
    p = Q.build_payload(first_chunk(), make_doc())
    for forbidden in ("statute", "statute_name", "law", "article", "article_number"):
        assert forbidden not in p


def test_payload_has_no_court_inference():
    """不得有 court —— source 沒有這個欄位，推論它是 FR-016 禁止的。"""
    p = Q.build_payload(first_chunk(), make_doc())
    assert "court" not in p


def test_module_has_no_resolver():
    import ast

    tree = ast.parse((JDIR / "index_load.py").read_text("utf-8"))
    public = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    }
    for forbidden in ("resolve", "resolve_statute", "lookup_law", "infer_court"):
        assert forbidden not in public


def test_module_does_not_read_a_gazetteer():
    import ast

    src = (JDIR / "index_load.py").read_text("utf-8")
    tree = ast.parse(src)
    code_literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "\n" not in n.value
        and len(n.value) < 64
    }
    assert not any("laws_meta" in v for v in code_literals)


# ── deterministic point ID ─────────────────────────────────────────────────

def test_point_id_is_deterministic_across_runs():
    c = first_chunk()
    assert Q.point_id(c) == Q.point_id(c)
    assert Q.point_id(c) == Q.point_id(first_chunk())


def test_point_id_is_64_hex():
    pid = Q.point_id(first_chunk())
    assert isinstance(pid, str)
    assert len(pid) == 64
    int(pid, 16)  # 必須是合法 hex


def test_point_ids_are_unique_per_chunk():
    cs = CH.chunk_document(make_doc(), chunk_size=100)
    ids = [Q.point_id(c) for c in cs]
    assert len(set(ids)) == len(ids)


def test_rechunking_reproduces_the_same_point_ids():
    """同一份文件、同一個 chunk_size，重跑得到相同 ID。

    這是「重跑覆寫而非重複」的基礎：若 ID 會變，舊 points 變成孤兒。
    """
    a = [Q.point_id(c) for c in CH.chunk_document(make_doc(), chunk_size=200)]
    b = [Q.point_id(c) for c in CH.chunk_document(make_doc(), chunk_size=200)]
    assert a == b


def test_point_id_does_not_depend_on_chunk_text():
    """point ID 只由 (entry_path, chunk_index) 決定。

    刻意**不含** `jfull_sha256`：若含了，一份被重新發布的判決（內容變了）
    會得到全新 ID，而舊 points 變成孤兒 —— 那需要一個刪除流程，而刪除語意
    是尚未決定的 maintainer decision（T016）。不含它則內容變更時覆寫同一個
    point，那是正確行為。
    """
    doc = make_doc()
    a = CH.chunk_document(doc, chunk_size=200)[0]
    # 同一個 index，但文件內容不同
    doc2 = document.from_document(
        {**doc.source_dict(), "JFULL": "完全不同的新內容。"},
        entry_path=doc.entry_path,
        sha256=doc.sha256,
    )
    b = CH.chunk_document(doc2, chunk_size=200)[0]
    assert a.text != b.text
    assert Q.point_id(a) == Q.point_id(b)


def test_point_id_differs_between_documents():
    """不同 entry → 不同 point ID。

    ## 為什麼這裡必須給**不同的 entry_path**

    `point_id` 的組成是 `sha256(entry_path | chunk_index)`，所以同一個
    `entry_path` 下的兩個不同 document 會得到**相同**的 ID。那是正確行為，
    不是 bug：`entry_path` 是 archive 內唯一的座標（T016 對它建了 unique
    index），而兩個 document 若有相同 entry_path，代表 identity 已經壞了 ——
    那應該被 `test_judgment_entry_path_is_unique` 那類斷言抓到，不是靠
    point ID 去補。
    """
    a_doc = make_doc()
    b_doc = document.from_document(
        json.loads(CONSTITUTIONAL.read_text(encoding="utf-8")),
        entry_path="202607\\y\\JCCC,115,審裁,1158,20260701.json",
        sha256="c" * 64,
    )
    a = first_chunk(a_doc)
    b = first_chunk(b_doc)
    assert a.source_document != b.source_document
    assert Q.point_id(a) != Q.point_id(b)


# ── payload 的值 ───────────────────────────────────────────────────────────

def test_payload_values_map_correctly():
    """逐欄位比對 —— 順序錯了型別仍是 int，不會報錯。"""
    doc = make_doc()
    c = first_chunk(doc)
    p = Q.build_payload(c, doc)
    assert p["entry_path"] == doc.entry_path
    assert p["jid"] == doc.jid
    assert p["chunk_index"] == c.chunk_index
    assert p["start_offset"] == c.start_offset
    assert p["end_offset"] == c.end_offset
    assert p["content_hash"] == c.sha256
    assert p["jyear"] == doc.jyear
    assert p["jdate"] == doc.jdate
    assert p["jcase"] == doc.jcase


def test_offsets_in_payload_are_authoritative():
    """payload 的 offsets 必須能切出 chunk text。"""
    doc = make_doc()
    for c in CH.chunk_document(doc, chunk_size=100):
        p = Q.build_payload(c, doc)
        assert doc.jfull[p["start_offset"] : p["end_offset"]] == c.text


def test_jyear_and_jdate_are_separate_fields():
    """兩者獨立存在（FR-029），不得合併。"""
    p = Q.build_payload(first_chunk(), make_doc())
    assert p["jyear"] != p["jdate"]
    assert "jyear" in p and "jdate" in p


def test_jcase_is_opaque():
    """`jcase` 是來源值本身，不是被解釋過的類型。"""
    doc = make_doc()
    p = Q.build_payload(first_chunk(doc), doc)
    assert p["jcase"] == doc.jcase
    assert "case_type" not in p


def test_5field_jid_survives_into_payload():
    """5-field JID 照原樣進 payload（FR-028）。"""
    doc = make_doc(CONSTITUTIONAL)
    p = Q.build_payload(first_chunk(doc), doc)
    assert p["jid"] == "JCCC,115,審裁,1158,20260701"


def test_payload_does_not_contain_full_text():
    """payload 不得含 `jfull`。

    放了會讓每個 point 攜帶整份判決（最大 777 KB），collection 體會爆炸。
    要檢索的是 chunk 自己的 text。
    """
    doc = make_doc()
    p = Q.build_payload(first_chunk(doc), doc)
    assert "jfull" not in p
    assert "text" not in p
    assert doc.jfull not in json.dumps(p, ensure_ascii=False)


# ── point 結構 ─────────────────────────────────────────────────────────────

def test_build_point_has_id_and_payload_only():
    """point 只有 `id` 與 `payload` —— **沒有** vector。

    刻意不放一個空陣列去「看起來完整」：那會讓一個沒有向量的 point 被寫進
    去，而那在檢索時是**靜默失效**的。
    """
    pt = Q.build_point(first_chunk(), make_doc())
    assert set(pt) == {"id", "payload"}
    assert "vector" not in pt


def test_point_is_json_serialisable():
    pt = Q.build_point(first_chunk(), make_doc())
    json.dumps(pt, ensure_ascii=False)  # 不該拋


def test_point_survives_round_trip():
    pt = Q.build_point(first_chunk(), make_doc())
    back = json.loads(json.dumps(pt, ensure_ascii=False))
    assert back["id"] == pt["id"]
    assert back["payload"] == pt["payload"]


def test_collection_name():
    assert Q.COLLECTION == "judgements"


# ── 這個模組不做什麼 ───────────────────────────────────────────────────────

def test_module_does_not_import_http_or_vector_libraries():
    """不得連線、不得產生向量。

    embedding 屬於這個 batch 之後，而 FR-013 規定它只能用來定位原文。
    """
    import ast

    tree = ast.parse((JDIR / "index_load.py").read_text("utf-8"))
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert not (imported & {"httpx", "requests", "urllib", "qdrant_client",
                            "numpy", "torch", "openai", "ollama", "psycopg"})


def test_module_has_no_ranking_fields():
    """不得有 score / rank —— ranking 是查詢時算的，不是存進去的。"""
    for f in Q.PAYLOAD_FIELDS:
        assert f not in ("score", "rank", "relevance", "similarity")


def test_payload_does_not_store_citation_candidates():
    """citation candidates 不得進 payload。

    它們是 derived 資料，而把它們存進去會造成一個誘惑：有人以為 payload 裡
    的 citation 是查證過的法律引用。實際上它們只是「形狀像引用的文字」。
    """
    p = Q.build_payload(first_chunk(), make_doc())
    for forbidden in ("citations", "candidates", "citation_count", "references"):
        assert forbidden not in p
