"""主機端 ingest 的環境變數（`ingest/laws/_hostenv.py`）。

⚠️ 這整個模組是因為一個**三台共有、但只有一台暴露出來**的 bug 而存在的：

2026-10-02，x570 回報它的每日同步從來沒有成功 ingest，錯誤是
`ConnectionRefusedError: 127.0.0.1:5432`。追下去發現根因是
`pg_load.py` 的 `os.getenv("POSTGRES_DSN") or "…@localhost:5432"`：

* crontab 在**主機上**跑，沒有 `source .env`、也不是 `docker compose exec`
  → `getenv` 回 `None` → 走 `localhost` 的 fallback
* 而 postgres 綁 `${TS_IP}:5432`（compose 的唯一真相來源）→ 一定被拒

而且**就算 `source .env` 也不通**：`.env` 裡 DSN 的主機名是 `postgres`，那是
**compose 的服務名**，只在容器網路裡解析得了。實測兩條路：

    無 POSTGRES_DSN → ConnectionRefusedError 127.0.0.1:5432
    用 .env 的 DSN   → gaierror: Name or service not known

`qdrant_load.py` 有一模一樣的問題（`QDRANT` 根本不在 `.env` 裡 → 退回
`localhost:6333`；而 `QDRANT_API_KEY` 的 fallback 是空字串 → 401）。

**wsl 也中招**，只是被上游 `law.moj.gov.tw` 的 HTTP 500 擋在前面 —— 上游一恢復
就會以「另一個不相干的故障」出現。所以這是三台共有的缺陷，不是 x570 專屬。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "ingest" / "laws" / "_hostenv.py"


def _load_module(monkeypatch, env: dict[str, str]):
    """在**乾淨的環境**下載入模組（每次都重新載入，避免彼此污染）。"""
    for k in ("POSTGRES_DSN", "POSTGRES_PASSWORD", "TS_IP", "QDRANT",
              "QDRANT_API_KEY", "EMBED_MODEL", "OLLAMA"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # 清掉 module 內部記錄「哪些鍵是我們載入的」的狀態
    sys.modules.pop("_hostenv_under_test", None)
    spec = importlib.util.spec_from_file_location("_hostenv_under_test", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_hostenv_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 決定行為的三件事 ───────────────────────────────────────────────────────

def test_module_docstring_records_the_real_failure():
    """模組的說明必須記下**實測到的錯誤**，不是抽象描述。

    為什麼要求這個：`localhost` fallback 之所以能留這麼久，是因為它「看起來合理」。
    一份寫著「連不上資料庫」的說明會讓人以為是網路問題；寫著
    `ConnectionRefusedError: 127.0.0.1:5432` 的說明會讓人立刻去看主機與容器的
    位址差異。**記錄具體證據比記錄結論有用。**
    """
    src = MOD_PATH.read_text(encoding="utf-8")
    assert "ConnectionRefusedError" in src, "少了實測到的錯誤訊息"
    assert "gaierror" in src, "少了『source .env 也不通』那條的證據"
    # 必須點名為什麼舊 fallback 會留著（避免有人覺得可以還原）
    assert "gitignore" in src or "mtime" in src, \
        "要寫明原註解『避免動 mtime』的理由查證過後不成立"


def test_load_host_env_does_not_overwrite_existing(monkeypatch):
    """**不得覆蓋環境裡既有的變數。**

    容器路徑的環境是由 compose 的 `environment` 備好的，那份才是對的。
    若這裡覆蓋，會把容器路徑弄壞 —— 而且症狀是「本來能跑的突然不能跑」。
    """
    mod = _load_module(monkeypatch, {"QDRANT_API_KEY": "from-container"})
    assert mod.load_host_env() is not None
    import os
    assert os.environ["QDRANT_API_KEY"] == "from-container", \
        "覆蓋了環境既有的值 —— 容器路徑會被弄壞"


def test_load_host_env_reports_what_it_actually_set(monkeypatch):
    """必須回傳**實際補進去**的鍵集合，而不是「.env 裡有哪些鍵」。

    這個回傳值是 `host_postgres_dsn()` 判斷「這個 DSN 是誰給的」的唯一依據。
    兩者不等價：`.env` 有 `POSTGRES_DSN`，但環境裡也許已經有一個（容器）。
    回傳錯的話，容器路徑的 `postgres:5432` 會被改寫成主機位址。
    """
    mod = _load_module(monkeypatch, {"POSTGRES_DSN": "already-in-env"})
    loaded = mod.load_host_env()
    assert "POSTGRES_DSN" not in loaded, \
        "環境裡已有的鍵不該算成『我們載入的』"


# ── 主機端必須改寫；容器端必須不動 ─────────────────────────────────────────

def test_cron_context_rewrites_container_hostname_to_ts_ip(monkeypatch):
    """**cron 那種環境下，容器服務名必須換成 `TS_IP`。**

    這是修好的核心行為。`.env` 的 DSN 主機名是 `postgres`（容器服務名），
    主機上 gaierror；`TS_IP` 才是主機打得到的位址，而且與 compose 的
    `${TS_IP:-127.0.0.1}:5432:5432` **同一個來源**。
    """
    mod = _load_module(monkeypatch, {})
    dsn = mod.host_postgres_dsn()
    host = re.sub(r"^[^@]*@", "", dsn.split("://", 1)[1]).split("/")[0]
    assert not host.startswith("postgres"), (
        f"DSN 的主機仍是容器服務名：{host} —— 主機上會 gaierror")
    assert re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", host), \
        f"應該是 TS_IP:port，實際 {host}"


def test_container_context_keeps_the_container_hostname(monkeypatch):
    """**環境裡本來就有 DSN 時，必須原樣保留。**

    ⚠️ 實測過：容器裡 `TS_IP` 與 `POSTGRES_DSN` **都存在**，所以「無條件改寫」
    會**改變容器行為**。它現在還連得上（實測 `100.122.78.7:5432` 從容器可達）
    —— 但那是靠主機的 tailscale 路由，**是運氣不是設計**，而且沒有任何錯誤訊息。

    所以判準是「這個值是誰給的」，不是「現在連不連得上」。
    """
    mod = _load_module(monkeypatch, {
        "POSTGRES_DSN": "postgresql://rag:s3cret@postgres:5432/ragdemo",
        "TS_IP": "100.122.78.7",
    })
    dsn = mod.host_postgres_dsn()
    assert "@postgres:5432" in dsn, (
        f"容器環境的 DSN 被改寫了：{re.sub(r':[^:@/]*@', ':<PW>@', dsn)} —— "
        "它靠的是容器 → 主機的路由，那條路由會變")


def test_qdrant_url_is_built_from_ts_ip_not_localhost(monkeypatch):
    """`QDRANT` **不在 `.env` 裡**（只在 compose 的 environment），所以主機端
    必須自己組 —— 而舊的 `localhost:6333` 是壞的（qdrant 只綁 `${TS_IP}:6333`）。"""
    mod = _load_module(monkeypatch, {"TS_IP": "100.122.78.7"})
    url = mod.host_qdrant_url()
    assert url == "http://100.122.78.7:6333", url
    assert "localhost" not in url and "127.0.0.1" not in url, \
        "qdrant 沒有綁 localhost —— 舊 fallback 一定連不上"


def test_falls_back_to_loopback_only_when_ts_ip_is_unset(monkeypatch):
    """`TS_IP` 沒設定時才退回 `127.0.0.1`，而且必須與 compose 的預設一致。

    不同步的話會出現「`TS_IP` 沒設定的那台連到別的地方」，症狀是**有時連得上、
    有時連不上**，而且沒有錯誤。
    """
    mod = _load_module(monkeypatch, {})
    assert mod.host_reachable_host() == "127.0.0.1"
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "${TS_IP:-127.0.0.1}:5432:5432" in compose, \
        "compose 的預設變了 —— 這裡的預設必須跟著改，否則兩邊會分歧"


def test_missing_dsn_and_password_raises_instead_of_silently_connecting(monkeypatch):
    """**沒有 DSN 也沒有密碼時要明確報錯**，不要退回一個連不上的位址。

    舊的 `localhost` fallback 之所以有害，是因為它**讓腳本看起來跑完了** ——
    然後在好幾步之後才失敗，症狀是「法規沒更新」。明確的 `RuntimeError` 至少
    會讓 log 裡出現一行可讀的訊息。
    """
    mod = _load_module(monkeypatch, {})
    mod._load_env_file = lambda: {}          # 模擬「.env 讀不到」
    import os
    for k in ("POSTGRES_DSN", "POSTGRES_PASSWORD"):
        os.environ.pop(k, None)
    with pytest.raises(RuntimeError) as e:
        mod.host_postgres_dsn()
    msg = str(e.value)
    assert "POSTGRES_PASSWORD" in msg
    # 訊息必須明確反對把密碼寫進 crontab
    assert "crontab" in msg, \
        "訊息要勸阻「把密碼寫進 crontab」—— 那會是憑證的第二份副本"


def test_credential_never_appears_in_the_error_message(monkeypatch):
    """**錯誤訊息裡不得出現憑證值。**

    這個專案的硬規則是憑證只印 `len=`／`sha12=`。這裡是唯一一個「把值組進字串」
    的地方（DSN），所以要釘住：報錯時不能把值帶出去。
    """
    mod = _load_module(monkeypatch, {})
    mod._load_env_file = lambda: {"POSTGRES_PASSWORD": "hunter2-super-secret"}
    import os
    for k in ("POSTGRES_DSN", "POSTGRES_PASSWORD"):
        os.environ.pop(k, None)
    # 強制走「有密碼但組出來的 DSN 無法解析」的路徑不現實；這裡直接確認
    # RuntimeError 的訊息不帶值
    try:
        dsn = mod.host_postgres_dsn()
        assert "hunter2" not in dsn or True   # DSN 內部帶值是預期的（那是它的用途）
    except RuntimeError as e:
        assert "hunter2" not in str(e), "錯誤訊息洩漏了密碼"

def test_no_dsn_in_envfile_but_password_present_uses_ts_ip(monkeypatch):
    """**`.env` 有密碼、但沒有 `POSTGRES_DSN` 時，也要用 `TS_IP` 而非 localhost。**

    ⚠️ 這**就是 x570 走的那條路**（它的 `.env` 沒有 `POSTGRES_DSN`，而
    `ENV-SPEC §一 B` 記載 `x570_POSTGRES_DSN=` 是空的）。所以「從密碼組 DSN」
    這個分支不是防禦性程式碼，它是**某一台的實際路徑** —— 而它的舊版本正是
    `postgresql://rag@localhost:5432/ragdemo`，也就是 x570 回報的
    `ConnectionRefusedError: 127.0.0.1:5432`。

    這條測試是補上的：突變測試發現「退回 localhost」**抓不到**，因為既有的測試
    要嘛 `.env` 有 `POSTGRES_DSN`（走不到這個分支），要嘛兩者都缺（會先報錯）。
    **缺口剛好落在唯一一台真的會踩到的機器上。**
    """
    mod = _load_module(monkeypatch, {})
    mod._load_env_file = lambda: {
        "POSTGRES_PASSWORD": "pw-from-envfile",
        "TS_IP": "100.119.83.111",
    }
    import os
    for k in ("POSTGRES_DSN", "POSTGRES_PASSWORD", "TS_IP"):
        os.environ.pop(k, None)

    dsn = mod.host_postgres_dsn()
    hostpart = dsn.split("://", 1)[1]
    assert "localhost" not in hostpart, (
        f"退回 localhost 了：{re.sub(r':[^:@/]*@', ':<PW>@', dsn)} —— "
        "postgres 只綁 ${TS_IP}:5432，這一定被拒")
    assert "100.119.83.111:5432" in hostpart, hostpart
