"""M1 profile build：allowlist 自檢的**離線層**（spec 006 T102／T103 / FR-010a）。

## 這份測試證明什麼

不是證明「現在是綠的」，而是證明「**守得住**」。所以除了 1 條正向斷言，
還有 5 條**注入錯誤**的測試（SC-008）：每一條都必須紅燈，否則守門形同虛設。

## 這份測試不需要 284M RAR（沿用 spec 004 的 fixture 策略）

離線層只讀 `allowlist-m1.json`（版控內，29 KB）。因此**在沒有 artifact 的機器上
也必須全綠**——這是 INV-NOSKIP 的具體要求：環境缺什麼可以誠實 skip，
但這一層沒有任何東西可缺。若哪天這條測試在乾淨 clone 上出現 skip，
就是違規，不是環境問題。

## 為什麼測試用「副本」而不是就地改檔

`allowlist-m1.json` 是**決策證據**。就地改它再改回來，等於讓證據在測試期間
被動過——而測試失敗時留下的就是個被改過的證據。全部注入都在 `tmp_path`
的副本上做。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import profile_build as PB  # noqa: E402

ALLOWLIST = PB.ALLOWLIST_PATH


def _copy_to(tmp_path: Path) -> Path:
    """把 allowlist 複製到 tmp，並回傳路徑（就地改檔案絕不碰版控內的證據）。"""
    dst = tmp_path / "allowlist.json"
    dst.write_text(ALLOWLIST.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _mutate(tmp_path: Path, fn) -> list[str]:
    """套用一個破壞動作，回傳 `verify_allowlist` 的問題清單。"""
    p = _copy_to(tmp_path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    fn(raw)
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return PB.verify_allowlist(PB.load_allowlist(p))


# ── 正向：版控內的 allowlist 必須通過全部檢查 ──────────────────────────────

def test_real_allowlist_has_no_problems():
    problems = PB.verify_allowlist(PB.load_allowlist())
    assert problems == [], f"allowlist 離線自檢失敗：{problems}"


def test_allowlist_loaded_shape():
    al = PB.load_allowlist()
    assert al.schema == PB.ALLOWLIST_SCHEMA
    assert len(al) == al.count == 100
    assert len(al.excluded_frozen) > 0
    # digest 是依清單**順序**算的；若有人誤用 selection.sample_digest（會先排序）
    # 這裡就會對不上。
    assert PB.entries_digest([e.path for e in al.entries]) == al.entries_sha256


def test_digest_differs_from_sorted_digest():
    """守住那個定義差異：本檔的 digest 不排序，`sample_digest` 排序。

    用**明確反轉**的輸入比較，不依賴「當前清單的順序恰好不是排序序」這個
    事實 —— 否則哪天重新選樣剛好同序，這條會假紅。
    """
    al = PB.load_allowlist()
    import selection  # noqa: PLC0415

    paths = [e.path for e in al.entries]
    flipped = list(reversed(paths))

    # 同樣的輸入集合，只因為順序不同，本檔的 digest 就必須不同。
    assert PB.entries_digest(flipped) != PB.entries_digest(paths)
    # 而 selection.sample_digest 會先排序，所以對兩者都給同一個值。
    assert selection.sample_digest(flipped) == selection.sample_digest(paths)


# ── 反向：SC-008 的 5 個注入錯誤，每一條都必須被抓到 ───────────────────────

def test_inject_modified_path_is_caught(tmp_path):
    def mutate(raw):
        raw["entries"][0]["path"] = raw["entries"][0]["path"].replace("2026", "2025")

    problems = _mutate(tmp_path, mutate)
    assert any("entries_sha256 不符" in p for p in problems), problems


def test_inject_dropped_entry_is_caught(tmp_path):
    def mutate(raw):
        raw["entries"].pop()

    problems = _mutate(tmp_path, mutate)
    assert any("count != len(entries)" in p for p in problems), problems


def test_inject_forged_digest_is_caught(tmp_path):
    """把 digest 欄位改掉——這正是「自己證自己」的漏洞，必須被抓。"""

    def mutate(raw):
        raw["entries_sha256"] = "0" * 64

    problems = _mutate(tmp_path, mutate)
    assert any("entries_sha256 不符" in p for p in problems), problems


def test_inject_duplicate_entry_is_caught(tmp_path):
    def mutate(raw):
        raw["entries"].append(dict(raw["entries"][0]))

    problems = _mutate(tmp_path, mutate)
    # 重複會同時觸發 count 不符與 digest 不符；只要「重複」被點名即可。
    assert any("重複" in p for p in problems), problems


def test_inject_overlap_with_frozen_is_caught(tmp_path):
    def mutate(raw):
        raw["excluded_frozen_document_ids"].append(
            raw["entries"][0]["path_posix"].rsplit("/", 1)[-1][:-len(".json")]
        )

    problems = _mutate(tmp_path, mutate)
    assert any("交集" in p for p in problems), problems


# ── 還原後必須轉綠（否則上面的紅燈可能只是「永久紅」）──────────────────────

def test_restore_turns_green_again(tmp_path):
    p = _copy_to(tmp_path)
    original = p.read_text(encoding="utf-8")
    raw = json.loads(original)
    raw["entries_sha256"] = "0" * 64
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    assert PB.verify_allowlist(PB.load_allowlist(p)) != []

    p.write_text(original, encoding="utf-8")
    assert PB.verify_allowlist(PB.load_allowlist(p)) == []


# ── 結構性錯誤必須 raise（不是回傳問題清單）───────────────────────────────

def test_missing_file_raises(tmp_path):
    with pytest.raises(PB.AllowlistError):
        PB.load_allowlist(tmp_path / "nope.json")


def test_not_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(PB.AllowlistError):
        PB.load_allowlist(bad)


def test_missing_key_raises(tmp_path):
    def mutate(raw):
        raw.pop("entries_sha256")

    p = _copy_to(tmp_path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    mutate(raw)
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PB.AllowlistError):
        PB.load_allowlist(p)


# ── 路徑可攜性（T115 的模組側前提）：模組本身不得含絕對路徑 ───────────────

def test_portable_no_absolute_home_path():
    src = (ROOT / "ingest" / "judgements" / "profile_build.py").read_text(
        encoding="utf-8"
    )
    assert "/home/" not in src, "模組不得寫死 /home/<user>/ 路徑（FR-011／INV-PATH）"


def test_portable_paths_are_repo_relative():
    assert PB.ROOT == ROOT
    assert PB.ALLOWLIST_PATH.is_file()
    # 佔位形式而非真實絕對路徑
    assert "<ARTIFACTS_ROOT>" in PB.EXTERNAL_SNAPSHOT_RELPATTERN
    assert "/home/" not in PB.EXTERNAL_SNAPSHOT_RELPATTERN
