"""司法判決管線的六大 invariant 稽核（spec 004 T023）。

## 為什麼要有這個檔案

T001–T022 各自守住一個 stage 的契約，但「整條管線合起來仍然保真」需要一個
高層次的可執行聲明。本檔案對應 spec 的六個 invariants：

| Invariant | 含義 | 本檔案測試 |
|---|---|---|
| INV-SRC | `JFULL` 永遠是原始 source text | 文件建立後與 source JSON 比對 |
| INV-SUB | 每個 chunk/candidate 都是 `JFULL[start:end]` | chunk 切片、candidate 切片 |
| INV-PROV | 每個 derived object 能追溯到 artifact→entry→field→offset | provenance.chain |
| INV-NOFAB | 不生成 statute name / summary / conclusion | candidate 沒有 statute name、模組無 gazetteer |
| INV-REPRO | 同一 artifact + decoder + algorithm → 相同輸出 | 重跑 chunking / extraction / payload 比 digest |
| INV-SEP | authoritative text 與 derived metadata 分開存 | payload 不含 `JFULL`、document 含 `JFULL` |

## fixture 策略

所有 unit tests 使用 `tests/fixtures/judgements/docs_fixture.rar` 裡的小檔案
（store method，不需要 production RAR 或 decoder）。
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

import artifact as ART  # noqa: E402
import chunk as CH  # noqa: E402
import citation_patterns as CP  # noqa: E402
import citations as CT  # noqa: E402
import document as DOC  # noqa: E402
import extract as EX  # noqa: E402
import index_load as IL  # noqa: E402
import inventory as INV  # noqa: E402
import provenance as PV  # noqa: E402
import schema as SCH  # noqa: E402
import text as TXT  # noqa: E402

DOCS = ROOT / "tests" / "fixtures" / "judgements" / "docs"
FIXTURE_RAR = ROOT / "tests" / "fixtures" / "judgements" / "docs_fixture.rar"
ARTIFACT_SHA256 = ART.ARTIFACT_SHA256


def all_doc_paths() -> list[Path]:
    return sorted(p for p in DOCS.glob("*.json") if not p.name.startswith("drift_"))


def load_doc(p: Path, entry_path: str = "") -> DOC.JudgmentDocument:
    data = json.loads(p.read_text(encoding="utf-8"))
    return DOC.from_document(
        data,
        entry_path=entry_path or f"docs\\{p.name}",
        sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
    )


# ── INV-SRC：JFULL 是原始 source text ────────────────────────────────────────


@pytest.mark.parametrize("path", all_doc_paths(), ids=lambda p: p.name)
def test_inv_src_document_preserves_jfull(path: Path):
    """JudgmentDocument.jfull 與 source JSON 的 JFULL 逐字相同。"""
    source = json.loads(path.read_text(encoding="utf-8"))
    doc = load_doc(path)
    assert doc.jfull == source["JFULL"]


@pytest.mark.parametrize("path", all_doc_paths(), ids=lambda p: p.name)
def test_inv_src_lossless_text_roundtrips(path: Path):
    doc = load_doc(path)
    t = TXT.LosslessText.from_string(doc.jfull)
    assert t.text == doc.jfull
    assert t.raw == doc.jfull.encode("utf-8")


# ── INV-SUB：chunk / candidate 都是 JFULL[start:end] ──────────────────────────


@pytest.mark.parametrize("path", all_doc_paths(), ids=lambda p: p.name)
def test_inv_sub_chunk_text_equals_jfull_slice(path: Path):
    doc = load_doc(path)
    for c in CH.chunk_document(doc, chunk_size=80):
        assert c.text == doc.jfull[c.start_offset : c.end_offset]


def test_inv_sub_candidate_text_equals_jfull_slice():
    s = "　　主　　　文\r\n　　依刑法第55條、第56條辦理。"
    candidates = CT.detect(TXT.LosslessText.from_string(s))
    assert candidates
    for cand in candidates:
        assert cand.matched_text == s[cand.start_offset : cand.end_offset]


# ── INV-PROV：每個 derived object 追溯到 artifact → entry → field → offset ───


@pytest.mark.parametrize("path", all_doc_paths(), ids=lambda p: p.name)
def test_inv_prov_chunk_resolves_to_five_fields(path: Path):
    doc = load_doc(path)
    chunks = CH.chunk_document(doc, chunk_size=80)
    assert chunks
    c = chunks[0]
    p = PV.chain(c, artifact_sha256=ARTIFACT_SHA256)
    assert p.as_dict() == {
        "artifact_sha256": ARTIFACT_SHA256,
        "entry_path": doc.entry_path,
        "json_field": "JFULL",
        "start_offset": c.start_offset,
        "end_offset": c.end_offset,
    }


def test_inv_prov_candidate_resolves_to_five_fields():
    s = "爰依民法第185條規定辦理。"
    candidates = CT.detect(
        TXT.LosslessText.from_string(s),
        entry_path="docs\\x.json",
        provenance={"artifact_sha256": ARTIFACT_SHA256},
    )
    cand = candidates[0]
    p = PV.chain(cand)
    assert p.json_field == "JFULL"
    assert p.entry_path == "docs\\x.json"
    assert p.artifact_sha256 == ARTIFACT_SHA256


# ── INV-NOFAB：不生成 statute name / summary / conclusion ────────────────────


def test_inv_nofab_candidate_statute_name_is_none():
    s = "依民法第1條"
    c = CT.detect(TXT.LosslessText.from_string(s))[0]
    assert c.statute_name is None
    assert c.article_number is None


def test_inv_nofab_citation_patterns_has_no_gazetteer():
    assert CP.has_gazetteer() is False
    assert CP.gazetteer_size() == 0


def test_inv_nofab_citations_module_has_no_resolver():
    """`citations.py` 不得有 resolution entry point。"""
    import inspect

    src = inspect.getsource(CT)
    for forbidden in ("resolve_statute", "lookup_law", "statute_name_of"):
        assert forbidden not in src, f"citations.py 含 {forbidden}（違反 FR-037）"


# ── INV-REPRO：重跑得到相同輸出 ─────────────────────────────────────────────


@pytest.mark.parametrize("path", all_doc_paths(), ids=lambda p: p.name)
def test_inv_repro_chunking_is_stable(path: Path):
    doc = load_doc(path)
    a = CH.chunk_document(doc, chunk_size=80)
    b = CH.chunk_document(doc, chunk_size=80)
    assert [c.as_dict() for c in a] == [c.as_dict() for c in b]


def test_inv_repro_payload_is_stable():
    doc = load_doc(DOCS / "civil_6field.json")
    chunks = CH.chunk_document(doc, chunk_size=80)
    a = IL.build_payload(chunks[0], doc)
    b = IL.build_payload(chunks[0], doc)
    assert a == b


def test_inv_repro_extraction_is_stable():
    """同一 entry 解壓兩次 → byte-identical。"""
    if not FIXTURE_RAR.exists():
        pytest.skip("docs_fixture.rar 不存在")
    entries = [e for e in INV.inventory(FIXTURE_RAR) if not e.is_dir]
    assert entries, "fixture RAR 應有檔案"
    e = entries[0]
    import tempfile

    with tempfile.TemporaryDirectory() as da:
        with tempfile.TemporaryDirectory() as db:
            a = EX.extract_one(e.path, archive=FIXTURE_RAR, dest=Path(da))
            b = EX.extract_one(e.path, archive=FIXTURE_RAR, dest=Path(db))
            assert a.data == b.data
            assert a.sha256 == b.sha256


# ── INV-SEP：authoritative text 與 derived metadata 分開 ─────────────────────


def test_inv_sep_payload_does_not_contain_jfull():
    doc = load_doc(DOCS / "civil_6field.json")
    chunks = CH.chunk_document(doc, chunk_size=80)
    payload = IL.build_payload(chunks[0], doc)
    assert "jfull" not in payload
    assert "text" not in payload


def test_inv_sep_document_stores_jfull_separately():
    doc = load_doc(DOCS / "civil_6field.json")
    assert doc.jfull
    # document model 的 as_provenance 刻意不含 jfull
    assert "jfull" not in doc.as_provenance()


def test_inv_sep_forbidden_fields_listed():
    """index_load 明列禁止欄位，確保未來加欄位時會被看見。"""
    assert "court" in IL.FORBIDDEN_FIELDS
    assert "statute" in IL.FORBIDDEN_FIELDS
    assert "summary" in IL.FORBIDDEN_FIELDS


# ── schema drift 偵測（INV-SRC 的延伸）────────────────────────────────────────


def test_inv_src_schema_validator_reports_drift_not_exception():
    bad = {"JID": "x", "JYEAR": "115"}  # 缺少 keys
    result = SCH.validate(bad)
    assert not result.valid
    assert isinstance(result, SCH.Drift)


# ── T025：DESIGN.md obsolete markers ─────────────────────────────────────────


def test_design_doc_boilerplate_stripping_marked_obsolete():
    src = (ROOT / "ingest" / "cases" / "DESIGN.md").read_text(encoding="utf-8")
    assert "[OBSOLETE — FR-002]" in src
    assert "去 boilerplate" in src
    assert "刪除內容是對司法原文" in src


def test_design_doc_llm_summary_marked_obsolete():
    src = (ROOT / "ingest" / "cases" / "DESIGN.md").read_text(encoding="utf-8")
    assert "[OBSOLETE — FR-005" in src
    assert "要旨" in src
    assert "LLM 輸出不得被存成權威司法內容" in src


def test_design_doc_court_regex_marked_obsolete():
    src = (ROOT / "ingest" / "cases" / "DESIGN.md").read_text(encoding="utf-8")
    assert "[OBSOLETE — FR-005 / FR-016]" in src
    assert "regex 即可" in src


def test_design_doc_pii_check_marked_deferred_not_forbidden():
    src = (ROOT / "ingest" / "cases" / "DESIGN.md").read_text(encoding="utf-8")
    assert "[DEFERRED — FR-032" in src
    assert "不是「禁止」的動作" in src


# ── T026：Constitution principle ─────────────────────────────────────────────


def test_constitution_has_judicial_immutability_principle():
    src = (ROOT / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")
    # 四個核心主張
    assert "Judicial Source Immutability" in src
    assert "Model output is not authoritative judicial or regulatory content" in src
    assert "Answers derived from source text MUST retain provenance" in src
    assert "Retrieval MUST NOT alter source content" in src


def test_constitution_cross_references_principle_vi():
    src = (ROOT / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")
    # 新原則應該引用 Principle VI，而不是複製它
    assert "Principle VI" in src
    assert "RAG Correctness and Traceability" in src


def test_constitution_version_bumped():
    src = (ROOT / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")
    # 1.0.0 → 1.1.0（minor bump）
    assert "**Version**: 1.1.0" in src


def test_constitution_has_sync_impact_report():
    src = (ROOT / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")
    assert "SYNC IMPACT REPORT" in src
    assert "Added principles: XI. Judicial Source Immutability" in src
