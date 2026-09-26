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
機台名燒進變數名本身（rag.py:167-171 的 `HOST_API_X570`／`HOST_API_MBP`／
`HOST_API_MSI`），不是執行時剝前綴。已刪除該說明。

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
`HOST_ID:-x570` → 預設是 x570 的。若 `.env` 的 `HOST_ID` 是 `mbp` 或 `msi`，
凡預設值內含 tailscale IP 或機台 id 的變數、以及出現在 `ports:` 裡的變數，
都從「選填」升級為「必填」。不用人工維護清單，且 mbp/msi 少設 `TS_IP`
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
    # 本專案測試 harness 的覆寫點（tests/test_env_sync.py 用 fixture 目錄
    # 隔離執行 scripts/env-sync.sh；不是給 .env 設的，不進 .env.example）
    "ENV_SYNC_DIR", "ENV_SYNC_ENV",
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
        mbp/msi 少設 TS_IP 就會去 bind x570 的 IP，docker 啟動即失敗。"""
        if not self.compose_default:
            return False
        return bool(IDENTITY_RE.search(self.compose_default))


# 預設值中的機台身份：tailscale 的 100.x.x.x 網段，或三台的 id 字面量
IDENTITY_RE = re.compile(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b(x570|mbp|msi)\b")


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
        for ln, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in SH_READ.finditer(line):
                name = m.group(1) or m.group(2)
                if not name or name in local or name in TOOL_ENV:
                    continue
                r = refs.setdefault(name, Ref(name))
                r.shell_read = True
                r.readers.append(f"{f.relative_to(ROOT)}:{ln}")
    return refs


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

    per_machine = sorted(k for k in env if re.match(r"^(msi|mbp|x570)_", k, re.I))
    if per_machine:
        print(f"  [{label}] 機台前綴變數（{'、'.join(per_machine)}）")
        print("        注意：程式**不會**自動剝掉前綴。實際慣例是把機台名燒進變數名，")
        print("        見 rag.py:167-171 的 HOST_API_X570 / HOST_API_MBP / HOST_API_MSI。")
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
    print("# .env.example — 三機共用的變數骨架")
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
    print("# 關於機台差異：程式**不會**自動剝掉 msi_/mbp_/x570_ 前綴。")
    print("# 既有慣例是把機台名直接燒進變數名，見 rag.py:167-171：")
    print("#   HOST_API_X570 / HOST_API_MBP / HOST_API_MSI")
    print("# 新增機台專屬變數時請比照這個寫法，並在 .env 設對應的值。")
    print("#")
    print("# 各機怎麼認出自己的身份：HOST_ID（x570/mbp/msi）、TS_IP、")
    print("# HOST_NAME、HOST_MACHINE_ID —— 這四個每台不同，值不進版控。")
    print("# 注意 TS_IP 不是擺著就好：它用在 compose 的 ports:，")
    print("# 沒設會去 bind 預設值那台機器的 IP，docker 直接啟動失敗。")
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

    for kind in ("必填", "必填（含憑證）", "設定（選填）", "憑證（選填）",
                 "未傳入容器（要加進 compose）", "host 端（容器拿不到）",
                 "compose 寫死（.env 設了無效）"):
        items = buckets.get(kind)
        if not items:
            continue
        print(f"\n# ══ {kind} ══")
        for r in sorted(items, key=lambda x: x.name):
            print(f"# {r.name}  —  消費者：{r.readers_label}")
            # 憑證永遠不印預設值
            if r.compose_default and not r.secret:
                print(f"#   預設值：{r.compose_default}")
            if r.required:
                print("#   必填：compose 用 ${...:?} 或 Python 讀取時沒給預設")
            if r.in_ports:
                print("#   必填：用在 compose 的 ports:（綁定 IP），沒設會啟動失敗")
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

    print(f"\n════ {n} 項需處理 ════")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
