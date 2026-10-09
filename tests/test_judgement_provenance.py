"""判決 derived object 的 provenance chain（spec 004 T021 / FR-017）。

## 這份測試的核心主張

> 每個 chunk、每個引用候選，都能被追溯到：artifact → entry → field → offset。

五個欄位缺一不可；缺失不是 default，是 error。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import chunk as CH  # noqa: E402
import citations as CT  # noqa: E402
import document  # noqa: E402
import provenance as PV  # noqa: E402
import text  # noqa: E402

DOCS = ROOT / "tests" / "fixtures" / "judgements" / "docs"
CIVIL = DOCS / "civil_6field.json"
CONSTITUTIONAL = DOCS / "constitutional_5field_empty_jpdf.json"

ARTIFACT_SHA256 = "ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c"
ENTRY = "202607\\臺灣臺北地方法院民事\\TPDV,115,訴,2468,20260901,1.json"
CONST_ENTRY = "202607\\憲法法庭\\JCCC,115,審裁,1158,20260701.json"


def make_doc(p: Path = CIVIL, entry: str = ENTRY):
    return document.from_document(
        json.loads(p.read_text(encoding="utf-8")),
        entry_path=entry,
        sha256="a" * 64,
    )


def make_chunks(doc):
    return CH.chunk_document(doc, chunk_size=100)


# ── 基本成功路徑 ───────────────────────────────────────────────────────────


def test_chunk_provenance_has_all_five_fields():
    doc = make_doc()
    c = make_chunks(doc)[0]
    p = PV.chain(c, artifact_sha256=ARTIFACT_SHA256)
    assert p.as_dict() == {
        "artifact_sha256": ARTIFACT_SHA256,
        "entry_path": ENTRY,
        "json_field": "JFULL",
        "start_offset": c.start_offset,
        "end_offset": c.end_offset,
    }


def test_candidate_provenance_uses_candidate_offsets():
    s = "爰依民法第185條規定辦理。"
    candidates = CT.detect(
        text.LosslessText.from_string(s),
        entry_path=ENTRY,
        jid="J",
        provenance={"artifact_sha256": ARTIFACT_SHA256},
    )
    assert candidates, "fixture 應至少有一個引用候選"
    cand = candidates[0]
    p = PV.chain(cand)
    assert p.artifact_sha256 == ARTIFACT_SHA256
    assert p.entry_path == ENTRY
    assert p.json_field == "JFULL"
    assert p.start_offset == cand.start_offset
    assert p.end_offset == cand.end_offset


def test_candidate_provenance_accepts_explicit_artifact_sha():
    s = "爰依民法第185條規定辦理。"
    candidates = CT.detect(
        text.LosslessText.from_string(s),
        entry_path=ENTRY,
        jid="J",
    )
    # 即使 candidate.provenance 沒有 artifact_sha256，explicit 參數也能補上
    p = PV.chain(candidates[0], artifact_sha256=ARTIFACT_SHA256)
    assert p.artifact_sha256 == ARTIFACT_SHA256


# ── 缺失欄位 → error ──────────────────────────────────────────────────────


def test_missing_artifact_sha_raises():
    doc = make_doc()
    c = make_chunks(doc)[0]
    with pytest.raises(PV.ProvenanceError, match="artifact_sha256"):
        PV.chain(c)


def test_empty_artifact_sha_raises():
    doc = make_doc()
    c = make_chunks(doc)[0]
    with pytest.raises(PV.ProvenanceError, match="artifact_sha256"):
        PV.chain(c, artifact_sha256="")


def test_missing_entry_path_raises():
    doc = make_doc()
    # 製造一個 source_document 為空的 chunk（直接建，不走 chunk_document）
    c = CH.Chunk(
        text="x", start_offset=0, end_offset=1, chunk_index=0,
        boundary_kind=CH.BoundaryKind.SINGLE,
        source_document="", jid="",
    )
    with pytest.raises(PV.ProvenanceError, match="entry_path"):
        PV.chain(c, artifact_sha256=ARTIFACT_SHA256)


@pytest.mark.parametrize("bad_offset", [-1, "abc", None])
def test_invalid_start_offset_raises(bad_offset):
    doc = make_doc()
    c = make_chunks(doc)[0]
    # 用 object.__setattr__ 改 frozen dataclass 的欄位（測試專用）
    c2 = CH.Chunk(
        text=c.text, start_offset=bad_offset, end_offset=c.end_offset,
        chunk_index=c.chunk_index, boundary_kind=c.boundary_kind,
        source_document=c.source_document, jid=c.jid, sha256=c.sha256,
    )
    with pytest.raises(PV.ProvenanceError, match="start_offset"):
        PV.chain(c2, artifact_sha256=ARTIFACT_SHA256)


def test_end_before_start_raises():
    doc = make_doc()
    c = make_chunks(doc)[0]
    c2 = CH.Chunk(
        text=c.text, start_offset=5, end_offset=3,
        chunk_index=c.chunk_index, boundary_kind=c.boundary_kind,
        source_document=c.source_document, jid=c.jid, sha256=c.sha256,
    )
    with pytest.raises(PV.ProvenanceError, match="end_offset"):
        PV.chain(c2, artifact_sha256=ARTIFACT_SHA256)


# ── 5-field JID 也適用 ─────────────────────────────────────────────────────


def test_constitutional_document_provenance():
    doc = make_doc(CONSTITUTIONAL, CONST_ENTRY)
    c = make_chunks(doc)[0]
    p = PV.chain(c, artifact_sha256=ARTIFACT_SHA256)
    assert p.entry_path == CONST_ENTRY
    assert p.json_field == "JFULL"


# ── 與 substring invariant 的關係 ──────────────────────────────────────────


def test_provenance_offsets_point_back_to_jfull():
    """provenance 給的 offset 必須能從 JFULL 切出 chunk text。"""
    doc = make_doc()
    c = make_chunks(doc)[0]
    p = PV.chain(c, artifact_sha256=ARTIFACT_SHA256)
    assert doc.jfull[p.start_offset : p.end_offset] == c.text


def test_provenance_offsets_point_back_to_candidate_text():
    s = "爰依民法第185條規定辦理。"
    candidates = CT.detect(
        text.LosslessText.from_string(s),
        entry_path=ENTRY,
        jid="J",
    )
    cand = candidates[0]
    p = PV.chain(cand, artifact_sha256=ARTIFACT_SHA256)
    assert s[p.start_offset : p.end_offset] == cand.matched_text


# ── 函式介面 ───────────────────────────────────────────────────────────────


def test_to_dict_returns_plain_dict():
    doc = make_doc()
    c = make_chunks(doc)[0]
    d = PV.to_dict(c, artifact_sha256=ARTIFACT_SHA256)
    assert isinstance(d, dict)
    assert set(d) == {
        "artifact_sha256", "entry_path", "json_field",
        "start_offset", "end_offset",
    }


def test_unsupported_type_raises():
    with pytest.raises(PV.ProvenanceError, match="不支援的物件類型"):
        PV.chain("not a chunk", artifact_sha256=ARTIFACT_SHA256)


# ── 不重複推論 ─────────────────────────────────────────────────────────────


def test_chain_does_not_invent_court():
    """provenance chain 不得加入 court 或 case_type 等推論欄位。"""
    doc = make_doc()
    c = make_chunks(doc)[0]
    p = PV.to_dict(c, artifact_sha256=ARTIFACT_SHA256)
    assert "court" not in p
    assert "case_type" not in p
    assert "statute_name" not in p
