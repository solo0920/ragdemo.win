"""主機端 ingest 的環境變數：**載入 `.env`，並把容器主機名換成主機端打得到的位址。**

## 為什麼需要這個模組（2026-10-02 實測）

`ingest/laws/*.py` 有兩種執行環境，而**它們需要的位址不一樣**：

| 執行者 | 網路 | `postgres` / `qdrant` 解析得到嗎 |
|---|---|---|
| `POST /ingest` → **容器內** | compose 網路 | ✓（那是 compose 的**服務名**） |
| crontab → **主機上** | 主機網路 | ✗ |

（`data/laws/*` 已 gitignore，所以動 mtime 無害 —— 見 sync-snapshot.sh 那側
  2026-10-02 的同一個修正）

而 cron 那行是 `cd … && .venv/bin/python ingest/laws/sync_daily.py --apply` ——
**沒有 `source .env`**，也不是 `docker compose exec`，所以：

1. `os.getenv("POSTGRES_DSN")` 回 `None` → `pg_load.py` 的 fallback 是
   `postgresql://rag@localhost:5432/ragdemo` → postgres 只綁 `${TS_IP}:5432`
   → **ConnectionRefusedError**
2. 就算 `source .env`，`POSTGRES_DSN` 裡的主機名是 **`postgres`**（容器服務名）
   → 在主機上 **gaierror**
3. `QDRANT` **根本不在 `.env` 裡** → `qdrant_load.py` 用 `localhost:6333`
   → qdrant 也只綁 `${TS_IP}:6333` → 同樣連不上
4. `QDRANT_API_KEY` 的 fallback 是 `""` → 不帶 key → **401**

**三台的 cron 都會踩到，而症狀是「沒有任何錯誤輸出，只是法規沒更新」。**

x570 先發現（它的上游 HTTP 200，下載成功後才走到這一步）；wsl 被上游 500 擋在
前面，所以還沒暴露 —— 但上游一恢復就會以「另一個不相干的故障」出現。

## 為什麼是「改寫主機名」而不是「另設一個變數」

* 新增 `POSTGRES_DSN_HOST` 之類 = **三台再多一個要同步的鍵**，而這個 repo 剛做完
  三機 `.env` 標準化（27 鍵、逐位元組比對）。能靠既有鍵推導出來的，就不新增。
* compose 自己就是這麼做的：`- "${TS_IP:-127.0.0.1}:5432:5432"` —— **主機端該用
  `TS_IP`**，這是 repo 裡既有的唯一真相來源。

## 為什麼不覆蓋既有的環境變數

容器內跑同一支 `pg_load.py` 時，環境已經由 compose 備好（`postgres:5432`、
`QDRANT_API_KEY`…）。**那些必須原樣保留**，否則會把容器路徑弄壞。所以這裡只
**補上缺的**，不覆蓋已有的 —— `setdefault` 語義。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]

# 容器網路裡的服務名 → 主機上要換成 TS_IP
_CONTAINER_HOSTS = ("postgres", "qdrant")

# 複用 env-audit 的解析器，不要寫第二份 .env parser
#（它有 `if __name__ == "__main__"` 保護，import 不會執行任何東西 —— 實測過）
def _load_env_file() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location(
        "_ragdemo_env_audit", ROOT / "scripts" / "env-audit.py")
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_env(ROOT / ".env")


_LOADED_FROM_ENVFILE: set[str] = set()


def load_host_env() -> set[str]:
    """把 `.env` 補進 `os.environ`（**不覆蓋**已有的），回傳**實際補進去的鍵**。

    為什麼要補而不是直接讀 dict：下游用的是 `os.getenv(...)`，而且
    `asyncpg.connect()` 之外的第三方呼叫也會讀環境。補進環境最一致。

    回傳值是給 `host_postgres_dsn()` 判斷「這個值是誰給的」用的 —— 見該函式。
    """
    for k, v in _load_env_file().items():
        if k not in os.environ:          # 不覆蓋：容器路徑已經由 compose 備好
            os.environ[k] = v
            _LOADED_FROM_ENVFILE.add(k)
    return set(_LOADED_FROM_ENVFILE)


def host_reachable_host() -> str:
    """主機端該用的位址：`TS_IP`，沒設定就退回 `127.0.0.1`。

    與 compose 的 `${TS_IP:-127.0.0.1}` **保持同一個預設** —— 不同步的話，
    `TS_IP` 沒設定的那台會連到不同的地方，而症狀是「有時連得上、有時連不上」。
    """
    return (os.environ.get("TS_IP") or "").strip() or "127.0.0.1"


def _retarget(url: str, default_port: str) -> str:
    """把 URL 的主機名從容器服務名換成主機端位址；已經是 IP/名稱就原樣留著。"""
    parts = urlsplit(url)
    if not parts.hostname or parts.hostname not in _CONTAINER_HOSTS:
        return url
    host = host_reachable_host()
    netloc = f"{host}:{parts.port or default_port}"
    if parts.username:
        # URL 裡有帳密 → 只換 host 部分，保留 userinfo
        _, _, rest = url.partition("://")
        userinfo, _, tail = rest.partition("@")
        return urlunsplit((parts.scheme, f"{userinfo}@{netloc}",
                           parts.path, parts.query, parts.fragment))
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def host_postgres_dsn() -> str:
    """主機端 ingest 用的 postgres DSN。

    ⚠️ **不再提供 `localhost` 的 fallback** —— 那個 fallback 是 2026-10-02 實測
    證實壞掉的：postgres 綁 `${TS_IP}:5432`，`localhost` 一定被拒。
    寧可在這裡明確報錯，也不要靜默用一個連不上的位址然後看起來像「跑了但沒資料」。

    ⚠️ **只改寫「我們自己從 `.env` 載進來的值」。** 如果 `POSTGRES_DSN` 本來就在
    環境裡（容器路徑：compose 用 `environment` 給的 `postgres:5432`），就**原樣
    保留** —— 那個 `postgres` 在容器網路裡是對的，改成 `TS_IP` 反而會讓它依賴
    「容器 → 主機 tailscale 位址」這條路由（實測現在連得上，但那是**運氣**，不是
    設計；容器網路一變就壞，而且沒有任何錯誤）。

    實測：容器裡 `TS_IP` 與 `POSTGRES_DSN` 都存在（len=71），所以「無條件改寫」
    會**改變容器行為**。這個判斷就是為了讓模組在兩種環境下都正確。
    """
    loaded = load_host_env()
    dsn = (os.environ.get("POSTGRES_DSN") or "").strip()
    if not dsn:
        pw = (os.environ.get("POSTGRES_PASSWORD") or "").strip()
        if not pw:
            raise RuntimeError(
                "沒有 POSTGRES_DSN 也沒有 POSTGRES_PASSWORD —— 主機端 ingest "
                "無法連資料庫。兩者都在 `.env`，請確認 cron 有載入它"
                "（用 `_hostenv.load_host_env()`，不要在 crontab 裡寫密碼）。")
        dsn = f"postgresql://rag:{pw}@postgres:5432/ragdemo"
        return _retarget(dsn, "5432")          # 我們自己組的 → 一定要改寫
    if "POSTGRES_DSN" not in loaded:
        return dsn                              # 環境本來就有的 → 原樣（容器路徑）
    return _retarget(dsn, "5432")


def host_qdrant_url() -> str:
    """主機端 ingest 用的 qdrant URL。

    ⚠️ `QDRANT` **不在 `.env` 裡**（它只在 compose 的 environment 裡），所以這裡
    從 `TS_IP` 直接組 —— 與 compose 的 `${TS_IP:-127.0.0.1}:6333:6333` 同一個來源。
    舊的 `localhost:6333` fallback 同樣是壞的（qdrant 不綁 localhost）。
    """
    load_host_env()
    q = (os.environ.get("QDRANT") or "").strip()
    if not q:
        q = f"http://{host_reachable_host()}:6333"
    return _retarget(q, "6333")


def host_ollama_url() -> str:
    """主機端 embed 用的 ollama 位址。

    `.env` 的 `OLLAMA` 通常已經是 DNS 名（例如 mbp 的 tailscale 名稱），那種在主機
    上本来就解析得到，所以**不強制改寫** —— 只在它剛好是容器名的情況下才換。
    """
    load_host_env()
    return (os.environ.get("OLLAMA") or "").strip() or "http://127.0.0.1:11434"