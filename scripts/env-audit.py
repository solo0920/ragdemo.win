#!/usr/bin/env python3
"""env-audit — 從程式碼反查 .env 的變數清單，比對落差。

## 為什麼改寫（2026-09-26）

舊版有一張手工維護的 `KNOWN` 表，docstring 寫「靠 scripts/env-audit.sh
從程式碼反查更新」—— **那個檔案從來不存在**。實際上是手工的，而且已經
爛掉，兩處不實：

1. **漏了 17 個 compose 實際會讀的變數**（COHERE_MODELS、GEMINI_MODELS、
   GROQ_MODELS、MISTRAL_MODELS、NVIDIA_MODELS、OPENROUTER_MODELS、
   ZEN_FREE_MODELS、HF_BASE_URL、HF_MODELS、OLLAMA_BASE_URL、
   KEEP_ALIVE、*_GATEWAY_URL 等）。它們全部吃著 compose 內建預設值，
   稽核工具完全看不到 —— 想調就必須先改 compose，不是改 .env。
2. **把 3 個「程式有讀」的變數誤標為幽靈**。`EMBED_MODEL`、`RERANK_MODEL`
   在 backend/app/rag.py 有讀（ingest 端也有），`QDRANT_URLS` 在
   rag.py:36 有讀；舊版卻標成「程式讀不到，設了沒作用」。

另一處不實在 `print_template()`：「程式端用 ${VAR_名} 讀取時會自動去掉
前綴」—— **沒有實作**，全 repo 找不到任何前綴處理程式碼。實際做法是把
per-host 的值放進 `settings/env/hosts.shared.env` 那張總表，由
`env-sync.sh render` 依本機 HOST_ID 挑列；同一台機器「同時」要有多組設定
（多個 peer 位址）時才把 id 燒進**值**裡 —— `HOST_API_URLS=x570=…,wsl=…`。
2026-09-27 之前是燒進變數**名**（`HOST_API_X570`／`HOST_API_MBP`／
`HOST_API_MSI`），那等於把機台清單寫進程式，第 4 台要改程式才能加；已改成單一
`HOST_API_URLS`。

## 設計原則

**不手維護任何清單。** 啟動時掃三個來源，反向建立「誰讀了什麼」：

- `compose.yaml`        —— `${VAR}` / `${VAR:-預設}` / `${VAR:?必填}`，
                            以及 `environment:` 下的字面值（= compose 寫死）
- `backend/**/*.py`     —— `os.getenv` / `os.environ.get` / `os.environ[...]`
- `ingest/**/*.py`      —— 同上（host 端 ingest 管線，container 跑不了）
- `scripts/*.sh`        —— `${VAR}` / `$VAR`，扣除同檔內自己賦值的區域變數

**長篇說明不放這裡，放回它描述的那段程式碼旁邊。** 舊版的 `note` 欄位
是人工寫的散文，注定與程式碼脫節；`compose.yaml` 的 `environment:` 區塊
已經有詳細註解（例：HOST_NAME 為何不用 /etc/hostname bind mount）。
本工具只產出機械性事實：誰讀、必不必須、compose 有沒有覆蓋。

## 分類

| 類別 | 意義 | 嚴重度 |
|---|---|---|
| 機台身份必須覆蓋 | 預設值是另一台機器的身份（`HOST_ID:-x570`、`TS_IP:-100.119.83.111`），本機 `HOST_ID` 不同卻沒設 | 高（`ports:` 那個會啟動失敗） |
| 幽靈 | `.env` 有，但沒有任何程式讀 | 高（誤以為有效） |
| 缺失必填 | compose `:?` 或 Python 無預設值，`.env` 沒有 | 高 |
| 低優先來源 | 純 fallback 連鎖的下層；設了上層，這個用不到 | 中（設兩個是冗餘） |
| compose 寫死 | compose 以字面值覆蓋 → `.env` 對容器無效 | 中（要改 compose） |
| 沒傳入容器 | compose 沒列 `environment:` → `.env` 的值到不了容器 | 中（分成容器端／host 端兩報） |
| 副本漂移 | 兩份 `.env` 同名變數值不同 | 高（`backend/.env` 已於 2026-09-26 刪除） |

### 「機台身份」是怎麼判定的（舊版靠人工，新版機械化）

舊 `KNOWN` 表手動標 `TS_IP`／`HOST_ID` 為 `required: True`。改寫後改由
`HOST_ID` 在 compose 的預設值當「這份 compose 為哪台烤的」基準：
`HOST_ID:-x570` → 預設是 x570 的。若 `.env` 的 `HOST_ID` 是 `mbp` 或 `wsl`，
凡預設值內含 tailscale IP 或機台 id 的變數、以及出現在 `ports:` 裡的變數，
都從「選填」升級為「必填」。不用人工維護清單，且 mbp/wsl 少設 `TS_IP`
會被抓到（docker 會試圖 bind x570 的 IP 而啟動失敗）。

## 用法

    scripts/env-audit.py              # 稽核目前 checkout（有問題回 exit 1）
    scripts/env-audit.py --template   # 產生 .env.example 骨架（只印骨架）
    scripts/env-audit.py --list       # 列出所有反查到的變數（除錯）
    scripts/env-audit.py --quiet      # 只輸出落差摘要（CI 用）

重導向範例：`scripts/env-audit.py --template > .env.example`。
稽核與產生器互斥 —— 骨架要能重導向，就不能和稽核輸出混在一起。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 掃描時跳過的路徑片段。
# build/ 要跳：backend/build/lib/app/rag.py 是 build 產物副本，
# 不跳會把同一個 os.getenv 算兩次（2026-09-26 實測）。
SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git",
             "build", "dist", ".svelte-kit", ".pytest_cache", ".ruff_cache"}

# 已知由外部工具鏈設定、非本專案 .env 管的環境變數。
# 這些在原始碼裡以字串出現（多數是判斷「有沒有設定」的程式碼），
# 不是我們要暴露給使用者調的參數。從原始碼反查會一併抓到，故排除。
TOOL_ENV = {
    # uv / venv / pytest / watchfiles / websockets / uvicorn 的自帶變數
    "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "UV_CACHE_DIR", "UV_LINK_MODE",
    "PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST", "PYTEST_DEBUG",
    "PYTEST_DEBUG_TEMPROOT", "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "PYTEST_PLUGINS", "PYTEST_THEME", "PYTEST_VERSION", "PY_COLORS",
    "WATCHFILES_CHANGES", "WATCHFILES_DEBUG", "WATCHFILES_FORCE_POLLING",
    "WATCHFILES_IGNORE_PERMISSION_DENIED", "WATCHFILES_POLL_DELAY_MS",
    "WEBSOCKETS_BACKOFF_FACTOR", "WEBSOCKETS_BACKOFF_INITIAL_DELAY",
    "WEBSOCKETS_BACKOFF_MAX_DELAY", "WEBSOCKETS_BACKOFF_MIN_DELAY",
    "WEBSOCKETS_MAX_BODY_SIZE", "WEBSOCKETS_MAX_LINE_LENGTH",
    "WEBSOCKETS_MAX_LOG_SIZE", "WEBSOCKETS_MAX_NUM_HEADERS",
    "WEBSOCKETS_MAX_REDIRECTS", "WEB_CONCURRENCY", "ASYNCPG_DEBUG_SERVER",
    "PYDANTIC_DISABLE_PLUGINS", "PYDANTIC_PRIVATE_ALLOW_UNHANDLED_SCHEMA_TYPES",
    "PYTHON_DOTENV_DISABLED", "PY_IGNORE_IMPORTMISMATCH",
    # libpq / PostgreSQL 官方環境變數
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSERVICE",
    "PGSERVICEFILE", "PGPASSFILE", "PGSSLMODE", "PGSSLROOTCERT", "PGSSLCERT",
    "PGSSLKEY", "PGSSLCRL", "PGSERVICE", "PGSSLMINPROTOCOLVERSION",
    "PGSSLMAXPROTOCOLVERSION", "PGSSLNEGOTIATION", "PGREQUIRESSL",
    "PGSSENCRYPTO", "PGGSSENCMODE", "PGKRBSRVNAME", "PGGSSLIB", "PGREALM",
    # 作業系統 / shell
    "HOME", "PATH", "USER", "USERNAME", "SHELL", "TERM", "PAGER", "LESS",
    "EDITOR", "VISUAL", "LANG", "LC_ALL", "TZ", "PWD", "OLDPWD", "TMPDIR",
    "HOSTNAME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    "COLORTERM", "NO_COLOR", "FORCE_COLOR", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "SSLKEYLOGFILE", "SYSTEMROOT", "HOMEDRIVE", "HOMEPATH", "PATHEXT",
    # bash 內建、由父 shell 繼承的選項集與特殊參數。`${SHELLOPTS:-}` 是偵測
    # xtrace 的標準手法（host-sync.sh／host-doctor.sh 都用），但它**不是**
    # .env 該管的東西：.env 設了也不會改變呼叫者的 shell 選項。
    # 踩過：2026-09-27 兩個新腳本的 xtrace 防線讓 --template 多印一行
    # `SHELLOPTS=`，害 .env.example 與 --template 不一致、CI 紅燈。
    "SHELLOPTS", "BASHOPTS", "BASHPID", "PS1", "PS2", "PS3", "PS4", "IFS",
    # 本專案測試 harness 的覆寫點（tests/test_env_sync.py 用 fixture 目錄
    # 隔離執行 scripts/env-sync.sh；不是給 .env 設的，不進 .env.example）
    "ENV_SYNC_DIR", "ENV_SYNC_ENV",
    # 同上：tests/test_rotate_secret.py 用它把 rotate-secret.sh 的 ROOT 指向
    # tmp 裡的假 repo。踩過的理由很具體 —— 沒列的話，env-audit 反查原始碼會把
    # `${ROTATE_SECRET_ROOT_OVERRIDE:-}` 抓成一個「該暴露給使用者調的參數」，
    # 於是 .env.example 多一行、CI 的 template 一致性檢查紅燈。而它一旦出現在
    # .env.example，等於在文件裡邀請人設一個只給測試用的變數 —— 那個變數設錯
    # 會讓腳本操作另一個目錄的加密檔。
    "ROTATE_SECRET_ROOT_OVERRIDE",
    # sops 官方的私鑰路徑變數。同樣是「工具的」不是「專案的」——
    # 它由呼叫者的環境決定，而 .env 是在腳本**內部**被 `set -a; . .env` 讀進來的
    # （rotate-secret.sh 不讀 .env、backup-env.sh 也不讀），所以在 .env 裡設它
    # **不會生效**，只會在 .env.example 多一行誤導人的東西。
    #
    # 為什麼之前沒被報、2026-10-02 加了 backup-env.sh 才被報出來（成因很細）：
    # env-audit 的 shell 反查會把「同檔內自己賦值的」視為 local 而跳過，而
    # env-sync.sh:395 與 rotate-secret.sh:177 寫的是
    #     SOPS_AGE_KEY_FILE="$KEYFILE" sops --decrypt …
    # —— 以 `SOPS_AGE_KEY_FILE=` **開頭**（賦值給自己），於是自動跳過。
    # 而 backup-env.sh:32 寫的是
    #     KEYS_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
    # —— 賦值的是 KEYS_FILE，SOPS_AGE_KEY_FILE 是純讀取 → 被判定成
    # 「該暴露給使用者調的參數」→ --template 多印一行 → 與 .env.example
    # 不一致 → pre-push 與 CI 紅燈。
    # 換句話說：**同一件事的兩種寫法，只因為位置不同而有不同的稽核結果** ——
    # 那正是白名單存在的理由。
    "SOPS_AGE_KEY_FILE",
}

SECRET_HINT = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", re.I)
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$")

# 政策性排除：程式**有**讀，但專案定案不用它，因此追蹤檔不得出現該賦值。
#
# 這與「幽靈變數（無人讀）」完全不同 —— LAN_IP 在 registry.py:21 確實被讀，
# 只是 2026-09-22 定案一律用 tailscale IP、停用 LAN_IP（ARCHITECTURE.md
# 「IP 準則」、ROADMAP.md:75「LAN_IP 已停用，勿再寫」）。
# .githooks/pre-push:31 與 ci.yml 都會擋 `^LAN_IP=`，因為 2026-09-22 之前
# 有 LAN IP 流進追蹤檔的事故。
#
# 所以 .env.example 不能印 `LAN_IP=` 那一行（會擋住 push），
# 但要以註解形式保留說明，讓人知道有這號變數以及為何不填。
POLICY_EXCLUDED: dict[str, str] = {
    "LAN_IP": "IP 準則（2026-09-22 定案）：一律 tailscale IP（100.64.0.0/10），"
              "停用 LAN_IP。192.168.x 一律不寫入 .env、不進 registry。"
              "追蹤檔出現 LAN_IP= 會被 pre-push hook 與 CI 擋下。見 ARCHITECTURE.md。",
}

# compose 的 ${...} 參照。
# 刻意「不」要求右花括號：只匹配到 ${NAME 加修飾符的開頭。
# 為什麼：巢狀展開 ${A:-x${B:?msg}} 的巢狀寫法若要求配對 }，
# 外層的 } 會把內層 ${B} 整個吃掉（2026-09-26 實測，compose.yaml:36 的
# ${POSTGRES_DSN:-postgresql://rag:${POSTGRES_PASSWORD:?...}} 就是這樣
# 讓 POSTGRES_PASSWORD 漏掉，被誤報成幽靈變數）。
COMPOSE_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z_0-9]*)(:?[-?])?")

# 預設值只用於顯示，獨立一條且不吃巢狀：抓不到就略過不顯示。
# 缺預設值僅影響顯示，不影響必填判定（必填看的是修飾符）。
COMPOSE_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z_0-9]*):-([^{]*)\}")

# Python 端讀環境變數的四種寫法。group(2) 為 None 代表「無預設值」→ 必填。
PY_REFS = [
    re.compile(r"""os\.getenv\(\s*["']([A-Za-z_][A-Za-z_0-9]*)["'](\s*,|\s*\))"""),
    re.compile(r"""os\.environ\.get\(\s*["']([A-Za-z_][A-Za-z_0-9]*)["'](\s*,|\s*\))"""),
    re.compile(r"""os\.environ\[\s*["']([A-Za-z_][A-Za-z_0-9]*)["']\s*\]"""),
    re.compile(r"""(?<![\w.])getenv\(\s*["']([A-Za-z_][A-Za-z_0-9]*)["'](\s*,|\s*\))"""),
]

# shell：變數賦值（用來排除同檔的局部變數）與讀取。
# 賦值可能以 `;` 串接在同一行（law-update-worker.sh:67 的
# `ROLE="source"; CMD="uv run ..."`），所以要先切成片段再逐段比對 ——
# 只比對行首會漏掉第二個，導致 CMD 被誤判成 .env 該管的變數。
SH_ASSIGN = re.compile(
    r"^\s*(?:export\s+|local\s+|declare\s+(?:-\w+\s+)*|readonly\s+|typeset\s+)*"
    r"([A-Za-z_][A-Za-z_0-9]*)=")
SH_READ = re.compile(r"\$\{([A-Za-z_][A-Za-z_0-9]*)[:}]|\$([A-Z][A-Z_0-9]{1,})\b")

# Python：常數承載哪個環境變數。
#   OLLAMA_DEFAULT = os.getenv("OLLAMA_BASE_URL", "http://...")
#   → ident_env["OLLAMA_DEFAULT"] = "OLLAMA_BASE_URL"
PY_IDENT_ENV = re.compile(
    r"^([A-Za-z_][A-Za-z_0-9]*)\s*=\s*os\.getenv\(\s*[\"']([A-Za-z_][A-Za-z_0-9]*)[\"']")

# Python：用另一個「承載環境變數的常數」當預設值 → fallback 連鎖。
#   os.getenv("OLLAMA_URLS", OLLAMA_DEFAULT)  且 OLLAMA_DEFAULT 承載 OLLAMA_BASE_URL
#   → 設了 OLLAMA_URLS，OLLAMA_BASE_URL 就完全用不到（rag.py:33-34）
#   os.getenv("QDRANT_URLS", QDRANT_DEFAULT)  且 QDRANT_DEFAULT 承載 QDRANT_URL
#   → 同上（rag.py:35-36）
# 為什麼必須偵測：OLLAMA_URLS 那行是 list comprehension 寫法
# （`OLLAMA_URLS = [u...for u in os.getenv(...)]`），不能用 `Y = os.getenv(...)`
# 的賦值形式配對，所以直接找呼叫本身，並確認第二個參數是已知常數。
PY_ENV_FALLBACK = re.compile(
    r"os\.getenv\(\s*[\"']([A-Za-z_][A-Za-z_0-9]*)[\"']\s*,\s*([A-Za-z_][A-Za-z_0-9]*)\s*[,)]")

COMPOSE_ENV_LINE = re.compile(r"^\s{6}([A-Z_][A-Z_0-9]*):\s*(.*)$")


class Ref:
    """單一變數的跨來源彙整。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.required = False          # compose :? 或 python 無預設
        self.compose_ref = False       # compose 有以 ${} 參照
        self.compose_hardcoded = False  # compose 用字面值覆蓋
        self.compose_default = ""      # ${VAR:-預設} 的預設值
        self.python_read = False
        self.python_default = False    # True=有預設, False=沒預設(=必填)
        self.shell_read = False
        self.readers: list[str] = []   # "backend/app/rag.py:36"
        self.in_ports = False          # 出現在 compose 的 ports: 裡（網路綁定）
        self.superseded_by = ""        # 若 .env 設了此變數，本變數即失效

    @property
    def secret(self) -> bool:
        return bool(SECRET_HINT.search(self.name))

    @property
    def readers_label(self) -> str:
        """'backend' / 'compose' / 'scripts' —— 給 .env.example 的消費者欄。"""
        parts = []
        if self.compose_ref or self.compose_hardcoded:
            parts.append("compose")
        if self.python_read:
            parts.append("backend")
        if self.shell_read:
            parts.append("scripts")
        return "/".join(parts) or "?"

    def source_note(self) -> str:
        bits = []
        if self.compose_hardcoded:
            bits.append("compose 以字面值寫死，.env 設了對容器無效（要改 compose）")
        elif not self.compose_ref and (self.python_read or self.shell_read):
            bits.append("compose 沒列入 environment，.env 的值到不了容器；"
                        "程式只會吃到原始碼裡的預設值")
        if self.required:
            bits.append("必填")
        if self.compose_default and not self.secret:
            bits.append(f"預設 {self.compose_default}")
        return "；".join(bits)

    def host_only_reader(self) -> bool:
        """讀取者是否**只在** host 端跑（ingest 管線 / shell 腳本）。

        backend/app/*.py 會在容器裡跑，所以「compose 沒傳入」不代表
        host 端才有效 —— 對它們來說是「容器內永遠拿不到 .env 的值」。
        這個區別影響建議該寫在哪，必須分開講。"""
        return all(r.startswith("ingest/") or r.startswith("scripts/")
                   for r in self.readers)

    def has_identity_default(self) -> bool:
        """預設值是否內建了「某一台機器的身份」（tailscale IP 或機台 id）。

        這類預設值在別的機器上是錯的。機械可偵測的兩個來源：
          - ${TS_IP:-100.119.83.111}      → tailscale IP
          - ${HOST_ID:-x570}              → 字面機台 id
        舊版 KNOWN 表用人工標 required=True 來擋，改寫後必須補回，否則
        mbp/wsl 少設 TS_IP 就會去 bind x570 的 IP，docker 啟動即失敗。

        但「身份寫在**鍵**裡」的變數要排除（見 NAME_SCOPED_HOST）。"""
        if NAME_SCOPED_HOST.search(self.name):
            return False
        if not self.compose_default:
            return False
        return bool(IDENTITY_RE.search(self.compose_default))


# 預設值中的機台身份：tailscale 的 100.x.x.x 網段，或 tailnet 上的機台 id。
#
# ⚠️ 這裡**同時**含 `msi` 與 `wsl`，而且是刻意的：2026-10-01 之後 `wsl` 是跑
# 後端的那台（WSL，Linux），`msi` 是同一台實體機器的 Windows 主機 —— 它不是
# 後端主機，但**仍是 tailnet 上真實存在、且提供 ollama 的機器**。所以判「這個
# 預設值是不是燒進了某台的身分」時兩個都要算。別因為 `msi` 不在 HOSTS 清單裡
# 就把它從 regex 拿掉。
IDENTITY_RE = re.compile(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b(x570|mbp|wsl|msi)\b")

# 機台代號寫在**變數名**裡的（FOO_MSI、HOST_API_X570…這類）。
# 這類變數的「身份在鍵不在值」：它描述的是**指定那一台**的位址，不是本機身份，
# 所以不該被判成「本機必須覆蓋」。
#
# 實測依據（2026-09-27，scope B 補上 HOST_API_* 時踩到）：預設值
# `${HOST_API_X570:-https://api-x570.ragdemo.win}` 的 **主機名裡**就有 x570，
# 被 IDENTITY_RE 命中 → MSI 被要求覆蓋 HOST_API_X570。但那時 rag.py 的
# HOST_API 是一張「三台都要有」的對照表（前端「連線與來源」彈窗逐台列位址），
# 缺一台就少一列 —— 那是假警告，不是設定錯誤。
#
# ⚠️ 2026-09-27 那三個鍵已刪（見 REMOVED_KEYS），所以**這個例外現在沒有已知
# 使用者**了。留著是因為規則本身還成立：只要有人再把機台代號燒進鍵名，
# 這裡就擋下同一類假警告。**沒有**因為「目前用不到」就放寬 IDENTITY_RE ——
# 那會讓真正的 TS_IP 綁錯也一起消失（下一段的 ports: 判準會補，但少一層少一層）。
#
# 這個例外不放寬真正會炸的保護：`ports:` 的綁定是獨立判準
# （`r.in_ports or r.has_identity_default()`），TS_IP 在 ports: 裡，
# 少設仍然會被報出來（`test_identity_laden_defaults_are_recognised` 鎖住）。
NAME_SCOPED_HOST = re.compile(r"_(x570|mbp|wsl|msi)$", re.I)

# 已被移除的鍵 → 遷移指引。刪掉一個變數名時要同時在這裡加一筆，否則升級後
# .env 裡的舊鍵只會被當成一般「幽靈」報出，使用者看不出該改成什麼。
# 2026-09-27：HOST_API_X570/MBP/MSI 三個合成 HOST_API_URLS（解除三台鎖死）。
    # 鍵名沿用當年的 MSI —— 那是 .env 裡既存的鍵名，不是機台清單。
REMOVED_KEYS = {
    "HOST_API_X570": "HOST_API_URLS=x570=<該機公網 api 網址>",
    "HOST_API_MBP": "HOST_API_URLS=mbp=<該機公網 api 網址>",
    # 2026-10-01：後端主機 msi → wsl，所以遷移目標的 id 也跟著換。
      # 舊鍵名 `HOST_API_MSI` 保持不變 —— 那是 .env 裡既存的鍵名，改了
      # 這條指引就指不到使用者真正要刪的那個鍵。
      "HOST_API_MSI": "HOST_API_URLS=wsl=<該機公網 api 網址>",
}


def _iter_files(root: Path, pattern: str):
    for f in sorted(root.rglob(pattern)):
        if any(part in SKIP_DIRS for part in f.parts):
            continue
        yield f


def scan_compose() -> dict[str, Ref]:
    path = ROOT / "compose.yaml"
    if not path.exists():
        return {}
    refs: dict[str, Ref] = {}
    lines = path.read_text(encoding="utf-8").splitlines()

    in_env_block = False
    env_indent = 0
    for i, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())

        # 追蹤 environment: 區塊（縮排 4，內容 6）
        if re.match(r"^\s{4}environment:\s*$", raw):
            in_env_block, env_indent = True, 4
            continue
        if in_env_block and indent <= env_indent:
            in_env_block = False

        # ports: 裡的變數決定「綁到哪個 IP」。若被別的機器的 TS_IP 綁住，
        # docker 會試圖 bind 非本地 IP 而啟動失敗，所以標出來。
        if re.search(r"\bports:", raw):
            for pm in COMPOSE_REF.finditer(raw):
                pname = pm.group(1)
                if pname not in TOOL_ENV:
                    refs.setdefault(pname, Ref(pname)).in_ports = True

        # 1) environment 區塊裡的純字面值 = 被 compose 寫死
        #    用 flag 而非 continue：continue 會跳掉下面的 COMPOSE_REF 掃描，
        #    導致 `KEY: ${VAR:?...}` 這行的參照完全沒被抓到（2026-09-26 實測，
        #    compose.yaml:24 的 POSTGRES_PASSWORD 因此被誤報成幽靈變數）。
        if in_env_block:
            m = COMPOSE_ENV_LINE.match(raw)
            if m and m.group(1) not in TOOL_ENV:
                key, val = m.group(1), m.group(2)
                # 只有「整個值是字面值」才算寫死。
                #   `KEY: ${VAR:-預設}`  → 可由 .env 覆蓋，不算寫死
                #   `KEY: http://qdrant:6333` → 真的寫死
                # 早期版本對整個 environment 區塊一律標寫死，造成約 45 個
                # 變數發出無效警告（2026-09-26 實測）。
                if "${" not in val:
                    r = refs.setdefault(key, Ref(key))
                    r.compose_hardcoded = True
                    r.readers.append(f"compose.yaml:{i}")

        # 2) 所有 ${VAR} 參照（不受縮排限制，`${VAR:?}` 可能在 expression: 下）
        for m in COMPOSE_REF.finditer(raw):
            name, mod = m.group(1), m.group(2) or ""
            if name in TOOL_ENV:
                continue
            r = refs.setdefault(name, Ref(name))
            r.compose_ref = True
            if f"compose.yaml:{i}" not in r.readers:
                r.readers.append(f"compose.yaml:{i}")
            # 只認「緊接在 $ { 之後」的修飾符，避免把預設值內容誤讀成修飾符
            if mod in (":?", "?"):
                r.required = True

        for m in COMPOSE_DEFAULT.finditer(raw):
            name, default = m.group(1), m.group(2).strip()
            if name in TOOL_ENV:
                continue
            r = refs.setdefault(name, Ref(name))
            if not r.compose_default:
                r.compose_default = default
    return refs


def scan_python() -> dict[str, Ref]:
    refs: dict[str, Ref] = {}
    for root in ("backend", "ingest"):
        base = ROOT / root
        if not base.exists():
            continue
        for f in _iter_files(base, "*.py"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            rel = f.relative_to(ROOT)

            # pass 1：`CONST = os.getenv("VAR", ...)` → CONST 承載 VAR，記下定義行
            ident_env: dict[str, str] = {}
            ident_def_line: dict[str, int] = {}
            for ln, line in enumerate(lines, 1):
                m = PY_IDENT_ENV.match(line)
                if m:
                    ident_env[m.group(1)] = m.group(2)
                    ident_def_line[m.group(1)] = ln

            # pass 1b：每個常數被提到的行（供 pass 3 判斷「是否只有連鎖用途」）
            ident_lines: dict[str, set[int]] = {}
            if ident_env:
                ident_re = re.compile(
                    r"\b(" + "|".join(re.escape(c) for c in ident_env) + r")\b")
                for ln, line in enumerate(lines, 1):
                    for g in ident_re.finditer(line):
                        ident_lines.setdefault(g.group(1), set()).add(ln)

            # pass 2：讀取行為 + 收集 fallback 候選
            fallbacks: list[tuple[str, str, int]] = []   # (高優先, 低優先常數, 行號)
            for ln, line in enumerate(lines, 1):
                if line.lstrip().startswith("#"):
                    continue

                for m in PY_ENV_FALLBACK.finditer(line):
                    hi, lo_const = m.group(1), m.group(2)
                    if lo_const in ident_env and hi != ident_env[lo_const]:
                        fallbacks.append((hi, lo_const, ln))

                for pat in PY_REFS:
                    for m in pat.finditer(line):
                        name = m.group(1)
                        if name in TOOL_ENV:
                            continue
                        r = refs.setdefault(name, Ref(name))
                        # group(2) 只在部分 pattern 存在：os.environ["X"] 沒有
                        # 第二個 group（那個寫法本身就代表無預設值 → 必填）。
                        # 用 lastindex 判斷，避免 pattern 差異導致 IndexError
                        # （2026-09-26 改寫時寫死 group(2)，是個潛在 crash）。
                        tail = m.group(2) if (m.lastindex or 0) >= 2 else None
                        has_default = tail is not None and tail.strip() == ","
                        r.python_read = True
                        if not has_default:
                            r.python_default = False
                            r.required = True
                        r.readers.append(f"{rel}:{ln}")

            # pass 3：判定是否「純連鎖」。
            # 只有當這個常數除了「定義」與「當別人的預設值」之外**沒有任何
            # 其他用處**，才能說「設了高優先就用不到」。判斷方式：它被提到的
            # 行集合必須 ⊆ {定義行, 當預設值那一行}。
            #
            # 為何要這麼嚴謹（2026-09-26 實測）：LLM_MODEL 同樣出現在
            # `os.getenv("OLLAMA_MODELS", LLM_MODEL)` 裡，若只看 fallback
            # 就會誤判「設了 OLLAMA_MODELS 就忽略 LLM_MODEL」—— 但 rag.py:215
            # `else LLM_MODEL` 與 :217 `return LLM_MODEL` 仍在獨立使用它，
            # 那是完全錯誤的宣稱。
            for hi, lo_const, ln in fallbacks:
                used = ident_lines.get(lo_const, set())
                allowed = {ident_def_line.get(lo_const, -1), ln}
                if used and used <= allowed:
                    low = ident_env[lo_const]
                    r = refs.setdefault(low, Ref(low))
                    r.superseded_by = hi
    return refs


def scan_shell() -> dict[str, Ref]:
    refs: dict[str, Ref] = {}
    scripts = ROOT / "scripts"
    if not scripts.exists():
        return refs
    for f in _iter_files(scripts, "*.sh"):
        text = f.read_text(encoding="utf-8", errors="replace")
        # 同檔內自己賦值的 = 局部變數，不是 .env 該管的。
        # 以 ; 或 && 切開後逐段比對，因為一行可能有兩個賦值。
        local: set[str] = set()
        for line in text.splitlines():
            for seg in re.split(r"[;&|]+", line):
                m = SH_ASSIGN.match(seg)
                if m:
                    local.add(m.group(1))
                # SH_ASSIGN 的 group(1) 抓的是宣告的**第一個**名字，
                # `local A="" B=""` 的 B 就漏了。用 SH_DECL 把整行宣告都收進來。
                d = SH_DECL.match(seg)
                if d:
                    local.update(x.group(1) for x in SH_DECL_ASSIGN.finditer(d.group(1)))
                # `read VAR` 的目標：段首的 read（while 可有可無）。互動式
                # 讀憑證的腳本全靠這裡擋掉，否則它們會被寫進 .env.example。
                mr = SH_READ_ASSIGN.match(seg)
                if mr:
                    local.update(_read_targets(mr.group("rest")))
        for ln, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in SH_READ.finditer(line):
                name = m.group(1) or m.group(2)
                if not name or name in local or name in TOOL_ENV:
                    continue
                # ⚠ `$UPPER` 這種「只用大寫」的反查會把 shell 的**控制流變數**
                #   誤判成環境變數，而那些是最常見的一批。真的踩過
                #   （2026-09-27 加 host-doctor.sh 時一次踩齊 12 個假變數進
                #   .env.example，害 .env.example 與 --template 不一致、CI 紅燈）：
                #
                #   (a) `while read -r A B` 的目標 —— 是**賦值**不是讀環境變數，
                #       而 SH_ASSIGN 只認 `NAME=` 開頭，抓不到。
                #   (b) `for FP in ...` 的迴圈變數 —— 同理沒有 `=`。
                #
                # 所以除了 SH_ASSIGN（`NAME=`）之外，再扣掉兩種明確的賦值位置：
                # while read 的目標、for 的迴圈變數。兩者都用**行內**比對。
                # 這裡不做「宣告過就跳過」那種更寬的判斷：那會連
                # `local MISS=""` 這種真的宣告過的也一起扣掉，行為正確但會
                # 順手蓋掉別的判定分支，而那超出本 scope。
                if _sh_assigns_control(text, name):
                    continue
                r = refs.setdefault(name, Ref(name))
                r.shell_read = True
                r.readers.append(f"{f.relative_to(ROOT)}:{ln}")
    return refs


# shell 控制流裡的賦值位置：這兩種沒有 `=`，SH_ASSIGN 認不出來，但確實是賦值。
#   while read -r A B C   → 目標變數
#   for A in ...          → 迴圈變數
#
# ⚠️ 2026-10-01：`while` 改成**選用**，因為互動式輸入根本沒有 while：
#   IFS= read -rs CF_ID        # 讀的是 stdin，不是環境變數
#   read -r LINE
# 原本只認 `while ...read`，於是任何互動式 `read VAR` 的 VAR 都被當成
# 「讀環境變數」→ 進 .env.example。症狀是 scripts/access-check.sh 一加進來，
# .env.example 就多出 `CF_ID=`／`CF_SECRET=` 兩行，CI 與 pre-push 的
# `test_template_has_no_lan_ip_assignment` 直接紅（它斷言兩者逐字相同）。
# 那兩行不只是多餘 —— 它會**教人把互時輸入的變數設進 .env**，而設了也沒用
# （`read` 讀 stdin，不看環境變數）。
#
# `^` 錨定是刻意的：`scan_shell` 會先用 `[;&|]+` 把一行切成指令段，段首就是
# 指令起點，所以錨定後不會把 `grep read foo` 之類誤判成 read 賦值。
# （那會造成**反方向的錯**：把真的環境變數誤認為局部而漏掉。）
# re.M 是給 _sh_assigns_control 的 finditer 用 —— 那邊掃的是整份檔案。
#
# ⚠️ `IFS=` 的值是**空的**（`IFS= read -r A` 是清 IFS，不是設 IFS），
# 所以那格必須用 `[^\s]*` 不能用 `\S+`。寫成 `\S+` 會讓整條正則匹配不到
# `while IFS= read -r FP`，於是 FP 變成幽靈鍵進 .env.example —— 正是
# 2026-09-27 踩過的那個坑（host-doctor.sh:291 的註解寫著）。
# bash `read` 的旗標有兩種：帶參數的（-p PROMPT、-a ARRAY、-d 設定字元、
# -n/-N/-t 數字、-u FD）與不帶的（-r、-s、-e）。必須區分，否則
# `read -p 'Enter: ' PW` 會把 'Enter: ' 當成目標變數而漏掉 PW。
#
# 這裡刻意**不用一條 regex 硬吞**。試過（2026-10-01），回溯會產生垃圾匹配：
# `read -a arr` 裡 `-a` 的參數就是變數名，regex 吃完就沒東西可抓，於是回溯到
# `-\w+` 只吃 `-a`、把 `arr` 的 `r` 當成變數名 —— 抓出一個不存在的變數，
# 那正是「幽靈鍵」的另一種形態。改用明確的 tokenizer：旗標逐個判斷，帶參數的
# 就跳過下一個 token。
SH_READ_ASSIGN = re.compile(
    r"^\s*(?:while\s+)?(?:IFS=[^\s]*\s+)?read\b(?P<rest>.*)$", re.M
)
SH_READ_ARG_FLAGS = set("adnNptu")     # 帶參數的 read 旗標
# 最後一段刻意用 [^\s;&|]+ 而不是 \S+：否則 \S+ 會從前一個位置就把分號一起
# 吞掉（`FP;` 變成一個 token），識別字檢查失敗 → **漏掉真正的變數名**。
_SH_TOKEN = re.compile(r"""'[^']*'|"[^"]*"|&&|\|\||;|[^\s;&|]+""")
_SH_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
SH_FOR_ASSIGN = re.compile(r"\bfor\s+([A-Za-z_][A-Za-z_0-9]*(?:\s+[A-Za-z_][A-Za-z_0-9]*)*)\s+in\b")


def _read_targets(rest: str) -> list[str]:
    r"""`read` 後面的變數名（已扣掉旗標與帶參數旗標的參數）。

    分隔符（`;`／`&&`／`||`）必須是獨立 token：否則 `\S+` 會把 `FP;` 整個
    當一個 token，而它不符合識別字 → **漏掉真正的變數名**。
    `_sh_assigns_control` 掃的是整份檔案（沒有先切段），所以這裡自己處理。
    """
    out: list[str] = []
    skip = False
    for tok in _SH_TOKEN.findall(rest):
        if skip:
            skip = False
            continue
        if tok in (";", "&&", "||", "do", "done", "{"):
            break
        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
            if tok[1] in SH_READ_ARG_FLAGS:
                skip = True
            continue
        if _SH_IDENT.fullmatch(tok):
            out.append(tok)
    return out

# `local A="" B="" C=""` 一行宣告多個：SH_ASSIGN 只會抓到**第一個**名字
# （它的 group(1) 就在開頭），所以 B、C 會被當成讀環境變數。
# 踩過：2026-09-27 `local T MISSING="" OK=""` 讓假的 `OK=` 進 .env.example。
SH_DECL = re.compile(r"^\s*(?:local|declare|export|readonly|typeset)\s+(.*)$")
SH_DECL_ASSIGN = re.compile(r"(?<![$\{\"'\w])([A-Za-z_][A-Za-z_0-9]*)\s*=")


def _sh_assigns_control(text: str, name: str) -> bool:
    """這個名字是 read / for 的目標嗎（即：被賦值，不是讀環境變數）。"""
    for m in SH_READ_ASSIGN.finditer(text):
        if name in _read_targets(m.group("rest")):
            return True
    for m in SH_FOR_ASSIGN.finditer(text):
        if name in m.group(1).split():
            return True
    return False


def build_registry() -> dict[str, Ref]:
    registry: dict[str, Ref] = {}
    for scanner in (scan_compose, scan_python, scan_shell):
        for name, ref in scanner().items():
            cur = registry.get(name)
            if cur is None:
                registry[name] = ref
                continue
            # 合併兩個來源的判定
            cur.required = cur.required or ref.required
            cur.compose_ref = cur.compose_ref or ref.compose_ref
            cur.compose_hardcoded = cur.compose_hardcoded or ref.compose_hardcoded
            cur.python_read = cur.python_read or ref.python_read
            cur.shell_read = cur.shell_read or ref.shell_read
            cur.in_ports = cur.in_ports or ref.in_ports
            if not cur.compose_default:
                cur.compose_default = ref.compose_default
            if ref.superseded_by:
                cur.superseded_by = ref.superseded_by
            for rd in ref.readers:
                if rd not in cur.readers:
                    cur.readers.append(rd)
    return registry


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    vals: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = ASSIGN.match(raw)
        if m:
            vals[m.group(1)] = m.group(2)
    return vals


def declared_hosts() -> list[str]:
    """總表宣告的機台清單（讀 `hosts.shared.env` 的 `HOSTS=` 那一行）。

    找不到檔案或沒宣告 → 空清單。呼叫端要自己決定怎麼報（缺宣告與
    「沒有 peer」是兩件事，不能混為一談）。
    """
    table = ROOT / "settings" / "env" / "hosts.shared.env"
    if not table.exists():
        return []
    for raw in table.read_text(encoding="utf-8").splitlines():
        m = ASSIGN.match(raw.strip())
        if m and m.group(1) == "HOSTS":
            return [h.strip() for h in m.group(2).split(",") if h.strip()]
    return []


# ── .env 版面分類（shared 在前、per-host 在最後）────────────────────────
#
# 為什麼需要這個分類：`.env.example` 原本依**設定類別**分節（必填／憑��／
# 選填／寫死），而 per-host 的鍵因此散落全檔 —— 實測 `HOST_ID` 在最前面、
# `POSTGRES_PASSWORD`（per-host 機密）在第 63 行、`OLLAMA_URLS` 在第 294 行，
# 而共用憑證 `QDRANT_PEER_API_KEY` 在第 260 行。三台的 .env 長得都不一樣，
# 要人眼比對才知道哪個鍵該跟著共用值更新。
#
# 分類的**三個來源，全部是資料不是程式**（寫死在這裡就會漂移）：
#   1. `hosts.shared.env` 的 `<機台>_<鍵>=` 列 → per-host（單一真相）
#   2. `env-sync.sh` 的 `PER_HOST_SECRETS`   → per-host 機密（單一真相）
#   3. 總表裡那行註解 `# LOCAL_ONLY: …`      → 刻意不進表、但仍是 per-host
#      的鍵（TS_IP／HOST_ID）。**必須是註解** —— 非註解的行會被 py_apply
#      讀成 layer 而報「非 <機台>_<鍵> 的行」（2026-10-02 第一版就踩到）。
#
# 為什麼前綴不寫進 .env：compose 只認 ${VAR} 插值，沒有依 HOST_ID 動態選欄的
# 能力。前綴若寫在 .env，`LLM_MODEL` 會掉回原始碼預設（**靜默**劣化）、
# `TS_IP` 會讓 docker 綁錯而啟動失敗。所以 per-host 的識別寫在**區段標題**，
# 識別力與前綴相同，契約不動。完整論證見 settings/env/README.md §9。
_LOCAL_ONLY_RE = re.compile(r"^#\s*LOCAL_ONLY:\s*(.+)$", re.M)


def per_host_keys() -> set[str]:
    """回傳「只屬於某一台」的鍵名集合。

    讀三個來源，任一缺失就少一類（呼叫端要能分辨「沒有 per-host 鍵」與
    「讀不到來源」—— 後者是壞掉的 repo，不是沒有差異）。
    """
    out: set[str] = set()
    hosts = declared_hosts()
    table = ROOT / "settings" / "env" / "hosts.shared.env"
    if hosts and table.exists():
        text = table.read_text(encoding="utf-8")
        # ⚠️ 鍵名那段必須用 [A-Za-z_][A-Za-z_0-9]* 而**不是** `.+`：
        # `.+` 會連 `=值` 一起吃掉，於是 out 裡出現
        # 「HOST_NAME=wsl」這種整行 —— 分類看起來成功但一個鍵都對不上。
        # 第一版就是這樣：23 個「鍵」裡 13 個帶著 `=值`。
        pref = re.compile(r"^(" + "|".join(re.escape(h) for h in hosts)
                          + r")_([A-Za-z_][A-Za-z_0-9]*)=")
        for raw in text.splitlines():
            m = pref.match(raw)
            if m:
                out.add(m.group(2))
    sync = ROOT / "scripts" / "env-sync.sh"
    if sync.exists():
        m = re.search(r'^PER_HOST_SECRETS="([^"]+)"',
                      sync.read_text(encoding="utf-8"), re.M)
        if m:
            out.update(m.group(1).split())
    if table.exists():
        m = _LOCAL_ONLY_RE.search(table.read_text(encoding="utf-8"))
        if m:
            out.update(x.strip() for x in m.group(1).split(",") if x.strip())
    return out


def _is_machine_scoped(key: str) -> bool:
    """這個鍵名是否帶了機台前綴（`wsl_OLLAMA_URLS` 這種）。

    機台清單來自總表宣告，不寫死。找不到宣告時**不**回報（此時報「每個鍵都
    帶前綴」會是滿屏假警告）；`check_hosts_table` 會另外報缺宣告。
    """
    hosts = declared_hosts()
    if not hosts:
        return False
    prefix = key.partition("_")[0]
    return prefix.lower() in {h.lower() for h in hosts}


def check_hosts_table(reg: dict[str, Ref], env: dict[str, str]) -> int:
    """settings/env/hosts.shared.env 的每個 base 鍵都必須真的有程式讀取。

    為什麼要查：總表是被追蹤的 per-host 唯一真相（2026-09-27 起的機制），
    但它不在 compose／python／shell 的掃描範圍內，所以這裡補一次反查 ——
    表裡打錯一個字（TS_IP→TS_I），render 會照樣寫進 .env，症狀是
    「設定看起來都對，但那台的容器綁錯 IP」。機械判定，不靠人記。

    值與 .env 的一致性由 env-sync.sh --check 負責（那裡才有 HOST_ID 選擇器），
    這裡只回答「這個鍵有人讀嗎」。
    """
    table = ROOT / "settings" / "env" / "hosts.shared.env"
    if not table.exists():
        print("  [per-host 總表] 找不到 settings/env/hosts.shared.env")
        return 1
    problems = 0
    seen: dict[str, int] = {}
    rows = {}
    for raw in table.read_text(encoding="utf-8").splitlines():
        m = ASSIGN.match(raw.strip())
        if m:
            rows[m.group(1)] = m.group(2)
    # 機台清單讀總表裡的 `HOSTS=` 那一行。2026-09-27 之前這裡寫死
    # `("x570", "mbp", "wsl")`，第 4 台就查不到自己的列（而且是**靜默**漏查：
    # 不報錯、只是少算，輸出的「N 個鍵 × 3 台」看起來完全正常）。
    #
    # ⚠️ 2026-09-29 修掉一個讓整支腳本必定崩潰的 bug：原本這裡是
    # `del rows  # 只留 base 鍵；宣告列已取出`，下一行 `for k in rows:`
    # 于是 100% 撞上 UnboundLocalError —— 任何一次完整審計（`env-audit.py`
    # 不帶參數）都跑到這裡就死，所以「完整審計可用」這件事從未被驗證過。
    # 宣告列已經由上面的 `rows.pop("HOSTS", ...)` 移除了，`del rows` 從來
    # 沒有它宣稱的「只留 base 鍵」作用 —— 它只是把整個名稱刪掉。
    # 刪掉那行即可，`rows` 此時自然只剩 base 鍵。
    hosts = [h.strip() for h in rows.pop("HOSTS", "").split(",") if h.strip()]
    if not hosts:
        print(f"  [per-host 總表] {table} 缺少 `HOSTS=<機台,機台,…>` 宣告")
        return 1
    for k in rows:
        host, _, base = k.partition("_")
        if host not in hosts:
            continue
        seen[base] = seen.get(base, 0) + 1
        if base not in reg:
            print(f"  [per-host 總表] {base} 沒有任何程式讀取（拼錯？或是幽靈變數）")
            problems += 1
        elif base in POLICY_EXCLUDED:
            print(f"  [per-host 總表] {base} 是政策性停用變數，不該出現在總表")
            problems += 1
    for base, n in sorted(seen.items()):
        if n != len(hosts):
            print(f"  [per-host 總表] {base} 只有 {n}/{len(hosts)} 台有列"
                  f"（宣告 {'/'.join(hosts)}）")
            problems += 1
    print(f"  [per-host 總表] {len(seen)} 個鍵 × {len(hosts)} 台"
          f"（{'/'.join(hosts)}；值與 .env 的一致性由 env-sync.sh --check 負責）")
    return problems


def audit(env: dict[str, str], reg: dict[str, Ref], label: str) -> int:
    problems = 0

    # ── 機台身份回填檢查（舊版 KNOWN 以人工 required=True 擋的，改寫後補回）──
    # compose 的預設值寫死了某一台機器（HOST_ID:-x570、TS_IP:-100.119.83.111）。
    # 在別的機器上這些預設值是錯的：少設 TS_IP 會讓 docker 去 bind 別人的
    # IP 而啟動失敗，少設 HOST_ID 會讓兩台機器在 registry 裡撞成同一個主鍵。
    # 規則：本機 HOST_ID 與預設值內建的那台不同時，那些變數就從「選填」
    # 變成「必填」—— 完全機械判定，不需要人工維護清單。
    baked_host = reg["HOST_ID"].compose_default if "HOST_ID" in reg else ""
    my_host = env.get("HOST_ID", "")
    if baked_host and my_host and my_host != baked_host:
        wrong = []
        for name, r in reg.items():
            if name in env or name == "HOST_ID":
                continue
            # 已被更高優先的變數覆蓋 → 沒設也沒關係
            # （rag.py:33-34：設了 OLLAMA_URLS，OLLAMA_BASE_URL 就用不到）
            if r.superseded_by and env.get(r.superseded_by):
                continue
            if r.in_ports or r.has_identity_default():
                wrong.append(name)
        if wrong:
            print(f"  [{label}] 機台身份必須覆蓋（本機 HOST_ID={my_host}，"
                  f"但這些的預設值是 {baked_host} 的）:")
            for k in sorted(wrong):
                r = reg[k]
                why = "用在 ports: 綁定 IP，缺了會啟動失敗" if r.in_ports \
                    else f"預設值 {r.compose_default!r} 屬於 {baked_host}"
                print(f"    - {k:<20} {why}")
            problems += len(wrong)

    # ── 純 fallback 連鎖：低優先來源 ──
    covered = sorted(k for k in env
                     if k in reg and reg[k].superseded_by
                     and env.get(reg[k].superseded_by))
    if covered:
        print(f"  [{label}] 低優先來源（高優先的設了有效值時，這個用不到）:")
        for k in covered:
            where = ", ".join(r for r in reg[k].readers
                              if r.startswith("backend") or r.startswith("ingest"))
            print(f"    - {k:<20} {reg[k].superseded_by} 設了有效值就用不到；"
                  f"該變數為空時才回退到它  [{where}]")
        problems += len(covered)

    ghosts = sorted(k for k in env if k not in reg)

    # 政策性停用的變數不該出現在 .env（IP 準則等）
    banned = sorted(k for k in env if k in POLICY_EXCLUDED)
    if banned:
        print(f"  [{label}] 政策上已停用（但程式仍會讀，故會被反查到）:")
        for k in banned:
            print(f"    - {k}")
            print(f"        {POLICY_EXCLUDED[k]}")
        problems += len(banned)

    if ghosts:
        print(f"  [{label}] 幽靈變數（沒有任何程式讀，設了沒作用）:")
        for k in ghosts:
            print(f"    - {k}")
            if k in REMOVED_KEYS:
                print(f"        ⚠️ 這個鍵已被移除，請從 .env 刪掉並改用：{REMOVED_KEYS[k]}")
        problems += len(ghosts)

    missing = sorted(k for k, r in reg.items() if r.required and k not in env)
    if missing:
        print(f"  [{label}] 缺少必填變數:")
        for k in missing:
            print(f"    - {k:<24} {reg[k].source_note()}")
        problems += len(missing)

    # 只在 compose 寫死、但 .env 設了的 —— 擺了不會生效，是誤導
    dead = sorted(k for k in env
                  if k in reg and reg[k].compose_hardcoded and not reg[k].compose_ref)
    if dead:
        print(f"  [{label}] 被 compose 寫死（.env 設了對容器無效，要改 compose.yaml）:")
        for k in dead:
            print(f"    - {k}")
        problems += len(dead)

    # 有讀，但 compose 沒列入 environment → .env 的值到不了容器
    not_fwd = sorted(k for k in env
                     if k in reg and not reg[k].compose_ref
                     and not reg[k].compose_hardcoded
                     and (reg[k].python_read or reg[k].shell_read))
    host_only = [k for k in not_fwd if reg[k].host_only_reader()]
    container = [k for k in not_fwd if not reg[k].host_only_reader()]
    if container:
        print(f"  [{label}] 沒傳入容器（程式在容器裡跑，但拿不到 .env 的值，只吃原始碼預設）:")
        for k in container:
            print(f"    - {k:<22} {reg[k].readers_label}")
        problems += len(container)
    if host_only:
        print(f"  [{label}] 只有 host 端讀得到（ingest 管線／腳本，容器用不到）:")
        for k in host_only:
            print(f"    - {k:<22} {reg[k].readers_label}")
        problems += len(host_only)

    per_machine = sorted(k for k in env if _is_machine_scoped(k))
    if per_machine:
        print(f"  [{label}] 前綴變數寫在執行期 .env（無效）: {'、'.join(per_machine)}")
        print("        compose 只認 ${VAR}，沒有依 HOST_ID 動態選前綴的能力 ——")
        print("        這些值到不了容器，症狀是靜默吃回原始碼預設。")
        print("        per-host 值請寫進 settings/env/hosts.shared.env，"
              "由 env-sync.sh render 挑列。")
        problems += len(per_machine)
    return problems


def check_drift(root: dict[str, str], back: dict[str, str]) -> int:
    if not (root and back):
        return 0
    shared = sorted(set(root) & set(back))
    drift = [k for k in shared if root[k] != back[k]]
    print(f"  [副本] 根 .env {len(root)} 變數／backend/.env {len(back)} 變數"
          f"／同名 {len(shared)} 個")
    if drift:
        print("  ❌ 值不一致（2026-09-26 造成 401 的原因）:")
        for k in drift:
            print(f"    - {k}")
        return len(drift)
    print("  ⚠️  兩份並存但值一致 —— 重複維護本身就是風險，見 HOST-UPGRADE.md §0b")
    return 0


def print_template(reg: dict[str, Ref]) -> None:
    print("# .env.example — 變數骨架")
    print("#")
    print("# 本檔由 `scripts/env-audit.py --template` 產生，**完全從程式碼反查**，")
    print("# 不含任何真實憑證，可安全進版控。改變數請改程式碼後重新產生。")
    print("#")
    print("# 填法：cp .env.example .env && chmod 600 .env，再逐項填值。")
    print("#")
    print("# 分類依據：")
    print("#   必填       compose 用 ${X:?}、Python 讀取時沒給預設值，")
    print("#              或是「預設值是別台機器的」（本機 HOST_ID 與預設不同時）")
    print("#   必填(憑證) 同上，且名稱含 KEY/SECRET/TOKEN/PASSWORD → 必須先填")
    print("#   選填       有預設值；不填就吃預設")
    print("#   憑證       選填的憑證類（一律 chmod 600）")
    print("#   未傳入容器  compose 沒列 environment: → .env 的值到不了容器，")
    print("#              要生效得改 compose.yaml")
    print("#   host 端    只有 ingest 管線／shell 腳本讀得到，容器本來就不需要")
    print("#   寫死       compose 用字面值覆蓋 → 這裡設了對容器無效")
    print("#")
    print("# 關於機台差異：per-host 的值**不在這個檔**填。")
    print("# 它們在 settings/env/hosts.shared.env（每台一組 x570_/mbp_/wsl_ 的值，")
    print("# 追蹤、明文、無憑證），由 scripts/env-sync.sh render 依本機 HOST_ID")
    print("# 挑列、展開 ${VAR} 後寫進 .env。機台清單是那個檔裡的 `HOSTS=` 一行 ——")
    print("# 加機器＝加一行資料，不必改程式（2026-09-27 之前寫死在 env-sync.sh 裡）。")
    print("#")
    print("# 為什麼前綴不放進 .env：compose 只認 ${VAR}，沒有依 HOST_ID 動態選")
    print("# wsl_/x570_ 的能力；前綴若寫在 .env，值會被 compose 讀不到而回退原始碼")
    print("# 預設 —— 靜默劣化。")
    print("#")
    print("# 同一台機器「同時」要有多組設定時（例：多個 peer 的位址），id 燒進**值**：")
    print("#   HOST_API_URLS=x570=https://…,wsl=https://…  逗號分隔；未設＝單機無 peer")
    print("#")
    print("# 各機怎麼認出自己的身份：HOST_ID、HOST_NAME、HOST_MACHINE_ID —— 這三個每台")
    print("# 不同。HOST_ID 是 render 的選擇器，只能在 .env 手動設一次")
    print("#（要先知道本機是誰才挑得到列）。")
    print("# TS_IP 選填：填了就綁那個位址（要讓別台機器連你的 qdrant/pg 才需要），")
    print("# 不填就綁 127.0.0.1 —— 所以**沒有 Tailscale 也能跑**。")
    print("#")
    print("# ⚠️ 一律 chmod 600：.env 有憑證。")
    print("#")

    buckets: dict[str, list[Ref]] = {}
    for r in reg.values():
        if r.name in POLICY_EXCLUDED:
            continue                      # 另以註解區塊呈現，不產出 NAME= 行
        if r.compose_hardcoded and not r.compose_ref:
            kind = "compose 寫死（.env 設了無效）"
        elif not r.compose_ref and (r.python_read or r.shell_read):
            kind = ("host 端（容器拿不到）" if r.host_only_reader()
                    else "未傳入容器（要加進 compose）")
        elif r.superseded_by:
            # 有上層就不是必填（設定上層即可），但要註明它是低優先。
            # 必須排在 secret 之前 —— 否則必填的憑證會被擺進「選填」。
            kind = "設定（選填）"
        elif r.required or r.has_identity_default() or r.in_ports:
            kind = "必填（含憑證）" if r.secret else "必填"
        elif r.secret:
            kind = "憑證（選填）"
        else:
            kind = "設定（選填）"
        buckets.setdefault(kind, []).append(r)

    # 版面：shared 在前、per-host 在最後。分類來自 per_host_keys()（三個資料
    # 來源），不在這裡寫死任何鍵名。
    #
    # 這一段取代原本「依設定類別分節」的排法 —— 那個排法對「這台怎麼跑」
    # 有意義（哪些必填），但對「三台的檔案能不能一致」沒有。
    ph = per_host_keys()

    # 區段標題帶機器代號的話 .env.example 就不能跨機共用（它是同一份檔）。
    # 所以範本寫佔位，由 `env-sync.sh render` 依本機 HOST_ID 填成
    # `# ══ HOST: wsl ══`。**識別力與前綴相同，而 compose 的契約不動。**
    banner = "══ HOST: <本機 HOST_ID> ══"
    printed_scope: set[str] = set()

    def emit(kind: str, label: str, scope: str) -> None:
        # scope: "shared"（三台共用）｜"host"（只屬本機）
        # 用 (r.name in ph) == (scope == "host") 一次過濾掉兩邊 ——
        # 寫兩個迴圈會有「某一邊漏掉」的中間狀態，而那正是版面漂移的來源。
        items = [r for r in buckets.get(kind, [])
                 if (r.name in ph) is (scope == "host")]
        if not items:
            return
        # 區塊標題只在**該區塊第一個有內容的類別**印一次。
        # 若無條件印，會出現「有標題但底下沒東西」的空區塊 —— 那正是版面
        # 漂移最難查的形狀（人會以為那一類真的沒有鍵）。
        if scope not in printed_scope:
            printed_scope.add(scope)
            print(f"\n# {banner}" if scope == "host"
                  else f"\n# ══ 共用（三台應該相同）══")
        print(f"\n# ══ {label} ══")
        for r in sorted(items, key=lambda x: x.name):
            print(f"# {r.name}  —  消費者：{r.readers_label}")
            # 憑證永遠不印預設值
            if r.compose_default and not r.secret:
                print(f"#   預設值：{r.compose_default}")
            if r.required:
                print("#   必填：compose 用 ${...:?} 或 Python 讀取時沒給預設")
            if r.in_ports and not r.compose_default:
                # 沒有預設值可退 → docker 真的會綁不上、啟動失敗。
                print("#   必填：用在 compose 的 ports:（綁定 IP），沒設會啟動失敗")
            elif r.in_ports:
                # ⚠️ 這裡曾對**所有** ports: 變數都印「必填…沒設會啟動失敗」。
                # 那是錯的：只要寫成 ${VAR:-預設}，留空就會退回預設、**不會**失敗
                # （`${VAR:?}` 才會）。錯的說法會產出「TS_IP 必填」——
                # 而 TS_IP 實務上三台都留空，只在**某台自己的** .env 設值才會
                # 讓對端能直連。一份說「必填」的範例檔會誘導人填 tailscale 位址，
                # 那正好把 qdrant(6333) 與 postgres(5432) 的暴露面從 localhost
                # 擴大到 tailnet 可達。文件主動引導那個方向，比不寫更糟。
                print(f"#   選填：用在 compose 的 ports:（綁定 IP）；留空則綁 {r.compose_default}")
            elif r.has_identity_default() and not r.superseded_by:
                print(f"#   預設值屬別台機器，本機必須覆蓋")
            if r.superseded_by:
                print(f"#   低優先：設了 {r.superseded_by} 的有效值就用不到")
            if r.compose_hardcoded and not r.compose_ref:
                print("#   ⚠️ compose 以字面值覆蓋，這裡設了對容器無效（要改 compose）")
            elif not r.compose_ref and r.python_read and not r.host_only_reader():
                print("#   ⚠️ compose 沒列入 environment，這裡設的值到不了容器")
            if r.readers:
                shown = ", ".join(r.readers[:3])
                more = f" …等 {len(r.readers)} 處" if len(r.readers) > 3 else ""
                print(f"#   讀取處：{shown}{more}")
            print(f"{r.name}=")
            print()

    KINDS = ("必填", "必填（含憑證）", "設定（選填）", "憑證（選填）",
             "未傳入容器（要加進 compose）", "host 端（容器拿不到）",
             "compose 寫死（.env 設了無效）")
    for scope, title in (("shared", "共用（三台應該相同）"),
                         ("host", banner)):
        for kind in KINDS:
            emit(kind, kind, scope)

    # 政策性停用的變數：**刻意不**印 `NAME=` 行。
    # .githooks/pre-push:31 與 .github/workflows/ci.yml 都會擋 `^LAN_IP=`，
    # 這邊若印成賦值形式，push 會直接失敗（2026-09-26 實測）。
    banned_items = [reg[n] for n in sorted(POLICY_EXCLUDED) if n in reg]
    if banned_items:
        print("# ══ 政策上已停用（刻意不列出 NAME= —— 追蹤檔不得出現該賦值）══")
        for r in banned_items:
            print(f"# {r.name}  —  消費者：{r.readers_label}")
            for chunk in _wrap(POLICY_EXCLUDED[r.name], 76):
                print(f"#   {chunk}")
            if r.readers:
                print(f"#   讀取處：{', '.join(r.readers[:3])}")
        print()


def _wrap(text: str, width: int) -> list[str]:
    """按字寬切行（中英混排時不要切壞）。"""
    out, cur = [], ""
    for ch in text:
        if len(cur.encode("utf-8")) > width * 3:
            out.append(cur)
            cur = ""
        cur += ch
    if cur:
        out.append(cur)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", action="store_true", help="輸出 .env.example 骨架")
    ap.add_argument("--quiet", action="store_true", help="只輸出摘要")
    ap.add_argument("--list", action="store_true",
                    help="列出反查到的所有變數（除錯用）")
    args = ap.parse_args()

    reg = build_registry()

    if args.list:
        print(f"反查到的變數（{len(reg)} 個）:")
        for name in sorted(reg):
            r = reg[name]
            flag = "必填" if r.required else "選填"
            print(f"  {flag}  {name:<26} {r.readers_label:<22} {r.source_note()}")
        return 0

    # --template 是產生器：只印骨架，讓 `> .env.example` 能直接用。
    # 與稽核輸出混在一起就沒辦法重導向，故兩者互斥，
    # 且必須在印任何稽核 banner **之前**就 return（否則檔案開頭會混入
    # 「════ env audit ════」那三行）。
    if args.template:
        print_template(reg)
        return 0

    print("════ env audit ════")
    print(f"  反查來源：compose.yaml + backend/**/*.py + ingest/**/*.py + scripts/*.sh")
    print(f"  實際讀取的變數：{len(reg)} 個（全部從程式碼推導，無人工清單）")

    root_env = load_env(ROOT / ".env")
    back_env = load_env(ROOT / "backend" / ".env")
    front_env = load_env(ROOT / "frontend" / ".env")

    if not root_env and not back_env:
        print("  ⚠️  找不到 .env（若這是刚 clone 的機器，正常）")

    n = 0
    if root_env and not args.quiet:
        n += audit(root_env, reg, "根 .env")
    if back_env:
        n += audit(back_env, reg, "backend/.env")
    if front_env:
        print(f"  [frontend/.env] {len(front_env)} 個變數"
              f"（{', '.join(sorted(front_env))}）— 獨立於後端，不參與本 audit")
    n += check_drift(root_env, back_env)
    if not args.quiet:
        n += check_hosts_table(reg, root_env)

    print(f"\n════ {n} 項需處理 ════")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
