#!/usr/bin/env bash
# env-sync — 三機共用設定與憑證的分發、合併、稽核（sops + age）。
#
# 解決的問題：8 把共用憑證必須三台一致，過去靠人工複製，2026-09-26 已造成
# 兩次不對稱 401／心跳失敗。另有 2 把是 per-host 機密，**刻意不**走這條路。
#
# 分層（詳見 settings/env/README.md），合併順序由低到高：
#   settings/env/common.env              追蹤明文，共用非敏感鍵
#   settings/env/secrets.common.enc.env  追蹤加密，8 把共用憑證真值
#   settings/env/hosts.shared.env        追蹤明文，三台 per-host 值（前綴 <機台>_<鍵>）
#   .env（repo 根）                      不追蹤、chmod 600，執行期唯一真相
#
# ⚠️ QDRANT_API_KEY / POSTGRES_PASSWORD 刻意**不在**下面任何一個 layer：
#   它們是各機自己容器的認證，沒有跨機讀寫關係。真值只留該機 .env。
#
# 為什麼前綴不直接放進 .env：compose 只認 ${VAR}，沒有「依 HOST_ID 動態選
# wsl_/x570_」的能力。前綴若寫在執行期 .env，compose 會拿到空值而回退原始碼
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
# QDRANT_PEER_API_KEY 是唯一跨機的 qdrant 認證（sync-snapshot.sh 拉來源機的快照）。
#
# 2026-09-30：移出 ZEN_API_KEY（7 把 → 6 把）。它不符合「共用」的定義之外的任何
# 理由 —— 實測 `secrets.common.enc.env` 裡它是**空值**（其餘 6 把都有 ENC[...]），
# 而 --init-secrets 是從第一台機器的 .env 抽值建檔的，空值代表**沒有任何一台設過它**；
# wsl/mbp 實測 `zen_ready: false`、usage snapshot 無 zen 記錄。分發一個沒人設的
# 空值只會讓每台 .env 都被塞一行永遠不會變的 `ZEN_API_KEY=`。
# ⚠️ 移出本清單**不等於** backend 不能讀它：`rag.py` 仍讀 `os.getenv("ZEN_API_KEY")`，
#   值留空就讓 `_zen_complete` 回 false（前端已優雅降級）。真要復活就重新申請 key
#   並加回本行 —— 不要因為「程式還在讀」就把它留在共用層，那正是幽靈鍵的來源。
# 2026-09-30：曾為連 x570 的 pg 而保留 POSTGRES_PEER_PASSWORD 的位置，但它
# **沒有任何程式讀取** —— 全 repo 只剩註解、.example 說明文字與測試 docstring。
# 當初要它，是因為三台 /hosts 指向同一個 pg（x570 的），密碼未知會互相鎖死；
# 2026-09-30 實測各台 /hosts 已改讀自己的 pg，鎖死不存在，該鍵無用。
# 所以：**不要加這個幽靈鍵**，未來若真的需要跨機讀 pg 再重新設計。
#
# 2026-10-02：納入 CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET（6 把 → 8 把）。
# 這是 Access 護三台之後**漏掉的分類**，不是新設計。判斷標準（README §7）：
# 「這個值有沒有跨機的讀寫關係？」—— 有，且與 ADMIN_TOKEN 完全同類：
# 三台拿**同一組** token 去跟**同一個外部服務**（Cloudflare Access）認證。
# 四個消費點分屬三台，所以三台少一台有值症狀極安靜：
#   compose.yaml:143-144         傳進 api 容器
#   backend/app/gateway.py:133   peer 探測帶 token（Access 護了三台後的必修補，4a0bf52）
#   scripts/sync-snapshot.sh:171 拉 law version
#   frontend/…/+server.ts:85     Pages worker（那組值在 Pages 後台，不靠這條分發）
#
# ⚠️ 它**不在** sops 層的期間（2026-10-01 → 10-02）造成的實際損害：
#   1. mbp/x570 只能人手貼 39 字元 hex ＋ 54 字元 `cfast_`，而「貼反」的症狀
#      與「Access 沒開」完全一樣（都回 403），X570-HANDOFF.md 為此專門記了坑。
#      納管後 `pull` 直接寫入，那個坑整類消失。
#   2. 輪換要動三個 .env，中間必然有一段 peer 探測全紅。
#   3. `--fingerprints` 不涵蓋它 → 兩台值不一致是**無聲**的。這正是
#      2026-09-26「診斷不出問題」那類缺口，所以納入清單即自動被指紋覆蓋。
SHARED_SECRETS="QDRANT_PEER_API_KEY ADMIN_TOKEN CF_AIG_TOKEN HF_TOKEN NVIDIA_API_KEY TYPESAFE_API_KEY CF_ACCESS_CLIENT_ID CF_ACCESS_CLIENT_SECRET"
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
# 舊版這裡有一份 HOSTS="x570 mbp msi"（2026-10-01 改名前）但整支 bash 從沒讀過它（死碼），
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
  --hosts-table            三台 per-host 值並排的表格（讀總表，不需 sops、不需 .env）
  --fingerprints [FILE]    8 把共用憑證（跨機比對用）＋2 把 per-host 機密的長度＋sha12
  --init-secrets [--force] 從本機 .env 抽出 8 把共用憑證建加密檔（只在第一台跑一次）
EOF
}

# 以指紋比對，不印值：輸出「鍵名 長度 sha12」。值缺失顯示缺失，不報錯。
# 鍵清單直接用 $SHARED_SECRETS／$PER_HOST_SECRETS —— 這裡曾經硬寫一份 9 個鍵
# 的名單，與 SHARED_SECRETS 是兩份真相；加第 10 把時只改到其中一份就會少印一把，
# 而「少印」看起來跟「那台沒設」一樣，正是 2026-09-26 診斷不出問題的那類。
#
# 兩段輸出的用途不同，不要混為一談：
#   共用 8 把 → 三台必須一致，橫向互比（不同就是有人沒 pull）
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
  python3 - "$DOTENV" "$1" "$2" "$3" "$4" "$5" "$MANAGED_MARK" "$PER_HOST_SECRETS" <<'PY'
import os, re, sys

env_path, mode, action, source, host, expand, mark, perhost = sys.argv[1:9]
expand = expand == "1"
FORBIDDEN = set(perhost.split())
# 機台清單來自總表裡的 `HOSTS=x570,mbp,wsl` 那一行，不是寫死在程式裡。
# 舊版這裡是 `HOSTS = ("x570", "mbp", "msi")`（改名前）加上 `PREFIXED = ^(x570|mbp|msi)_(.+)$`：
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

# 2026-10-01：這裡原本有一段「WSL 上自動偵測 Windows 主機 ollama 位址」的注入，
# 連帶 detect_host_endpoint() 函式與 tests/test_detect_host_endpoint.py。
# 整組刪除的理由：WSL 現在自己就有 tailscale（hostname `wsl`），Windows 主機的
# ollama 直接走 MagicDNS 就能連，不必再繞「WSL NAT 預設閘道」那個會變的位址。
# 死掉的機制比沒有機制更糟 —— 它會讓人以為 OLLAMA_URLS 有人負責。
# 對應的真相搬到 hosts.shared.env 的 OLLAMA_URLS 與 OLLAMA 兩列（三台同一套規則）。

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
    # ⚠️ 2026-10-02：把「空列 → 沿用現值」這個狀態**印出來**。
    #
    # 那個語意是刻意的（總表可以宣告「這台自己決定」），但它有個看不見的後果：
    # `layer` 只收有值的列 → **表裡是空列的 per-host 鍵，`--check` 根本不比較**。
    # 於是「該機有值、總表沒人管」這種漂移在 `--check` 下是**全綠**的。
    #
    # 實測踩到（2026-10-02 三機比對）：mbp 的 `OLLAMA_URLS` 有值，而總表的
    # `mbp_OLLAMA_URLS` 是空的 → 層級裡沒這個鍵 → `--check` 綠 → 沒有人知道
    # 那個值是手改的還是 render 後總表被清掉留下的殘留。
    #
    # 這裡**不**把它變成錯誤：那會擋住「這台刻意自己決定」的合法用法，
    # 而那個用法是這段程式碼的原設計意圖。只報出來 ——
    # 不可見的狀態不會被處理，可見的至少會被問一次。
    if action == "check":
        inherit = sorted(k for k, cols in table.items()
                         if not cols.get(host, "") and cur.get(k))
        if inherit:
            print(f"env-sync --check: ⚠️ {len(inherit)} 個 per-host 鍵在總表的 "
                  f"{host or '?'} 列是空的，該機沿用自己的值（設計如此，但值不在"
                  f"總表裡 → 改總表不會影響它 → 沒有任何人在管它）: "
                  f"{' '.join(inherit)}", file=sys.stderr)
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

# 從總表的 `HOSTS=` 那一行讀機台清單，供錯誤訊息使用。
# 為什麼不寫死：2026-09-27 已經把「機台清單」從程式搬進資料（那是 py_apply 裡
# HOSTS / PREFIXED 的改動）。這裡若留一份寫死的複本，就等於把鎖死複製一份到
# 訊息字串裡 —— 2026-10-01 `msi` 改名的時候，寫死的那份會開始說謊。
# 讀不到就回空字串：訊息少給建議，但**不會把錯誤的主機名寫進去**。
table_hosts() {
  [ -f "$TABLE" ] || { printf '%s' ""; return 0; }
  grep -m1 -E '^HOSTS=' "$TABLE" 2>/dev/null | cut -d= -f2- | tr ',' '/' || true
}

# ⚠️ 2026-10-01：這裡原本有 detect_host_endpoint() —— 抓 WSL NAT 的預設閘道
# （`ip route` 的 default gw，形如 172.24.x.1）當作 Windows 主機 ollama 的位址。
# 已刪除，因為 WSL 現在自己裝了 tailscale，ollama 走 MagicDNS（`msi.` 的 FQDN）
# 就通，不必依賴那個 Windows 分配、重啟會變的位址。三台因此是同一套規則。
#
# ⚠️ 順帶記一個坑，不要以為「短名在容器裡通、就一定在 host 端通」：
#   WSL 自動產生 /etc/hosts 帶一行 `127.0.1.1 msi.localdomain msi`，而
#   glibc 是 `hosts: files dns`（檔案先贏）→ MagicDNS 的 `msi` 被蓋成 127.0.1.1。
#   實測（2026-10-01）：容器 curl http://msi:11434 → 200，
#   WSL host curl http://msi:11434 → connection refused（127.0.1.1）。
#   所以 hosts.shared.env 用的是 FQDN。

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
    echo "                請先在 .env 設 HOST_ID（$(table_hosts)），或用 --host 指定。" >&2
    exit 1
  fi
  # expand=1：總表裡 ${POSTGRES_PASSWORD} 這類佔位要在 render 時注入真值。
  py_apply table "$action" "$TABLE" "$host" 1
  # 把 .env 裡的區段標題佔位換成實際機台代號：
  #     # ══ HOST: <本機 HOST_ID> ══  →  # ══ HOST: wsl ══
  #
  # 為什麼佔位在 .env.example、而這裡才填：範本是**同一份檔三台共用**的
  # （追蹤、無憑證），寫死 `HOST: wsl` 會讓另外兩台的 .env 帶著別人的代號。
  # 而 .env 是 per-machine 的，所以由知道 host 是誰的這裡填。
  #
  # 這就是「per-host 用區段標題識別、而不用前綴」的折衷：識別力等同前綴，
  # 但鍵名不變 → compose 的 ${VAR} 契約不動（見 settings/env/README.md §9：
  # 前綴若寫在 .env，LLM_MODEL 會掉回原始碼預設、TS_IP 會讓 docker 啟動失敗）。
  if [ "$action" = apply ]; then
    python3 - "$DOTENV" "$host" <<'PY'
import re, sys
path, host = sys.argv[1], sys.argv[2]
try:
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
except FileNotFoundError:
    sys.exit(0)
n = 0
for i, l in enumerate(lines):
    if "HOST: <本機 HOST_ID>" in l:
        lines[i] = l.replace("<本機 HOST_ID>", host); n += 1
if n:
    open(path, "w", encoding="utf-8").write("".join(lines))
# 沒找到不算錯：.env 可能是舊格式（還沒遷到新版面），那是遷移前的正常狀態。
# 刻意不 exit 1 —— 否則三台升級順序會變成「必須先改範本才能 pull」。
PY
    chmod 600 "$DOTENV"
  fi
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
  py_apply file apply "$tmp" "" 0
  py_apply file apply "$ENV_DIR/common.env" "" 1
  trap - EXIT
  shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  cmd_render
  chmod 600 "$DOTENV"
}

# 三欄並排的 per-host 視圖（<機台>_<鍵>=<值> 對齊成表格）。
#
# 為什麼需要：`.env` 裡的 per-host 鍵**不能**加前綴（compose 只認 ${VAR}，
# 見 settings/env/README.md §9）。所以「哪個鍵屬哪台」在執行期檔案裡靠
# 區段標題辨識，而要看三台並排比較就得回來看這張總表 —— 它本來就有前綴，
# 但是一行一個 `x570_TS_IP=` 形式，不適合人眼橫向比較。這支把它排成表格。
#
# 值可以直接印：總表是**追蹤檔且不含任何憑證**（該檔自己的規則），
# `${VAR}` 佔位也會原樣顯示（不展開 —— 展開需要該機 .env，會把別台的
# 密碼混進來）。
cmd_hosts_table() {
  [ -f "$TABLE" ] || { echo "env-sync: 找不到 $TABLE" >&2; exit 1; }
  python3 - "$TABLE" <<'PY'
import re, sys
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$")
rows, hosts = {}, []
for raw in open(sys.argv[1], encoding="utf-8"):
    if raw.lstrip().startswith("#"):
        continue
    m = ASSIGN.match(raw.rstrip("\n"))
    if not m:
        continue
    k, v = m.group(1), m.group(2)
    if k == "HOSTS":
        hosts = [h.strip() for h in v.split(",") if h.strip()]
        continue
    pref = k.split("_", 1)[0]
    if pref in hosts:
        rows.setdefault(k.split("_", 1)[1], {})[pref] = v
if not hosts:
    sys.exit("總表缺少 HOSTS= 宣告")
# 欄寬：至少容得下機台名，但**有上限**。第一版沒有上限，於是
# HOST_API_URLS（97 字元）把整張表撐成一行 —— 而那正是最需要橫向比較的
# 一列（「三台的 peer 清單是不是同一份」），撐爆等於看不到。
MAXW = 40


def dw(t: str) -> int:
    """顯示寬度：中文字元佔兩格。否則中文欄位會比英文欄位短。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in t)


def cell(v: str, width: int) -> str:
    if dw(v) > width:
        out = ""
        for c in v:
            if dw(out + c) > width - 1:
                break
            out += c
        v = out + "…"
    pad = width - dw(v)
    return v + " " * max(0, pad)


w = max([dw(h) for h in hosts] + [dw(k) for k in rows])
cols = [max(dw(h), 12) for h in hosts]
print(cell("鍵", w) + "  " + "  ".join(cell(h, c) for h, c in zip(hosts, cols)))
print("─" * (w + 2 + sum(c + 2 for c in cols)))
for k in sorted(rows):
    out = [cell(k, w)]
    for h, c in zip(hosts, cols):
        v = rows[k].get(h)
        if v is None:
            v = "（缺列）"
        elif v == "":
            v = "（空）"
        out.append(cell(v, c))
    print("  ".join(out))
print()
print("「（空）」不是待辦 —— 語意是「該機沿用自己 .env 現值」。")
print("「…」是截斷（欄寬上限）；要看完整值直接讀 settings/env/hosts.shared.env。")
print("總表不得含憑證值；需要內嵌密碼時寫 ${VAR} 佔位由 render 展開。")
PY
}

cmd_check() {
  # 不需 sops：只核對鍵名覆蓋率、總表 schema、per-host 漂移與版控衛生。值一律不印。
  [ -f "$DOTENV" ] || { echo "env-sync --check: MISSING .env" >&2; exit 1; }
  # 版面指紋：鍵名序列 ＋ 區段標題的 sha12。**不含任何值。**
  #
  # 為什麼需要：三台的 .env 在不同機器上，不能直接 diff；而要驗「三台格式
  # 一致」又不能把 .env 拿去做版本控制或貼進聊天（裡面有 8 份憑證）。
  # 指紋是唯一能在不曝露任何值的前提下跨機比對「結構」的方法。
  #
  # ⚠️ 這只證明**結構**一致（鍵集合、順序、區塊劃分），不證明值一致 ——
  #    值的一致性是 --fingerprints 的工作。兩者不可互相取代。
  local layout_fp
  layout_fp="$(python3 - "$DOTENV" <<'PY'
import hashlib, re, sys
seq = []
for line in open(sys.argv[1], encoding="utf-8"):
    m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=", line)
    if m:
        seq.append(m.group(1))
    elif line.startswith("# ══ 共用") or line.startswith("# ══ HOST"):
        seq.append(line.rstrip())
print(f"{len(seq)} keys, layout sha12="
      f"{hashlib.sha256(chr(10).join(seq).encode()).hexdigest()[:12]}")
PY
)"
  # 指後面要指明「這不是值的一致性」—— 否則使用者會把「指紋相同」��成
  # 「三台環境一樣」。那是兩件事：值會漂移而結構不變（而結構漂移才是這裡
  # 要抓的），反過來也會。
  echo "env-sync --check: 版面 $layout_fp（三台的**結構**必須相同；值請用 --fingerprints 比）"
  # 鍵覆蓋率要把三層都算進去：共用憑證範本、per-host 機密範本、共用非敏感。
  # 漏算 per-host 機密層＝「該機根本沒有這兩個鍵」沒人管，而症狀是本機
  # qdrant/pg 認證失敗（401／心跳失敗），極難回推到是環境變數缺了。
  #
  # ⚠️ 範本裡「值為空」的鍵**不比對**，理由是它與 merge 端語意相反會造成死結
  #   （2026-09-30 x570 回報的 2 個 FAIL 就是這個）：
  #     merge 端  py_apply: layer = {k: v for k, v in source.items() if v != ""}
  #              → 來源空值不合併，「空值＝沿用本機現值」是全專案的既定語意
  #     check 端 若把空值鍵也要求存在 → .env 缺了就報 fail，而 pull 永遠補不上
  #              （merge 層裡根本沒有那個鍵）→ 兩邊矛盾，該機無論怎麼做都清不掉
  #   判準是「值為空」而不是「在不在某個檔」：那正是本專案對空值的統一語意
  #   （hosts.shared.env 的空值＝該機沿用自己的；--init-secrets 的空值放行）。
  #
  #   憑證層（sec／host）**不套用**這個豁免：那兩層的值是「有或沒有」而非
  #   「空＝沿用」，漏了就是 401／心跳失敗，必須報 fail。實測兩把在範本裡都有值。
  python3 - "$DOTENV" "$ENV_DIR/secrets.common.env.example" \
      "$ENV_DIR/secrets.host.env.example" "$ENV_DIR/common.env" <<'PY'
import os, re, sys
def keys(p):
    out, withval = set(), set()
    if not os.path.exists(p):     # 範本檔缺了就當空集；存在性另有專門檢查
        return out, withval
    for line in open(p, encoding="utf-8").read().splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
        if m:
            out.add(m.group(1))
            if m.group(2).strip():
                withval.add(m.group(1))
    return out, withval
env = keys(sys.argv[1])[0]
sec, secv = keys(sys.argv[2])
host, hostv = keys(sys.argv[3])
com, comv = keys(sys.argv[4])
# 憑證層要全比（漏 = 認證失敗）；共用非敏感只比「來源有值」的鍵。
missing = sorted((sec | host | (comv & com)) - env)
if missing:
    print("env-sync --check: .env 缺少鍵: " + " ".join(missing))
    sys.exit(1)
# 豁免掉的鍵要說清楚有幾個 —— 靜默地少檢查比不檢查更糟，因為下一个人會以為
# 「--check 綠 = 每個鍵都有人管」。這些鍵靠 compose 的 ${VAR:-預設} 與原始碼
# 的 fallback 存活（2026-09-30 wsl 實測：EMBED_MODEL 與 RERANK_MODEL 在 .env
# 裡是空的，查詢照跑、confidence=high）。
skipped = sorted((com - comv) - env)
if skipped:
    print(f"env-sync --check: key coverage ok ({len(env)} keys in .env)；"
          f"跳過 {len(skipped)} 個共用層空值鍵（來源無值＝不要求本機有，"
          f"靠預設值存活）: {' '.join(skipped)}")
else:
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
    #
    # `host-inventory/*.txt` 也放行（2026-10-02）：那三個檔是**三機往返的
    # 欄位快照**，內容只有 `KEY: SET|EMPTY|ABSENT`，**沒有任何值**。
    #
    # 這條放行不該靠信任，而是靠三道獨立的守衛：
    #  1. 格式本身用**冒號**分隔 —— `KEY: SET` 結構上不可能被讀成賦值
    #     （`test_column_format_cannot_be_mistaken_for_an_assignment`）
    #  2. `test_no_tracked_secret_values` 掃**所有**被追蹤檔，任何 `SECRET=<值>`
    #     都會紅 —— 放行白名單不會繞過它
    #  3. `test_emit_column_output_passes_the_repo_own_guards` 掃這個目錄的
    #     `^LAN_IP=`
    # 換句話說：白名單放行的是**檔名**，安全由**內容規則與測試**保證。
    local extra
    extra="$(git -C "$ROOT" ls-files settings/env/ \
      | grep -vE '(\.md|\.env\.example|common\.env|hosts\.shared\.env|\.enc\.env)$' \
      | grep -vE '^settings/env/host-inventory/[a-z0-9_-]+\.txt$' || true)"
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
  py_apply table check "$TABLE" "$host" 1
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
# 缺鍵（整行不存在）是錯誤；空值放行 —— 空＝「未設定」。合併端本來就會跳過空值，
# 所以空值進加密檔不會清空別台的真值；只報鍵名，不印值。
# （2026-09-30 前的 ZEN_API_KEY 就是這樣的案例：分發了兩年、沒有任何一台設過。
#  那把已從 SHARED_SECRETS 移出 —— 「分發一個沒人設的空值」不是保險，
#  是幽靈鍵。要判斷一把共用憑證該不該留在清單裡，看的是「有沒有機真的設過它」，
#  而這件事看 enc 檔裡那行有沒有 ENC[...] 就知道，不必靠猜。）
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
  --hosts-table) cmd_hosts_table ;;
  --fingerprints) fingerprints "${2:-$DOTENV}" ;;
  --init-secrets) cmd_init_secrets "${2:-}" ;;
  -h|--help|help) usage ;;
  *) echo "env-sync: unknown arg: $1" >&2; usage >&2; exit 2 ;;
esac
