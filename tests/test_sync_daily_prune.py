"""`prune_versions()` 的行為測試。

⚠️ 為什麼這些要測：`versions/` 是**過去法規狀態的唯一紀錄** —— 上游
law.moj.gov.tw 只提供當前版本，舊的下不回來。所以這個函式刪錯一個目錄，
就是永久刪掉一份歷史，而不是清掉一個可重新產生的檔案。

真正會出事的是三種情況，各測一種：
  · 保護不到當前版本 → 刪掉唯一一份完整的當前工件
  · 排序用名稱而非 mtime → 兩種命名格式（sha12 / timestamp）互比，砍錯
  · 刪不掉就讓整次同步回報失敗 → 資料已套用完成卻被標成失敗
"""
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "ingest" / "laws" / "sync_daily.py"


def _load(tmp_path, monkeypatch, keep_env=None):
    """載入模組並把 VERS 指到 tmp_path。回傳模組。"""
    if keep_env is not None:
        monkeypatch.setenv("RAGDEMO_KEEP_VERSIONS", keep_env)
    spec = importlib.util.spec_from_file_location("sync_daily_t", SYNC)
    m = importlib.util.module_from_spec(spec)
    sys.modules["sync_daily_t"] = m
    spec.loader.exec_module(m)
    vers = tmp_path / "versions"
    vers.mkdir()
    m.VERS = vers
    return m


def _mk(vers: Path, name: str, age_days: float) -> Path:
    d = vers / name
    d.mkdir()
    (d / "ChLaw.json").write_text("x" * 100)
    # 明確設定 mtime —— 「年齡」是這個函式唯一的排序依據
    t = time.time() - age_days * 86400
    os.utime(d, (t, t))
    return d


def test_keeps_the_newest_n_by_mtime(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch)
    for i in range(8):
        _mk(m.VERS, f"2026010{i}-000000", age_days=8 - i)   # 0 號最新
    removed = m.prune_versions(3, protect=set())
    left = sorted(d.name for d in m.VERS.iterdir())
    assert left == ["20260105-000000", "20260106-000000", "20260107-000000"]
    assert len(removed) == 5


def test_protected_names_survive_even_if_oldest(tmp_path, monkeypatch):
    """protect 的目錄即使最舊也不能刪 —— 那是回退路徑。"""
    m = _load(tmp_path, monkeypatch)
    old = _mk(m.VERS, "oldest-sha", age_days=99)          # 非常舊
    for i in range(3):
        _mk(m.VERS, f"2026020{i}-000000", age_days=3 - i)
    m.prune_versions(1, protect={"oldest-sha"})
    names = {d.name for d in m.VERS.iterdir()}
    assert "oldest-sha" in names, "protect 的目錄被刪了 —— 那就是刪掉回退路徑"
    assert "20260200-000000" not in names


def test_sorts_by_mtime_not_name(tmp_path, monkeypatch):
    """兩種命名格式（sha12 與 timestamp）不可用名稱互比，必須靠 mtime。

    這是實際會踩到的：'bc7402385de4' 字典序大於 '20261003-081932'，
    但它可能是**較舊**的版本。用名稱排序會保留新的、刪掉舊的 —— 正好相反。
    """
    m = _load(tmp_path, monkeypatch)
    sha = _mk(m.VERS, "ffffffffffff", age_days=10)         # 舊，名字卻最大
    ts = _mk(m.VERS, "20260101-000000", age_days=1)        # 新，名字卻最小
    m.prune_versions(1, protect=set())
    names = {d.name for d in m.VERS.iterdir()}
    assert "20260101-000000" in names, "保留了舊的、刪掉新的 —— 排序用錯欄位"
    assert "ffffffffffff" not in names


def test_only_directories_are_considered(tmp_path, monkeypatch):
    """VERS 底下的檔案不該被當成版本目錄處理。"""
    m = _load(tmp_path, monkeypatch)
    _mk(m.VERS, "20260101-000000", age_days=1)
    (m.VERS / "README.txt").write_text("not a version")
    m.prune_versions(1, protect=set())
    assert (m.VERS / "README.txt").exists()


def test_keep_larger_than_available_keeps_everything(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch)
    for i in range(3):
        _mk(m.VERS, f"v{i}", age_days=i + 1)
    removed = m.prune_versions(10, protect=set())
    assert removed == []
    assert len(list(m.VERS.iterdir())) == 3


def test_undeletable_dir_is_warned_not_fatal(tmp_path, monkeypatch, caplog):
    """刪不掉就記警告，**絕不能**讓它變成例外往外丟。

    呼叫點（成功套用之後）把整段包在 try 裡，但這裡測的是更底層的性質：
    prune_versions 自己遇到 OSError 就要吞掉 —— 因為呼叫點的 try 是防禦性
    的第二層，不該是唯一一層。
    """
    m = _load(tmp_path, monkeypatch)
    _mk(m.VERS, "newest", age_days=1)
    victim = _mk(m.VERS, "oldest", age_days=99)

    def boom(*a, **k):
        raise OSError("Permission denied")
    monkeypatch.setattr(m.shutil, "rmtree", boom)
    removed = m.prune_versions(1, protect=set())   # 不應拋出
    assert removed == []                            # 沒刪掉就別回報刪了


def test_keep_count_is_configurable_via_env(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch, keep_env="2")
    assert m.KEEP_VERSIONS == 2
    for i in range(6):
        _mk(m.VERS, f"v{i}", age_days=6 - i)
    m.prune_versions(m.KEEP_VERSIONS, protect=set())
    assert len(list(m.VERS.iterdir())) == 2


def test_missing_versions_dir_is_not_an_error(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch)
    m.VERS = tmp_path / "does-not-exist"
    assert m.prune_versions(3, protect=set()) == []
