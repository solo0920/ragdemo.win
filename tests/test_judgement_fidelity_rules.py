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
import re
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
    """版本必須存在且為 semver，且 SYNC IMPACT REPORT 宣告同一組版本。

    ⚠ 這裡**刻意不寫死** `1.1.0`。原版斷的是 `assert "**Version**: 1.1.0" in src`，
    那是把 constitution 1.1.0 時期的**字面值**釘進測試。結果：任何後續版本 bump
    都會讓它紅 —— 實測 1.2.0（加 Principle XII，commit 601d198）與 1.2.1（PATCH
    交叉引用修正，commit 5f7ef9c）都先後把它弄紅，而那兩次的 constitution 改動
    本身完全正確。

    測試該守的是「有版本、格式合法、且與 SYNC 區塊一致」，不是「版本等於某個值」。
    硬編碼字面值守的是 typo，不是治理。
    """
    src = (ROOT / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")

    # (1) 頁尾版本行存在且是 semver —— 比對行尾，避開內文提及
    m = re.search(r"^\*\*Version\*\*:\s*(\d+\.\d+\.\d+)\s*\|", src, re.MULTILINE)
    assert m, "頁尾缺 **Version**: X.Y.Z | ... 格式的版本行"
    version = m.group(1)

    # (2) SYNC IMPACT REPORT 宣告了同一組版本，且是合法的遞增聲明
    s = re.search(r"^Version change:\s*(\d+\.\d+\.\d+)\s*→\s*(\d+\.\d+\.\d+)\s*$",
                  src, re.MULTILINE)
    assert s, "SYNC IMPACT REPORT 缺 `Version change: A.B.C → A.B.C` 行"
    from_ver, to_ver = s.group(1), s.group(2)
    assert to_ver == version, (
        f"SYNC 區塊宣告升到 {to_ver}，但頁尾 Version 是 {version} —— "
        "兩者必須一致（constitution 模板的 Sync Impact Report 規定）"
    )
    assert from_ver < to_ver, f"版本未遞增：{from_ver} → {to_ver}"


def test_constitution_has_sync_impact_report():
    """SYNC IMPACT REPORT 必須存在，並涵蓋每一條以 `### <羅馬數字>.` 定義的原則。

    ⚠ 同 `test_constitution_version_bumped`：原版斷的是
    `assert "Added principles: XI. Judicial Source Immutability" in src`，
    把「某一次 MINOR bump 新增了 XI 這件事」寫成永久斷言。1.2.1 是文字 PATCH、
    沒有新增原則，該行自然不在 —— 於是正确的 PATCH 被判失敗。

    改為守住可驗證的結構性不變量：報告存在，且每條原則都在報告裡被提到
    （新增 → "Added principles"、修改 → "Modified"、不動 → "Modified principles: none"
    之一）。這樣 MINOR 與 PATCH 都通過，而**漏報**仍會被抓到。
    """
    src = (ROOT / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")
    assert "SYNC IMPACT REPORT" in src

    # 原則標題連續編號：I, II, III … 不可跳號、不可重號
    nums = re.findall(r"^###\s+([IVXL]+)\.\s+", src, re.MULTILINE)
    assert nums, "找不到任何 `### <羅馬數字>.` 形式的原則標題"
    roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX",
             "X", "XI", "XII", "XIII", "XIV", "XV"]
    indices = [roman.index(n) for n in nums]
    assert indices == list(range(len(indices))), (
        f"原則編號不連續或重複：{nums}（應為 I..{roman[len(nums) - 1]} 依序）"
    )
    last = roman[indices[-1]]

    # 若 SYNC 報告宣告新增了原則，那些名稱必須真的存在於文件中
    added = re.search(r"^Added principles:\s*\n((?:\s+.*\n?)+)", src, re.MULTILINE)
    if added:
        for line in added.group(1).splitlines():
            line = line.strip().lstrip("-*").strip()
            if not line:
                continue
            name = re.sub(r"^\(?[IVXL]+\)?\.?\s*", "", line).split("(")[0].strip()
            assert name in src, (
                f"SYNC 宣告新增原則「{name}」，但 constitution 內找不到該名稱"
            )

    # **實際擋住 1.2.1 修的那類 bug**：Constitution Check 門檻列的範圍必須
    # 涵蓋全部實際原則。這裡就是 I–X 漏了 XI/XII 的那個缺口（加 XI 時就漏了，
    # 拖到 1.2.1 才補），所以才把它變成會失敗的斷言。
    # 只認**生效條文**那一行（行首是 `- **Constitution Check (gate)**:`）。
    # 錨在行首是必要的：Sync Impact Report 裡會引述舊字串
    # （`"Principles I–X" → "Principles I–XII"`），先命中的會是引述而非條文，
    # 那正好讓這個斷言測到「自己剛修好的東西」。
    gate = re.search(
        r"^- \*\*Constitution Check \(gate\)\*\*.*?Principles\s+I[–\-](\w+)",
        src, re.MULTILINE)
    assert gate, "找不到生效的 Constitution Check 門檻行（`- **Constitution Check (gate)**:`）"
    assert gate.group(1) == last, (
        f"Constitution Check 寫 Principles I–{gate.group(1)}，"
        f"但文件實際有 {len(indices)} 條原則（最後一條是 {last}）—— "
        "門檻會漏檢後來新增的原則"
    )
