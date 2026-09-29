#!/usr/bin/env bash
# env-sync — 三機共用設定與憑證的分發、合併、稽核（sops + age）。
#
# 解決的問題：7 把共用憑證必須三台一致，過去靠人工複製，2026-09-26 已造成
# 兩次不對稱 401／心跳失敗。另有 2 把是 per-host 機密，**刻意不**走這條路。
#
# 分層（詳見 settings/env/README.md），合併順序由低到高：
#   settings/env/common.env              追蹤明文，共用非敏感鍵
#   settings/env/secrets.common.enc.env  追蹤加密，7 把共用憑證真值
#   settings/env/hosts.shared.env        追蹤明文，三台 per-host 值（前綴 <機台>_<鍵>）
#   .env（repo 根）                      不追蹤、chmod 600，執行期唯一真相
#
# ⚠️ QDRANT_API_KEY / POSTGRES_PASSWORD 刻意**不在**下面任何一個 layer：
#   它們是各機自己容器的認證，沒有跨機讀寫關係。真值只留該機 .env。
#
# 為什麼前綴不直接放進 .env：compose 只認 ${VAR}，沒有「依 HOST_ID 動態選
# msi_/x570_」的能力。前綴若寫在執行期 .env，compose 會拿到空值而回退原始碼
# 預設（LLM_MODEL 掉回 14b、TS_IP 讓 ports: 綁錯而啟動失敗）—— 靜默劣化。
# 所以前綴活在被追蹤的總表，render 才挑列寫成不帶前綴的鍵。
#
# 安全規則（違反過的才寫下來）：
#   - 絕不在 stdout/stderr 印任何值；比對只用 sha256 前 12 碼＋長度。
#   - 在 `bash -x`（xtrace）下直接拒絕執行 —— 2026-09-26 三次外洩之一就是 bash -x。
#   - 解密只落 mktemp 暫存檔，trap 保證 shred；絕不寫固定路徑的明文。
set -euo pipefail

# xtrace 下拒絕：$- 含 x 表示 set -x 生效中（bash -x 也一樣）。
case "$-" in
  *x*) echo "env-sync: refuse to run under xtrace (bash -x leaks secrets)" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 測試覆寫點（預設行為不變）：tests/test_env_sync.py 用 fixture 目錄隔離執行。
ENV_DIR="${ENV_SYNC_DIR:-$ROOT/settings/env}"
DOTENV="${ENV_SYNC_ENV:-$ROOT/.env}"
TABLE="$ENV_DIR/hosts.shared.env"

# 必須三台一致的憑證（與 secrets.common.env.example 同步；改了一處要改另一處，
# tests/test_env_sync.py 會鎖）。per-host 鍵永遠不在此列。
# ⚠️ 這裡是「各機都該有同一個值」的清單，不是「本機自己的值」的清單。
# 判斷標準只有一個：**這個值有沒有跨機的讀寫關係？** 沒有就是 per-host。
# QDRANT_PEER_API_KEY 是唯一跨機的 qdrant 認證（sync-snapshot.sh 拉 x570 的快照）。
# 連 x570 的 pg 密碼應另設 POSTGRES_PEER_PASSWORD（照 *_PEER_* 慣例），
# 尚未納管 —— 值待 x570 查證（X570-HANDOFF.md 事項 2），在此之前不要加這個
# 沒人讀的幽靈鍵。
SHARED_SECRETS="QDRANT_PEER_API_KEY ADMIN_TOKEN CF_AIG_TOKEN HF_TOKEN NVIDIA_API_KEY TYPESAFE_API_KEY ZEN_API_KEY"
# per-host 機密（與 secrets.host.env.example 同步）：各機自己的值，**不分發**。
# 這份清單**只用於兩件事**：`--check` 的鍵覆蓋率、`--fingerprints` 的輸出標記。
# 絕不可把它們寫進 .env、絕不可加進 py_apply 的任何 layer ——
# 寫進去就變回「三台鎖步輪換」，而這正是 2026-09-27 要消除的成本。
# 2026-09-27 逐點 grep 查證：兩把的每個消費點都只指向自己那台 ——
#   QDRANT_API_KEY    compose.yaml:12（自己 qdrant 容器的 QDRANT__SERVICE__API_KEY）、
#                     compose.yaml:34 ＋ rag.py:331（api 打寫死的 QDRANT_URL: qdrant:6333）
#   POSTGRES_PASSWORD compose.yaml:24（自己的 pg 容器）、
#                     compose.yaml:36（DSN 預設值裡的 @postgres:5432，也是自己的）
# 舊分類錯在把 2026-09-26「本機 200／遠端 401」的根因誤認為「key 要三台同值」；
# 真正的根因是 backend/.env 與根 .env 兩份副本（已刪）。根因修掉後舊分類被留著
# 當保險，副作用是造出「mbp 的 qdrant key 還是第三把舊的」這個不存在的故障。
PER_HOST_SECRETS="QDRANT_API_KEY POSTGRES_PASSWORD"
# ⚠️ 這裡曾有一份 SHARED_CONFIG="COLLECTION EMBED_MODEL …" 列舉 common.env 的鍵，
#   **是死碼**：common.env 是整份套用的（cmd_pull 呼叫 py_apply file apply），
#   從來沒讀過 SHARED_CONFIG。留著最壞：有人加一個共用鍵去同步那份清單，
#   會得到「我改了但沒作用」的假結論。刪掉。刪除後唯一的真相是
#   settings/env/common.env 檔本身。
MANAGED_MARK="# --- managed by env-sync.sh (shared layers; do not edit below) ---"
# 機台清單刻意**不在這裡**：它住在 settings/env/hosts.shared.env 的 `HOSTS=` 那一行。
# 舊版這裡有一份 HOSTS="x570 mbp msi" 但整支 bash 從沒讀過它（死碼），
# 真正生效的是下面 py_apply 裡的 Python tuple。留著只會讓人以為改這裡有用。

need() { command -v "$1" >/dev/null 2>&1 || { echo "env-sync: missing tool: $1" >&2; exit 1; }; }

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'
usage: env-sync.sh <command> [options]
  pull                     解密共用憑證 → 合併 common.env → render per-host 值
  render [--host ID]       只做 per-host render（不需 sops）
  render --dry-run         只印「會動哪幾個鍵」，不寫檔、不印值
  --check                  鍵覆蓋率、總表 schema、per-host 漂移、版控衛生（不需 sops）
  --fingerprints [FILE]    7 把共用憑證（跨機比對用）＋2 把 per-host 機密的長度＋sha12
  --init-secrets [--force] 從本機 .env 抽出 7 把共用憑證建加密檔（只在第一台跑一次）
EOF
}

# 以指紋比對，不印值：輸出「鍵名 長度 sha12」。值缺失顯示缺失，不報錯。
# 鍵清單直接用 $SHARED_SECRETS／$PER_HOST_SECRETS —— 這裡曾經硬寫一份 9 個鍵
# 的名單，與 SHARED_SECRETS 是兩份真相；加第 10 把時只改到其中一份就會少印一把，
# 而「少印」看起來跟「那台沒設」一樣，正是 2026-09-26 診斷不出問題的那類。
#
# 兩段輸出的用途不同，不要混為一談：
#   共用 7 把 → 三台必須一致，橫向互比（不同就是有人沒 pull）
#   per-host 2 把 → 各機獨立存在，**不跨機比對**；只為「輪換前後在這台各跑一次，
#                   確認這台真的換掉了」。看到不同不代表故障。
fingerprints() {
  local f="${1:-$DOTENV}"
  [ -f "$f" ] || { echo "env-sync: no such file: $f" >&2; exit 1; }
  python3 - "$f" "$SHARED_SECRETS" "$PER_HOST_SECRETS" <<'PY'
import re, sys, hashlib
vals = {}
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
    if m:
        vals[m.group(1)] = m.group(2)


def fp(v):
    if v is None:
        return "MISSING"
    if v == "":
        return "EMPTY"
    return f"len={len(v):<4} sha12={hashlib.sha256(v.encode()).hexdigest()[:12]}"


shared, perhost = sys.argv[2].split(), sys.argv[3].split()
for k in shared:
    print(f"{k:<24} {fp(vals.get(k))}")
if perhost:
    print(f"# 以下 {len(perhost)} 把是 per-host 機密：各機獨立存在，**不跨機比對**"
          "（值不同不是故障）。用途只有輪換前後在這台各跑一次，確認這台真的換掉了。")
    for k in perhost:
        print(f"{k:<24} {fp(vals.get(k))}  [per-host；本機獨立存在，不跨機比對]")
PY
}

# 合併引擎（layer 建構＋展開＋寫檔全部在這一份 Python 裡，理由見檔末註）。
#   $1 mode   file | table
#   $2 action apply | plan | check
#   $3 source layer 檔（mode=file）或 hosts.shared.env（mode=table）
#   $4 host   mode=table 時要挑的機台
#   $5 expand 1 = 展開值裡的 ${VAR}；0 = 原樣（憑證層必須 0，見下）
#   $6 不得進本 layer 的 per-host 機密鍵（$PER_HOST_SECRETS）
# 過濾放在**合併引擎**裡而不是只靠「加密檔剛好沒有這兩把」：分類是規則，
# 規則必須由機器執行。否則誰把 QDRANT_API_KEY 手動加回加密檔，pull 就會
# 照樣分發到三台，而症狀要等下次輪換才浮現（別台的 key 被別台換掉）。
py_apply() {
  # 第 6 個參數＝本機 ollama 端點（WSL 自動偵測的結果）。刻意走**位置參數**
  # 而不是環境變數：`VAR=x func` 不會把 VAR export 給 python3 子行程，
  # 讀 os.environ 會拿到空字串（第一版就這樣，debug 才發現）。
  python3 - "$DOTENV" "$1" "$2" "$3" "$4" "$5" "$MANAGED_MARK" "$PER_HOST_SECRETS" "$6" <<'PY'
import os, re, sys

env_path, mode, action, source, host, expand, mark, perhost, detected = sys.argv[1:10]
expand = expand == "1"
FORBIDDEN = set(perhost.split())
# 機台清單來自總表裡的 `HOSTS=x570,mbp,msi` 那一行，不是寫死在程式裡。
# 舊版這裡是 `HOSTS = ("x570", "mbp", "msi")` 加上 `PREFIXED = ^(x570|mbp|msi)_(.+)$`：
# 第 4 台要加程式才能進來，等於「三台」是程式的前提。改成資料宣告後，
# 加機器＝改總表一行（會被 code review 看到），而嚴格性反而沒打折 ——
# 沒列在 HOSTS 裡的前綴照樣被 PREFIXED 擋掉（`mssi_OLLAMA_URL` 仍會報錯）。
HOSTS = []
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$")
PREFIXED = None
REF = re.compile(r"\$\{([A-Za-z_][A-Za-z_0-9]*)\}")


def read_kv(path):
    vals = {}
    for line in open(path, encoding="utf-8").read().splitlines():
        m = ASSIGN.match(line.strip())
        if m:
            vals[m.group(1)] = m.group(2)
    return vals


def die(msg):
    print("env-sync: " + msg, file=sys.stderr)
    sys.exit(1)


errs = []
layer = {}
if mode == "file":
    # 空值不合併：範本檔的值是空的，合進去等於清空本機真值。
    layer = {k: v for k, v in read_kv(source).items() if v != ""}
else:
    table = {}
    rows = read_kv(source)
    decl = rows.pop("HOSTS", "")
    HOSTS = [h.strip() for h in decl.split(",") if h.strip()]
    if not HOSTS:
        die(f"總表 {source} 缺少 `HOSTS=<機台,機台,…>` 宣告（機台清單是資料，不是程式）")
    PREFIXED = re.compile(r"^(" + "|".join(re.escape(h) for h in HOSTS) + r")_(.+)$")
    for k, v in rows.items():
        m = PREFIXED.match(k)
        if not m:
            errs.append(f"總表有非 <機台>_<鍵> 的行: {k}（前綴只允許 {'/'.join(HOSTS)}）")
            continue
        table.setdefault(m.group(2), {})[m.group(1)] = v
    # schema 完整性：每個鍵每台都要有列（值可空）。缺列＝漏改，寧可報錯。
    for base in sorted(table):
        lack = [h for h in HOSTS if h not in table[base]]
        if lack:
            errs.append(f"總表 {base}: 缺 {'/'.join(lack)} 的列")
    if host not in HOSTS:
        errs.append(f"主機代號不合法: {host or '(空)'}（須為 {'/'.join(HOSTS)}）")
    else:
        for base, cols in sorted(table.items()):
            v = cols.get(host, "")
            if v:                      # 空值＝該機沿用自己現值，不覆蓋
                layer[base] = v
    if errs:
        for e in errs:
            die(e)

# per-host 機密的防線：任何 layer（解密檔／common.env／總表）都不准帶這幾把。
# 分兩種處理，理由不同：
#   apply（pull／render）→ 剔除＋警告。報錯會讓「加密檔還留著舊鍵」這種
#     待清理狀態擋住所有 pull；剔除則保證 .env 裡該機自己的值一定原封不動，
#     這正是「另外兩台不需要做任何事」這個性質。
#   plan／check（render --dry-run、--check）→ 硬失敗。只剔除的話 --check 會在
#     「總表裡還藏著 per-host 機密」時回 0 —— 那正是本專案最恨的靜默劣化。
dropped = sorted(FORBIDDEN & set(layer))
for k in dropped:
    del layer[k]
if dropped:
    if action in ("plan", "check"):
        die("per-host 機密不得出現在分發 layer: " + " ".join(dropped)
            + "（真值只留該機 .env；請從該 layer 移除）")
    # 只報鍵名，不報值
    print("env-sync: 略過 per-host 機密（不得進分發 layer，真值只留該機 .env）: "
          + " ".join(dropped), file=sys.stderr)

cur = read_kv(env_path) if os.path.exists(env_path) else {}

# 配對規則：某些鍵的語意是「與另一鍵位置對齊」，長度不一致會靜默配錯
# （rag.py:215 用 index i 取 OLLAMA_URLS[i] 對應的模型）。所以比較的是
# 「生效值」＝總表有值用總表、否則沿用 .env 現值，才不會漏掉半填的情況。
PAIRED = {"OLLAMA_MODELS": "OLLAMA_URLS"}


def eff(key):
    return layer.get(key) or cur.get(key) or ""


def count(s):
    return len([x for x in s.split(",") if x.strip()])

if expand:
    for k, v in layer.items():
        for ref in sorted(set(REF.findall(v))):
            if not cur.get(ref):
                # 寫出空密碼的 DSN 比不寫更糟：連線會拿去對空密碼，症狀是
                # 「看起來有設定但就是連不上」。只報變數名，不報值。
                die(f"{k} 引用的 {ref} 在 .env 不存在或為空，拒絕 render")
        layer[k] = REF.sub(lambda m: cur.get(m.group(1), ""), v)

# 自動偵測本機 ollama 位址（僅 WSL；其他平台回空＝不注入）。
#
# 為什麼要有這一步：OLLAMA_URLS 在 WSL 上必須是 Windows 主機的閘道 IP，而那個
# IP 由 Windows 分配、重啟會變。總表刻意留空（因為「IP 不進被追蹤的表」——
# 見 hosts.shared.env 的說明），改由 render 依當下實際路由填入，WSL 換網段
# 後重跑一次 render 就跟上。
#
# 兩個變數都注入，值相同：
#   OLLAMA_URLS — 容器內 gateway.py 讀（容器經 WSL 轉發到同一個 IP）
#   OLLAMA      — host 端 ingest/laws/qdrant_load.py 讀
# 兩者視角不同但位址相同時最省事；真正需要分開設定的是 Docker Desktop 那種
# 容器直連 Windows 的情況（屆時 OLLAMA_URLS 該用 host.docker.internal）。
_inject = detected or ""
# 只在**真實 repo** 上注入。判斷依據與 cmd_check 的「版控衛生」檢查同一個：
# ENV_SYNC_DIR 被覆寫 = 測試 fixture（tests/test_env_sync.py 用 tmp 目錄），
# 那裡的總表是假的，注入只會干擾它斷言的行為。
_real_repo = not os.environ.get("ENV_SYNC_DIR")
if _inject and _real_repo and mode == "table":
    # 有多台候選時（總表已列其他 peer），注入的本機要**附加**在前面而不是取代
    # 整個清單 —— 否則長度會變，底下的 PAIRED 檢查（OLLAMA_MODELS 位置對應）
    # 會誤報。把本機放第一位也是刻意的：開發機不該被別台的可用性綁住。
    #
    # 刻意**不**動 OLLAMA_MODELS：本機 ollama 有哪些模型是該機的事（要知道主機
    # 上真的裝了什麼），env-sync 推導不出來。總表若列了 N 台對應 N 個模型，
    # 注入本機後 URL 變 N+1、模型仍是 N —— 這時 PAIRED 檢查會擋下來，那正是
    # 想要的：它在提醒「新增了一台 ollama 候選，請把模型清單也補一項」。
    # 想自動對齊就在該機 .env 手動補，別讓工具猜 —— 猜錯的症狀是
    # 「8b 機器被餵 14b 而 OOM」，比報錯更難查。
    parts = [p.strip() for p in layer.get("OLLAMA_URLS", "").split(",") if p.strip()]
    # 先移除同一個位址的舊項，重跑 render 不該疊加。
    parts = [p for p in parts
             if p.split("=", 1)[-1].rstrip("/") != _inject.rstrip("/")]
    parts.insert(0, f"msi={_inject}")
    layer["OLLAMA_URLS"] = ",".join(parts)
    layer["OLLAMA"] = _inject

if not layer:
    print("env-sync: 沒有要合併的鍵（總表該機的列皆為空＝沿用現值）"
          if mode == "table" else "env-sync: 沒有要合併的鍵")
    sys.exit(0)

for a, b in PAIRED.items():
    if eff(a) and eff(b) and count(eff(a)) != count(eff(b)):
        die(f"{a} 有 {count(eff(a))} 項但 {b} 有 {count(eff(b))} 項 —— "
            f"兩者位置對應，長度必須相同（rag.py 依 index 取值）")

if action in ("plan", "check"):
    drift = []
    for k in sorted(layer):
        have = cur.get(k)
        if have == layer[k]:
            continue
        drift.append(k)
        if action == "plan":
            tag = "會新增" if have is None else ("會覆寫" if have != "" else "會填入空值")
            print(f"  {k:<22} {tag}")
    if action == "plan":
        print(f"env-sync: dry-run，{len(layer)} 個鍵在表內、{len(drift)} 個與 .env 不同"
              f"（值不顯示）")
        sys.exit(0)
    if drift:
        print("env-sync --check: .env 與總表不一致的鍵: " + " ".join(drift), file=sys.stderr)
        print("              跑 `env-sync.sh render` 讓總表成為真相（或確認該機真的該不同）",
              file=sys.stderr)
        sys.exit(1)
    print(f"env-sync --check: per-host 值與總表一致（{len(layer)} 鍵，該機 "
          f"{host or '?'}）")
    sys.exit(0)

lines = open(env_path, encoding="utf-8").read().splitlines() if os.path.exists(env_path) else []
have, out = set(), []
for line in lines:
    m = ASSIGN.match(line.strip())
    if m and m.group(1) in layer:
        out.append(f"{m.group(1)}={layer[m.group(1)]}")
        have.add(m.group(1))
    else:
        out.append(line)
missing = [k for k in layer if k not in have]
if missing:
    if mark not in out:
        out.append(mark)
    for k in missing:
        out.append(f"{k}={layer[k]}")
open(env_path, "w", encoding="utf-8").write("\n".join(out) + "\n")
print(f"env-sync: 合併 {len(layer)} 鍵進 {os.path.basename(env_path)}"
      f"（缺鍵 {len(missing)} 個附加於 managed 區）")
PY
}

# 挑本機 HOST_ID：render 的選擇器。刻意不放進總表（要先知道本機是誰才挑得到列）。
local_host() {
  local v=""
  [ -f "$DOTENV" ] && v="$(grep -m1 -E '^HOST_ID=' "$DOTENV" 2>/dev/null | cut -d= -f2- || true)"
  printf '%s' "$v"
}

# 偵測「本機 ollama 的可達位址」，回空字串代表不該注入。
#
# 為什麼需要（2026-09-29 實測）：WSL NAT 模式下，Windows 主機的 ollama 從 WSL
# 看到的位址是**預設閘道**（`ip route` 的 default gw，NAT 網段，形如 172.24.x.1），而那個
# IP 由 Windows 分配，每次 WSL 重啟可能變。寫進被追蹤的總表就會變成陷阱
# （過期值 → 查詢全掛，症狀是 httpx.ConnectError: ollama unreachable）。
# 所以這裡刻意不放常數 —— tests/test_detect_host_endpoint.py 會擋下任何
# 硬寫的 IP。
#
# 為什麼不用 host.docker.internal：那是 Docker Desktop 的慣用名。WSL **原生**
# docker 把它解析到 WSL 自己在 docker0 上的位址（不是 Windows），而 ollama 跑在
# Windows、在 WSL 網段外 → 容器連不到。實測踩過：logs 顯示
# `httpx.ConnectError: ollama unreachable`。
#
# 三種情況：
#   ① WSL 且有閘道 → 注入閘道位址（實測 WSL 直連與容器→host.docker.internal
#      之外的路徑都通；容器共用同一個 IP 亦可，因為它經 WSL 轉發）
#   ② 非 WSL（x570 原生 Linux / mbp macOS）→ 不注入，「本機」＝localhost，
#      總表空值＝沿用現值即可
#   ③ 偵測不到 → 不注入。寧可留空，也不要猜一個錯的位址。
detect_host_endpoint() {
  if [ -z "${WSL_DISTRO_NAME:-}" ] && ! grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
    printf '%s' ""
    return 0
  fi
  local gw=""
  gw="$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
  case "$gw" in
    ''|*[!0-9.]*|127.*) printf '%s' ""; return 0 ;;
  esac
  printf 'http://%s:11434' "$gw"
}

cmd_render() {
  local host="" action=apply
  while [ $# -gt 0 ]; do
    case "$1" in
      --host) host="${2:-}"; shift 2 ;;
      --dry-run) action=plan; shift ;;
      *) echo "env-sync render: unknown arg: $1" >&2; exit 2 ;;
    esac
  done
  [ -f "$TABLE" ] || { echo "env-sync render: 找不到 $TABLE" >&2; exit 1; }
  [ -n "$host" ] || host="$(local_host)"
  if [ -z "$host" ]; then
    echo "env-sync render: .env 沒有 HOST_ID，無法知道要挑哪一列。" >&2
    echo "                請先在 .env 設 HOST_ID（x570/mbp/msi），或用 --host 指定。" >&2
    exit 1
  fi
  # expand=1：總表裡 ${POSTGRES_PASSWORD} 這類佔位要在 render 時注入真值。
  #
  # RAGDEMO_DETECT_ENDPOINT：WSL 上自動偵測 Windows 主機的 ollama 位址。
  # 總表刻意留空（IP 會變，不進被追蹤的表），改由 render 依當下路由填。
  #
  # **傳入值優先於偵測值**：這個變數同時是「注入通道」與「偵測結果的輸出」，
  # 外部傳進來就代表「我要用這個位址」（測試靠它餵假位址驗證注入路徑，
  # 真的話也能手動指定跳過偵測）。只有沒傳時才用偵測結果。
  # 傳入值優先於偵測值：這個變數同時是「注入通道」與「偵測結果的輸出」，
  # 外部傳進來就代表「我要用這個位址」。只有沒傳時才用偵測結果。
  local ep="${RAGDEMO_DETECT_ENDPOINT:-}"
  [ -n "$ep" ] || ep="$(detect_host_endpoint)"
  py_apply table "$action" "$TABLE" "$host" 1 "$ep"
  [ "$action" = apply ] && chmod 600 "$DOTENV"
  return 0
}

cmd_pull() {
  need sops
  local enc="$ENV_DIR/secrets.common.enc.env"
  [ -f "$enc" ] || { echo "env-sync: $enc not found; nothing to pull yet" >&2; exit 1; }
  local tmp
  tmp="$(mktemp "$ENV_DIR/.decrypted.XXXXXX")"
  # trap 字串用雙引號立即展開：tmp 是函式 local，EXIT trap 在函式返回後才跑，
  # 到時 local 已出作用域，set -u 下會報 unbound（2026-09-27 實測）。
  # shred 不在 macOS，需 rm 回退。
  trap "shred -u \"$tmp\" 2>/dev/null || rm -f \"$tmp\"" EXIT
  SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}" \
    sops --decrypt --output "$tmp" "$enc"
  chmod 600 "$tmp"
  # 憑證層 expand=0：密碼裡若真的含 ${...} 字面，展開會把它改掉。
  # 這裡的 ${VAR} 只在「值是我們寫的設定」時才該展開。
  py_apply file apply "$tmp" "" 0 ""   # pull 不注入本機端點（那是 render 的事）
  py_apply file apply "$ENV_DIR/common.env" "" 1 ""
  trap - EXIT
  shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  cmd_render
  chmod 600 "$DOTENV"
}

cmd_check() {
  # 不需 sops：只核對鍵名覆蓋率、總表 schema、per-host 漂移與版控衛生。值一律不印。
  [ -f "$DOTENV" ] || { echo "env-sync --check: MISSING .env" >&2; exit 1; }
  # 鍵覆蓋率要把三層都算進去：共用憑證範本、per-host 機密範本、共用非敏感。
  # 漏算 per-host 機密層＝「該機根本沒有這兩個鍵」沒人管，而症狀是本機
  # qdrant/pg 認證失敗（401／心跳失敗），極難回推到是環境變數缺了。
  python3 - "$DOTENV" "$ENV_DIR/secrets.common.env.example" \
      "$ENV_DIR/secrets.host.env.example" "$ENV_DIR/common.env" <<'PY'
import os, re, sys
def keys(p):
    out = set()
    if not os.path.exists(p):     # 範本檔缺了就當空集；存在性另有專門檢查
        return out
    for line in open(p, encoding="utf-8").read().splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
        if m:
            out.add(m.group(1))
    return out
env, sec, host, com = (keys(a) for a in sys.argv[1:5])
missing = sorted((sec | host | com) - env)
if missing:
    print("env-sync --check: .env 缺少鍵: " + " ".join(missing))
    sys.exit(1)
print(f"env-sync --check: key coverage ok ({len(env)} keys in .env)")
PY
  local fail=$?
  # per-host 總表：schema ＋ 與 .env 的一致性（同時驗證 ${VAR} 都解析得到）。
  if ! cmd_render_check; then
    fail=1
  fi
  # 以下只在真實 repo 有意義（fixture 目錄不在 git 裡）。
  if [ "$ENV_DIR" = "$ROOT/settings/env" ]; then
    # 明文暫存檔不得殘留。
    if ls "$ENV_DIR"/.decrypted.* "$ENV_DIR"/.init-secrets.* 2>/dev/null | grep -q .; then
      echo "env-sync --check: leftover decrypted tmp in settings/env/" >&2
      fail=1
    fi
    # 單一真相：總表與加密檔必須被追蹤，且不得另立 per-host 明文真值檔。
    if ! git -C "$ROOT" ls-files --error-unmatch settings/env/hosts.shared.env >/dev/null 2>&1; then
      echo "env-sync --check: settings/env/hosts.shared.env 未被 git 追蹤" >&2
      fail=1
    fi
    # per-host 機密的鍵名宣告檔必須被追蹤（--check 的覆蓋率要靠它）。
    if ! git -C "$ROOT" ls-files --error-unmatch \
         settings/env/secrets.host.env.example >/dev/null 2>&1; then
      echo "env-sync --check: settings/env/secrets.host.env.example 未被 git 追蹤" >&2
      fail=1
    fi
    # per-host 機密的**明文**檔永不進版控（值只留各機 .env）。
    if git -C "$ROOT" ls-files --error-unmatch \
         settings/env/secrets.host.env >/dev/null 2>&1; then
      echo "env-sync --check: settings/env/secrets.host.env 是明文 per-host 機密，不得進版控" >&2
      fail=1
    fi
    # 不可變量（不解密就驗得到）：per-host 機密不得出現在共用加密檔裡。
    # sops 的 dotenv 輸出格式讓**鍵名保持明文**、只有值是 ENC[...]，
    # 所以這條檢查不需要 age 私鑰，CI 沒有 .env 也能跑。
    local ph_in_enc=""
    if [ -f "$ENV_DIR/secrets.common.enc.env" ]; then
      ph_in_enc="$(sed -E 's/=.*$//' "$ENV_DIR/secrets.common.enc.env" \
                   | grep -E "^($(echo "$PER_HOST_SECRETS" | tr ' ' '|'))$" || true)"
    fi
    if [ -n "$ph_in_enc" ]; then
      echo "env-sync --check: per-host 機密不得進共用加密檔（會變回三台鎖步輪換）:" >&2
      echo "$ph_in_enc" | sed 's/^/    /' >&2
      fail=1
    fi
    # 白名單：README.md、*.env.example、common.env、hosts.shared.env、*.enc.env
    local extra
    extra="$(git -C "$ROOT" ls-files settings/env/ \
      | grep -vE '(\.md|\.env\.example|common\.env|hosts\.shared\.env|\.enc\.env)$' || true)"
    if [ -n "$extra" ]; then
      echo "env-sync --check: settings/env 下有非白名單的追蹤檔（per-host 真值只能放總表）:" >&2
      echo "$extra" | sed 's/^/    /' >&2
      fail=1
    fi
  fi
  return $fail
}

cmd_render_check() {
  local host
  host="$(local_host)"
  if [ -z "$host" ]; then
    echo "env-sync --check: .env 沒有 HOST_ID（render 的選擇器）" >&2
    return 1
  fi
  py_apply table check "$TABLE" "$host" 1 ""   # check 不注入（--check 不可改 .env）
}

cmd_init_secrets() {
  # 從本機 .env 抽出 SHARED_SECRETS 的真值，加密成 secrets.common.enc.env。
  # 只在「第一台」（MSI）跑一次；之後輪換直接 sops 解密改值。
  need sops
  local enc="$ENV_DIR/secrets.common.enc.env" force=0
  [ "${1:-}" = "--force" ] && force=1
  if [ -f "$enc" ] && [ "$force" -ne 1 ]; then
    echo "env-sync: $enc exists; refusing without --force" >&2; exit 1
  fi
  [ -f "$DOTENV" ] || { echo "env-sync: no .env to extract from" >&2; exit 1; }
  local tmp
  tmp="$(mktemp "$ENV_DIR/.init-secrets.XXXXXX")"
  trap "shred -u \"$tmp\" 2>/dev/null || rm -f \"$tmp\"" EXIT
  python3 - "$DOTENV" "$tmp" "$SHARED_SECRETS" <<'PY'
import re, sys
vals = {}
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
    if m:
        vals[m.group(1)] = m.group(2)
want = sys.argv[3].split()
# 缺鍵（整行不存在）是錯誤；空值放行 —— 空＝「未設定」
# （例：MSI 的 ZEN_API_KEY 目前是空的）。合併端本來就會跳過空值，
# 所以空值進加密檔不會清空別台的真值；只報鍵名，不印值。
missing = [k for k in want if k not in vals]
if missing:
    print("env-sync --init-secrets: .env 缺少鍵: " + " ".join(missing), file=sys.stderr)
    sys.exit(1)
empty = [k for k in want if vals[k] == ""]
with open(sys.argv[2], "w", encoding="utf-8") as f:
    for k in want:
        f.write(f"{k}={vals[k]}\n")
if empty:
    print("env-sync --init-secrets: 以下鍵為空（未設定，仍會寫入以保 schema 齊全）: "
          + " ".join(empty))
print(f"env-sync --init-secrets: extracted {len(want)} keys")
PY
  # --filename-override：sops 按「輸入路徑」配 .sops.yaml 的 creation_rules，
  # tmp 路徑配不上任何規則會報 no matching creation rules（2026-09-27 實測），
  # 故宣告這次加密的邏輯檔名。必須是 repo 根相對路徑且與 .sops.yaml 的
  # path_regex 一致，否則換了檔名規則就靜默失效。
  (cd "$ROOT" && sops --encrypt \
    --filename-override "settings/env/secrets.common.enc.env" \
    --output "$enc" "$tmp")
  trap - EXIT
  shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  echo "env-sync: wrote $enc (track it with git)"
}

# 合併引擎只有一份 Python 的理由：2026-09-27 之前 merge_layer 與後來新增的
# render 各寫一份合併邏輯；兩份都對但只要一份改了就是無聲漂移。收斂後
# 「空值不合併／行保留／缺鍵附加在 managed 區」只有一個實作處。
case "${1:-pull}" in
  pull) shift; cmd_pull "$@" ;;
  render) shift; cmd_render "$@" ;;
  --check) cmd_check ;;
  --fingerprints) fingerprints "${2:-$DOTENV}" ;;
  --init-secrets) cmd_init_secrets "${2:-}" ;;
  -h|--help|help) usage ;;
  *) echo "env-sync: unknown arg: $1" >&2; usage >&2; exit 2 ;;
esac
