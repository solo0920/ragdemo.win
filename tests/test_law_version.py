"""法規版本（/status 的 law_version/versions）純邏輯測試。

不碰網路、不碰真實 data/laws/ —— 一律用 tmp_path + monkeypatch 換掉讀檔位置。
重點守三件事：
  1. sidecar（.law_version）優先於 .law_sync.json：備援機的 sidecar 才代表它實際服務的版本
  2. 壞檔 / 缺檔 / 空值都要降級成 {}，絕不让 /status 整個 500
  3. TTL 快取要真的生效（否則 /status 每次輪詢都碰磁碟）
"""
import json

import pytest

import app.rag as rag


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """每個測試都從空快取開始，並讓 TTL 失效（0 表示一定重讀）。"""
    monkeypatch.setattr(rag, "_LAW_VERSION_CACHE", (0.0, {}))
    yield
    rag._LAW_VERSION_CACHE = (0.0, {})


@pytest.fixture
def laws_dir(tmp_path, monkeypatch):
    """把 _read_law_version 的候選基底路徑指到 tmp_path（改常數，不改函式）。"""
    d = tmp_path / "laws"
    d.mkdir()
    monkeypatch.setattr(rag, "_LAW_VERSION_DIRS", (d,))
    return d


def test_missing_all_returns_empty(laws_dir):
    assert rag.law_version() == {}


def test_reads_law_sync_json(laws_dir):
    (laws_dir / ".law_sync.json").write_text(json.dumps(
        {"update_date": "2026-09-11", "last_checked": "2026-09-26T18:00:00"}), encoding="utf-8")
    v = rag.law_version()
    assert v["update_date"] == "2026-09-11"
    assert v["at"] == "2026-09-26T18:00:00"
    assert v["source"] == "law_sync.json"


def test_sidecar_wins_over_law_sync(laws_dir):
    """備援機兩者並存時，sidecar 才代表它手上實際服務的資料版本。"""
    (laws_dir / ".law_sync.json").write_text(json.dumps({"update_date": "2020-01-01"}), encoding="utf-8")
    (laws_dir / ".law_version").write_text(json.dumps(
        {"update_date": "2026-09-25", "source": "x570", "synced_at": "2026-09-26 19:00"}), encoding="utf-8")
    v = rag.law_version()
    assert v["update_date"] == "2026-09-25"
    assert v["source"] == "x570"
    assert v["at"] == "2026-09-26 19:00"


def test_broken_json_degrades_to_empty(laws_dir):
    """壞檔不能讓 /status 500 —— 降級成 {}，前端顯示 '-'。"""
    (laws_dir / ".law_version").write_text("not-json{{{", encoding="utf-8")
    assert rag.law_version() == {}


def test_blank_update_date_skipped(laws_dir):
    """update_date 空白等於沒有版本，應繼續往下一個候選檔找。"""
    (laws_dir / ".law_version").write_text(json.dumps({"update_date": "   "}), encoding="utf-8")
    (laws_dir / ".law_sync.json").write_text(json.dumps({"update_date": "2026-09-11"}), encoding="utf-8")
    assert rag.law_version()["update_date"] == "2026-09-11"


def test_ttl_cache_avoids_reread(monkeypatch):
    """TTL 內第二次呼叫不得再碰磁碟。"""
    calls = []
    monkeypatch.setattr(rag, "_read_law_version", lambda: calls.append(1) or {"update_date": "2026-09-11"})
    rag.law_version()
    rag.law_version()
    rag.law_version()
    assert len(calls) == 1


def test_ttl_expires(monkeypatch):
    calls = []
    monkeypatch.setattr(rag, "_read_law_version", lambda: calls.append(1) or {"update_date": "v"})
    monkeypatch.setattr(rag, "_LAW_VERSION_TTL", 0.0)
    rag.law_version()
    rag.law_version()
    assert len(calls) == 2
