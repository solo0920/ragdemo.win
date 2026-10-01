"""`check_hosts_table` 的行為契約（2026-09-29 修掉必定崩潰的 bug 後補的）。

歷史：這支函式裡有一行 `del rows`，下一行卻 `for k in rows:` —— 任何一次
完整審計（`env-audit.py` 不帶參數）都 100% 撞上 UnboundLocalError。
因為 `--template` 模式不會呼叫它，而測試只測 `--template` 與個別掃描器，
這個 bug 從未被任何測試抓到，於是「完整審計可用」這件事從未被驗證。

這組測試的作用是讓它以後壞掉時會紅。
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("env_audit", ROOT / "scripts" / "env-audit.py")
ea = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ea)


# 在 monkeypatch ROOT **之前**建好 registry：build_registry() 會掃描
# backend/、ingest/、compose.yaml、scripts/，若在 tmp_root 下跑會掃到空目錄，
# 於是每個鍵都變成「沒有任何程式讀取」—— 那是測試自己的錯，不是程式的。
REG = ea.build_registry()


@pytest.fixture
def table(tmp_path, monkeypatch):
    """造一個 settings/env/hosts.shared.env，並把 ROOT 指過去。"""
    root = tmp_path / "repo"
    (root / "settings" / "env").mkdir(parents=True)
    monkeypatch.setattr(ea, "ROOT", root)
    return root / "settings" / "env" / "hosts.shared.env"


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_real_table_audits_without_crashing(capsys):
    """對 repo 裡真的那張總表跑一次 —— 回歸測試的主體。"""
    n = ea.check_hosts_table(REG, {})
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "UnboundLocalError" not in out
    # 3 台（x570/mbp/wsl）× 10 個 base 鍵，應該是 0 項問題
    assert n == 0, f"真的總表不該有問題，但回報 {n} 項：\\n{out}"


def test_missing_table_reports_and_returns_1(table, capsys):
    assert ea.check_hosts_table({}, {}) == 1
    assert "找不到" in capsys.readouterr().out


def test_missing_hosts_declaration_is_caught(table, capsys):
    """沒有 `HOSTS=` 宣告 → 明確報錯，不是靜默少算。"""
    _write(table, "wsl_TS_IP=100.65.68.106\n")
    assert ea.check_hosts_table(REG, {}) == 1
    assert "缺少" in capsys.readouterr().out


def test_ghost_key_in_table_is_caught(table, capsys):
    """總表裡打錯字的鍵（TS_IP 寫成 TS_I）必須被反查到。

    這是這支函式存在的理由：render 會照樣把打錯的鍵寫進 .env，
    症狀是「設定看起來都對，但那台的容器綁錯 IP」。
    """
    _write(table, "HOSTS=wsl\nwsl_TS_I=100.65.68.106\n")
    n = ea.check_hosts_table(REG, {})
    out = capsys.readouterr().out
    assert n >= 1
    assert "TS_I" in out and "沒有任何程式讀取" in out


def test_incomplete_host_coverage_is_caught(table, capsys):
    """宣告 3 台但只有 1 台有列 → 要報「只有 1/3 台有列」。"""
    _write(table, "HOSTS=x570,mbp,wsl\nwsl_TS_IP=100.65.68.106\n")
    n = ea.check_hosts_table(REG, {})
    out = capsys.readouterr().out
    assert n >= 1
    assert "1/3" in out


def test_policy_excluded_key_in_table_is_caught(table, capsys, monkeypatch):
    """政策性停用的變數（LAN_IP）不該出現在總表。"""
    monkeypatch.setattr(ea, "POLICY_EXCLUDED", {"LAN_IP": "IP 準則：只用 tailscale"})
    _write(table, "HOSTS=wsl\nwsl_LAN_IP=192.168.1.5\n")
    n = ea.check_hosts_table(REG, {})
    out = capsys.readouterr().out
    assert n >= 1
    assert "LAN_IP" in out and "政策性停用" in out


def test_unknown_host_prefix_is_ignored(table, capsys):
    """不在 HOSTS 宣告裡的前綴不該被算進來（那是別人的列）。"""
    _write(table, "HOSTS=wsl\nwsl_TS_IP=100.65.68.106\nmbp_TS_IP=100.64.121.9\n")
    n = ea.check_hosts_table(REG, {})
    out = capsys.readouterr().out
    assert n == 0, f"只宣告 wsl 就不該抱怨 mbp 缺列：\\n{out}"
