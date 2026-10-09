"""`decoder.py` 的契約測試（spec 004 T003）。

**這些測試不得執行任何 decoder binary。** T003 的交付物是「記錄一份已證明的
configuration」，不是「讓 decoder 跑起來」。若測試需要二進位，CI 的乾淨 clone
（沒有 unrar）就會整組失敗 —— 而那正是預期狀態。

`probe()` 只在這裡被**假設有東西可探**的方式測試（用一個假 script 代替真的
binary），從不碰系統上的 unrar。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import decoder  # noqa: E402


# ── 釘死值 ────────────────────────────────────────────────────────────────────

def test_pinned_decoder_identity_matches_the_proof():
    """decoder 身分必須是 decoder-proof.md 證明的那一組，不是挑一個順眼的。"""
    assert decoder.DECODER_NAME == "unrar"
    assert decoder.DECODER_VERSION == "7.13"
    assert decoder.SOURCE_SHA256 == (
        "72a9ccca146174f41876e8b21ab27e973f039c6d10b13aabcb320e7055b9bb98"
    )
    assert decoder.SOURCE_URL == "https://www.rarlab.com/rar/unrarsrc-7.1.10.tar.gz"
    assert decoder.SOURCE_SIZE_BYTES == 268008


def test_version_is_7_13_not_the_tarball_name():
    """tarball 叫 7.1.10，version.hpp 與 `unrar` 自報 7.13。

    這個落差是實測出來的（plan.md §0.2），很容易被後人「修正」成檔名裡的
    7.1.10 而不知不覺間換了一個 claim。
    """
    info = decoder.describe()
    assert info.version == "7.13"
    assert "7.1.10" in info.source_url  # source 路徑保留原檔名
    assert info.version != "7.1.10"


def test_container_and_compression_are_the_measured_ones():
    assert decoder.SUPPORTED_CONTAINER == "rar4"
    assert decoder.SUPPORTED_COMPRESSION == "rar1.5(v29) -m3 -md=1m"
    assert decoder.VERIFIED_ENTRIES == 108547


# ── describe()：宣稱用什麼解的，不執行 ────────────────────────────────────────

def test_describe_returns_frozen_identity():
    info = decoder.describe()
    assert info.name == "unrar"
    assert info.version == "7.13"
    with pytest.raises(Exception):
        info.version = "9.9"  # type: ignore[misc]


def test_describe_does_not_execute_anything(monkeypatch):
    """`describe()` 絕不可執行外部指令。

    若它偷偷 probe 一支不存在的 binary，整個 module import 就會炸 ——
    而 inventory (T004) 與多數測試都只需要「宣稱的 provenance」。
    """
    def boom(*a, **k):  # pragma: no cover - 若被呼叫就會失敗
        raise AssertionError("describe() 不該執行任何東西")

    monkeypatch.setattr(decoder.subprocess, "run", boom)
    assert decoder.describe().version == "7.13"


# ── provenance ────────────────────────────────────────────────────────────────

def test_provenance_records_source_but_not_a_binary():
    """provenance 必須記 source sha256，且明確記錄 binary 未打包。

    這兩點是同一個決定的兩面：用 source 路線（可散布）而不是 binary 路線
    （試用授權禁止打包）。少了任何一半，日後重現不出這批資料是怎麼來的。
    """
    prov = decoder.provenance()
    assert prov["decoder"]["source_sha256"] == decoder.SOURCE_SHA256
    assert prov["binary_bundled"] is False
    assert prov["verification_doc"].endswith("decoder-proof.md")
    assert prov["verified_entries"] == 108547


def test_provenance_carries_no_secret_or_host_path():
    """provenance 會隨 corpus 存檔，所以不得含任何機器路徑。"""
    prov = decoder.provenance()
    blob = repr(prov)
    assert "/home/" not in blob and "/Users/" not in blob
    assert "keys.txt" not in blob


# ── probe()：只讀回 binary 的自我報告 ─────────────────────────────────────────

def test_probe_returns_first_line_of_output(tmp_path):
    fake = tmp_path / "unrar"
    fake.write_text("#!/bin/sh\necho 'UNRAR 7.13 freeware'\necho 'second line'\n")
    fake.chmod(0o755)
    assert decoder.probe(fake).startswith("UNRAR 7.13 freeware")


def test_probe_raises_when_binary_absent(tmp_path):
    with pytest.raises(FileNotFoundError):
        decoder.probe(tmp_path / "not-installed")


def test_probe_does_not_search_path(tmp_path, monkeypatch):
    """`probe()` 必須用呼叫端給的路徑，不得自行搜尋 PATH。

    自行搜尋會讓同一段 code 在三台機器上行為不同 —— 而這個 repo 的三台機器
    目前**都沒有** unrar（plan.md §0.2）。
    """
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        decoder.probe(tmp_path / "definitely-not-here")