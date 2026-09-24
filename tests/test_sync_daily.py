"""sync_daily：版本判定/縮水防呆/state 持久化/zip 解析（純邏輯，不碰網路）。"""
import io
import json
import zipfile

import pytest

import sync_daily as S


def _make_zip(laws, update_date="2026/9/18"):
    data = {"UpdateDate": update_date, "Laws": laws}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ChLaw.json", json.dumps(data, ensure_ascii=False))
    return buf.getvalue()


def _law(art="第1條"):
    return {"LawName": "測試法",
            "LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode=A0000001",
            "LawArticles": [{"ArticleType": "A", "ArticleNo": art, "ArticleContent": "內容。"}]}


def test_version_changed():
    assert S.version_changed({}, "abc") is True          # 首次視為新版
    assert S.version_changed({"sha256": "abc"}, "abc") is False
    assert S.version_changed({"sha256": "abc"}, "abd") is True


def test_shrink_guard():
    assert S.shrink_guard(0, 10) is False                # 首次不擋
    assert S.shrink_guard(100, 120) is False
    assert S.shrink_guard(100, 80) is False              # 剛好 80% → 不算縮水
    assert S.shrink_guard(100, 79) is True               # <80% → 擋
    assert S.shrink_guard(100, 10) is True
    assert S.shrink_guard(0, 0) is False


def test_parse_validate_ok():
    body = _make_zip([_law()])
    data, laws_n, arts_n = S.parse_validate(body)
    assert laws_n == 1 and arts_n == 1
    assert data["UpdateDate"] == "2026/9/18"
    assert data["Laws"][0]["LawName"] == "測試法"


def test_parse_validate_rejects_invalid():
    with pytest.raises(ValueError):
        S.parse_validate(b"not a zip")
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("Other.json", "{}")
    with pytest.raises(ValueError):
        S.parse_validate(bad.getvalue())     # 無 ChLaw.json
    empty = _make_zip([])
    with pytest.raises(ValueError):
        S.parse_validate(empty)              # Laws 空


def test_extract_chlaw_sha_consistency():
    body = _make_zip([_law()])
    raw = S.extract_chlaw(body)
    assert raw.startswith(b'{"UpdateDate"')
    # sha256 與 parse 版本一致（同內容）
    assert S.sha256_bytes(raw) == S.sha256_bytes(S.extract_chlaw(body))


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATE", tmp_path / ".law_sync.json")
    assert S.load_state() == {}
    st = {"sha256": "x" * 64, "update_date": "2026/9/18", "laws_count": 10, "articles_count": 30}
    S.save_state(st)
    assert S.load_state() == st


def test_state_corrupt_fails_loud(tmp_path, monkeypatch):
    # 壞 state fail-loud（main 的 __main__ 外層會接手中止，不更動資料）
    monkeypatch.setattr(S, "STATE", tmp_path / ".law_sync.json")
    (tmp_path / ".law_sync.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        S.load_state()


def test_sha256_bytes_deterministic():
    assert S.sha256_bytes(b"abc") == S.sha256_bytes(b"abc")
    assert len(S.sha256_bytes(b"abc")) == 64