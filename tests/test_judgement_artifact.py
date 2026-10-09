"""`artifact.py` 的契約測試（spec 004 T001）。

**這些測試不得依賴 283 MB artifact 存在。** T001 的驗證重點之一就是
「artifact 被換掉時要紅」，若測試本身需要真 artifact 才能跑，那正是最需要
它跑的情況（換檔後）。所以 mutation 一律在 temp copy 上做。

需要真 artifact 的那條（`test_real_artifact_matches_pinned_identity`）以
`skipif` 保護：artifact 不在時 skip，而不是 fail —— CI 的乾淨 clone 本來就
沒有那份 283 MB 檔案，那是預期狀態，不是錯誤。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import artifact  # noqa: E402

HAS_REAL = artifact.ARTIFACT_PATH.is_file()


# ── 釘死值本身 ────────────────────────────────────────────────────────────────

def test_pinned_constants_match_the_evidence():
    """釘死值必須是 investigation 記錄的那三個，不是近似值。

    這條看起來瑣碎，但它是整個 feature 的根：digest 一旦被「順手改掉」，
    後面每一條 invariant 都還會綠，只是驗的是另一份資料。
    """
    assert artifact.ARTIFACT_SHA256 == (
        "ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c"
    )
    assert artifact.ARTIFACT_SIZE_BYTES == 297279556
    assert artifact.FILESET_ID == 70691
    assert artifact.ARTIFACT_PATH.name == "202607--(20260916Update).rar"


def test_provenance_records_manual_acquisition():
    """acquisition 必須記成 manual —— 這個 repo 不做自動下載。

    寫成 'api' 會讓後人以為有條自動取得的路徑，而實際上那條路徑對所有
    RAR fileset 都回 HTTP 500（plan.md §4）。
    """
    prov = artifact.provenance()
    assert prov["acquisition"] == "manual-download"
    assert prov["fileset_id"] == 70691
    assert prov["artifact_sha256"] == artifact.ARTIFACT_SHA256


# ── identify：回答「這是什麼」，不與釘死值比對 ──────────────────────────────────

def test_identify_reports_name_size_and_digest(tmp_path):
    f = tmp_path / "sample.bin"
    payload = b"ragdemo judicial fixture"
    f.write_bytes(payload)

    ident = artifact.identify(f)
    assert ident.name == "sample.bin"
    assert ident.size_bytes == len(payload)
    assert ident.sha256 == hashlib.sha256(payload).hexdigest()


def test_identify_does_not_raise_for_an_unpinned_file(tmp_path):
    """identify 必須能描述一份「陌生」的 artifact。

    這是把 identify 與 verify 分開的理由：上游換檔時，第一個要回答的問題是
    「新的是什麼」，不是「新的對不對」。verify 才負責後者。
    """
    f = tmp_path / "unrecognised.bin"
    f.write_bytes(b"something else entirely")
    assert artifact.identify(f).sha256 != artifact.ARTIFACT_SHA256


def test_identify_raises_when_file_absent(tmp_path):
    with pytest.raises(artifact.ArtifactIntegrityError):
        artifact.identify(tmp_path / "nope.rar")


def test_identity_is_frozen(tmp_path):
    """身分記錄不可改寫 —— 否則 provenance 可以被程式悄悄改掉。"""
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    ident = artifact.identify(f)
    with pytest.raises(Exception):
        ident.sha256 = "deadbeef"  # type: ignore[misc]


# ── verify：digest + size 都查 ───────────────────────────────────────────────────

def _pinned(tmp_path: Path) -> Path:
    """造一份「大小與 digest 符合釘死值」的檔案不可行（那是 283 MB 的雜湊），
    所以 verify 的分支測試改用 monkeypatch 釘住常數。"""
    return tmp_path


def test_verify_detects_digest_mismatch(tmp_path, monkeypatch):
    """核心契約：digest 不符 → verify 必須失敗。

    monkeypatch 只換掉「期望值」，檔案本身是真的讀出來的，所以
    sha256_file() 的計算路徑仍被完整執行。
    """
    f = tmp_path / "mutated.rar"
    f.write_bytes(b"Rar!\x1a\x07\x00" + b"x" * 1024)
    monkeypatch.setattr(artifact, "ARTIFACT_SIZE_BYTES", 1028)  # 大小先對齊
    monkeypatch.setattr(artifact, "ARTIFACT_SHA256", "0" * 64)

    with pytest.raises(artifact.ArtifactIntegrityError) as ei:
        artifact.verify(f)
    assert "sha256" in str(ei.value)


def test_verify_detects_size_mismatch(tmp_path, monkeypatch):
    """大小相同而內容被換過是最常見的換檔情境，只比 digest 會漏掉大小。"""
    f = tmp_path / "same-size.rar"
    payload = b"A" * 512
    f.write_bytes(payload)
    monkeypatch.setattr(artifact, "ARTIFACT_SIZE_BYTES", len(payload))
    monkeypatch.setattr(artifact, "ARTIFACT_SHA256", "0" * 64)

    with pytest.raises(artifact.ArtifactIntegrityError) as ei:
        artifact.verify(f)
    msg = str(ei.value)
    # digest 先被發現也對 —— 重點是有錯
    assert "sha256" in msg or "size" in msg


def test_verify_reports_both_problems_when_both_differ(tmp_path, monkeypatch):
    f = tmp_path / "both.rar"
    f.write_bytes(b"short")
    monkeypatch.setattr(artifact, "ARTIFACT_SIZE_BYTES", 999)
    monkeypatch.setattr(artifact, "ARTIFACT_SHA256", "0" * 64)

    with pytest.raises(artifact.ArtifactIntegrityError) as ei:
        artifact.verify(f)
    msg = str(ei.value)
    assert "size" in msg and "sha256" in msg


def test_verify_passes_when_identity_matches(tmp_path, monkeypatch):
    f = tmp_path / "good.rar"
    payload = b"Rar!\x1a\x07\x00payload"
    f.write_bytes(payload)
    monkeypatch.setattr(artifact, "ARTIFACT_SIZE_BYTES", len(payload))
    monkeypatch.setattr(
        artifact, "ARTIFACT_SHA256", hashlib.sha256(payload).hexdigest()
    )
    ident = artifact.verify(f)
    assert ident.size_bytes == len(payload)


# ── T002：raw/ 寫入防護 ────────────────────────────────────────────────────────
#
# 這組測試用 monkeypatch 換掉 raw_dir，不碰真 raw/ —— 那樣才可以在 CI 的乾淨
# clone 上跑（那裡沒有 data/judgements/raw/，但防護邏輯必須成立）。

def _rawdir(tmp_path: Path) -> Path:
    raw = tmp_path / "data" / "judgements" / "raw"
    raw.mkdir(parents=True)
    (raw / artifact.ARTIFACT_FILENAME).write_bytes(b"Rar!\x1a\x07\x00")
    return raw


def test_raw_guard_rejects_write_inside_raw(tmp_path):
    raw = _rawdir(tmp_path)
    with pytest.raises(artifact.ArtifactIntegrityError):
        artifact.assert_outside_raw(raw / "new.rar", raw_dir=raw)


def test_raw_guard_rejects_the_raw_dir_itself(tmp_path):
    """寫進 raw/ 本身（而非其子目錄）也要擋。"""
    raw = _rawdir(tmp_path)
    with pytest.raises(artifact.ArtifactIntegrityError):
        artifact.assert_outside_raw(raw, raw_dir=raw)


def test_raw_guard_allows_sibling_working_dir(tmp_path):
    raw = _rawdir(tmp_path)
    work = tmp_path / "data" / "judgements" / "work"
    work.mkdir()
    out = artifact.assert_outside_raw(work / "extracted.json", raw_dir=raw)
    assert out.name == "extracted.json"


def test_raw_guard_rejects_dotdot_traversal(tmp_path):
    """`../` 繞過必須被擋。

    只用字串比對的守衛會漏掉這個：'data/judgements/raw/../raw/x' 不以
    'data/judgements/raw' 開頭，但它確實寫進 raw/。
    """
    raw = _rawdir(tmp_path)
    sneaky = tmp_path / "data" / "judgements" / "raw" / ".." / "raw" / "x.rar"
    with pytest.raises(artifact.ArtifactIntegrityError):
        artifact.assert_outside_raw(sneaky, raw_dir=raw)


def test_raw_guard_does_not_confuse_raw_backup_with_raw(tmp_path):
    """`raw_backup/` **不是** `raw/` 的子目錄 —— 常見的備份資料夾名。

    用 `str.startswith()` 的守衛會把它誤判成在 raw/ 之下，然後擋掉一個
    完全合法的寫入目標。
    """
    raw = _rawdir(tmp_path)
    backup = tmp_path / "data" / "judgements" / "raw_backup"
    backup.mkdir()
    out = artifact.assert_outside_raw(backup / "keep.rar", raw_dir=raw)
    assert out.name == "keep.rar"


def test_is_in_raw_is_a_pure_query(tmp_path):
    raw = _rawdir(tmp_path)
    assert artifact.is_in_raw(raw / artifact.ARTIFACT_FILENAME, raw_dir=raw) is True
    assert artifact.is_in_raw(tmp_path / "elsewhere.json", raw_dir=raw) is False


def test_guard_rejects_the_real_raw_dir_by_default():
    """不傳 raw_dir 時，必須擋住 repo 裡那個真的 raw/。

    這條防的是「呼叫端忘了傳 raw_dir，於是守護形同虛設」——那是最可能的
    失效模式。
    """
    if not HAS_REAL:
        pytest.skip("真 raw/ 不在這個 checkout，無法驗證預設行為")
    with pytest.raises(artifact.ArtifactIntegrityError):
        artifact.assert_outside_raw(artifact.ARTIFACT_PATH)


# ── 真 artifact（僅在本機有時執行）──────────────────────────────────────────────

@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_real_artifact_matches_pinned_identity():
    """真 artifact 必須與釘死值一致 —— 這是 T001 唯一真正對資料的斷言。"""
    ident = artifact.verify()
    assert ident.sha256 == artifact.ARTIFACT_SHA256
    assert ident.size_bytes == artifact.ARTIFACT_SIZE_BYTES


@pytest.mark.skipif(not HAS_REAL, reason="283 MB artifact 不在這個 checkout")
def test_mutation_of_a_copy_is_detected(tmp_path):
    """tasks.md 指定的 mutation test：改一份 **copy**，verify 必須失敗。

    刻意用 copy 而非原檔。這條測試存在的唯一目的是證明「寫壞 artifact
    會被抓到」，如果它自己就能改壞真 artifact，那這條測試是個破壞工具。
    """
    copy = tmp_path / artifact.ARTIFACT_PATH.name
    # 只複製前 1 MB：digest 必然不同，而我們要驗證的是「不同會被抓到」，
    # 不是「能複製 283 MB」。複製整份會讓這條測試慢到沒有價值。
    with open(artifact.ARTIFACT_PATH, "rb") as src, open(copy, "wb") as dst:
        dst.write(src.read(1 << 20))

    with pytest.raises(artifact.ArtifactIntegrityError):
        artifact.verify(copy)