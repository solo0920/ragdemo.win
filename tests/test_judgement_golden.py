"""Golden dataset：固定來源紀錄，偵測 pipeline 的無意間改動（spec 004 T024）。

## 這份測試的核心主張

> 有一批從 artifact **逐字複製**出來的判決，它們的 size、CRC32、SHA-256
> 都被釘死；任何會改變 derived output 的程式改動都會讓這些測試紅掉。

## 為什麼 golden record 不能手寫

手寫的 golden 本身就是 unsourced document，違反本 feature 的根本原則。
這裡的檔案是從 `tests/fixtures/judgements/docs_fixture.rar` 用 `extract.py`
解出來的 bytes，原封不動寫入 `golden/`。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import document as DOC  # noqa: E402
import extract as EX  # noqa: E402
import inventory as INV  # noqa: E402
import schema as SCH  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "judgements" / "golden"
MANIFEST = GOLDEN / "manifest.json"
FIXTURE_RAR = ROOT / "tests" / "fixtures" / "judgements" / "docs_fixture.rar"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _read(p: Path) -> bytes:
    return p.read_bytes()


# ── golden 檔案本身與 manifest 一致 ──────────────────────────────────────────


def test_golden_manifest_exists():
    assert MANIFEST.is_file()


@pytest.mark.parametrize("name", [
    "docs_civil_6field.json",
    "docs_constitutional_5field_empty_jpdf.json",
    "docs_criminal_6field.json",
])
def test_golden_file_matches_manifest_size_and_digest(name: str, manifest: dict):
    assert name in manifest, f"manifest 缺少 {name}"
    info = manifest[name]
    p = GOLDEN / name
    data = _read(p)
    assert len(data) == info["size"], f"{name} size 不符"
    assert f"{zlib.crc32(data) & 0xFFFFFFFF:08X}" == info["crc32"], f"{name} CRC32 不符"
    assert hashlib.sha256(data).hexdigest() == info["sha256"], f"{name} SHA-256 不符"


# ── golden 檔案與 archive entry 一致 ────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "docs_civil_6field.json",
    "docs_constitutional_5field_empty_jpdf.json",
    "docs_criminal_6field.json",
])
def test_golden_file_matches_archive_entry(name: str, manifest: dict):
    if not FIXTURE_RAR.exists():
        pytest.skip("docs_fixture.rar 不存在")
    entry_path = manifest[name]["archive_entry"]
    entries = {e.path: e for e in INV.inventory(FIXTURE_RAR) if not e.is_dir}
    entry = entries[entry_path]
    data = _read(GOLDEN / name)
    assert len(data) == entry.unpacked_size
    assert (zlib.crc32(data) & 0xFFFFFFFF) == entry.crc32


# ── 重跑 extraction → byte-identical ────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "docs_civil_6field.json",
    "docs_constitutional_5field_empty_jpdf.json",
    "docs_criminal_6field.json",
])
def test_extraction_is_reproducible(name: str, manifest: dict):
    if not FIXTURE_RAR.exists():
        pytest.skip("docs_fixture.rar 不存在")
    entry_path = manifest[name]["archive_entry"]
    with tempfile.TemporaryDirectory() as da:
        with tempfile.TemporaryDirectory() as db:
            a = EX.extract_one(entry_path, archive=FIXTURE_RAR, dest=Path(da))
            b = EX.extract_one(entry_path, archive=FIXTURE_RAR, dest=Path(db))
            assert a.data == b.data
            assert a.sha256 == b.sha256


# ── golden 檔案覆蓋 civil / criminal / constitutional ───────────────────────


def test_golden_set_covers_required_document_classes(manifest: dict):
    names = set(manifest)
    assert "docs_civil_6field.json" in names
    assert "docs_criminal_6field.json" in names
    assert "docs_constitutional_5field_empty_jpdf.json" in names


# ── golden 檔案是有效 source JSON ────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "docs_civil_6field.json",
    "docs_constitutional_5field_empty_jpdf.json",
    "docs_criminal_6field.json",
])
def test_golden_file_is_valid_schema(name: str):
    data = json.loads(_read(GOLDEN / name).decode("utf-8"))
    result = SCH.validate(data)
    assert result.valid, f"{name} 不符合 schema：{result.reason}"


@pytest.mark.parametrize("name", [
    "docs_civil_6field.json",
    "docs_constitutional_5field_empty_jpdf.json",
    "docs_criminal_6field.json",
])
def test_golden_file_can_become_document(name: str, manifest: dict):
    data = json.loads(_read(GOLDEN / name).decode("utf-8"))
    doc = DOC.from_document(
        data,
        entry_path=manifest[name]["archive_entry"],
        sha256=manifest[name]["sha256"],
    )
    assert doc.jfull == data["JFULL"]
