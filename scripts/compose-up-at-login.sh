#!/usr/bin/env bash
# compose-up-at-login.sh — 登入後把本機 compose stack 拉起來（launchd 呼叫）
#
# 為什麼需要這支：compose.yaml 的 `restart: unless-stopped` **只重啟「已存在」的
# 容器，不會「建立」容器**。2026-10-01 實測這台是 `docker ps -a` 完全沒有 ragdemo
# 容器（只有 2025-02-10 被 SIGKILL 的 laradock 殘骸，其 policy=no 本來就不會自啟），
# 但 image（ragdemo-api:latest）與 volume（ragdemo_pg_data / ragdemo_qdrant_data）
# 都還在 —— 典型的 `docker compose down` 或 prune 痕跡。
#
# 所以「以前進系統會自動 up、現在不會」不是設定壞掉，是**沒有任何東西會建立容器**。
# `down` 之後唯一能收斂回去的就是這支。
#
# 設計：
#   - 冪等：已經在跑就什麼都不做（compose 對 running 的容器是 no-op）。
#   - **不 --build**：登入的責任是「讓 stack 是 running」，不是重建 image。
#     重建是刻意的動作（改了 backend/ 才做），放進登入路徑會變成每次登入都
#     靜默花幾分鐘跑一次 build，出了事也不知道是哪一次 build 造成的。
#   - 等 engine ready 再上：`RunAtLoad` 在登入就跑，那一刻 OrbStack 的
#     Docker engine 常常還沒起來，直接 `docker compose up -d` 會得到
#     "Cannot connect to the Docker daemon"。`orbctl start` 冪等且會 block 到
#     ready（已運行時印 "already running" 並 exit 0），比 sleep 猜時間可靠。
#   - 不設 launchd KeepAlive：KeepAlive 會在每次結束後再跑，變成迴圈。
#     登入時收斂一次就夠，RunAtLoad 正確。
#
# ⚠️ log 不得出現憑證值。特別不要在這裡跑 `docker compose config`（會展開所有
#    憑證）。repo 內 `data/.ops/` 已被 .gitignore 涵蓋，但 log 仍只寫狀態。
set -euo pipefail

# ⚠️ PATH 必須自己給。launchd 的環境**不含** homebrew：
#    實測 2026-10-01 第一次 `launchctl bootstrap` 後，job 直接 exit 1，
#    log 印「✗ 找不到 orbctl」—— 同一支腳本在終端機手跑是成功的，差別只在
#    呼叫者。launchd 給的 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin，
#    而 orbctl 在 /opt/homebrew/bin、docker 在 /usr/local/bin。
#    這就是「值有沒有傳進去」那類問題的 launchd 版本：不是值錯，是**找不到**。
#    放在腳本裡而不是只寫進 plist 的 EnvironmentVariables，是因為這支也可能被
#    cron / ssh / 手動呼叫，那些來源的 PATH 又各不相同 —— 腳本自己設定最可靠。
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

# ⚠️ 路徑要是 repo 的真實位置。這台是 ~/projects/ragdemo；
#    MBP-HANDOFF.md 的 plist 範例寫 ~/projects/ragdemo.win，但 2026-10-01 實測
#    **那個目錄不是 git repo、也沒有 .env**（只有一個空的 data/，4KB）——
#    照抄會讓 compose 讀不到 .env 而用預設值啟動（等於拿空憑證開機）。
#    這支腳本因此不依賴 launchd 傳的 WorkingDirectory，自己算 repo 根目錄。
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/data/.ops"
LOG="$LOG_DIR/compose-up.log"
mkdir -p "$LOG_DIR"

ts() { date '+%F %T'; }
log() { printf '%s %s\n' "$(ts)" "$*" >>"$LOG"; }

# ⚠️ 這裡必須寫 ${REPO} 不是 $REPO：後面接的是全形右括號「）」（U+FF09）。
#    bash 3.2（macOS 系統內建）在 UTF-8 locale 下會把該多位元組字元的高位元組
#    算進變數名，於是它查的是「REPO）」這個從沒定義過的變數 →
#    `REPO: unbound variable`，而且是在這行才爆，跟第 34 行的賦值無關，
#    症狀看起來像「明明賦值了卻說沒賦值」。
log "── 開始（repo=${REPO}）"

cd "$REPO"

# 1. 確保 OrbStack 在跑，且 engine ready。冪等。
if ! command -v orbctl >/dev/null 2>&1; then
  log "✗ 找不到 orbctl：這台的 Docker runtime 不是 OrbStack（或沒裝在 PATH）。中止。"
  log "   若換回 Docker Desktop，請把這支的 orbctl 步驟換成 open -a Docker + 等 socket。"
  exit 1
fi

if ! orbctl start >>"$LOG" 2>&1; then
  log "✗ orbctl start 失敗（見上）。中止。"
  exit 1
fi

# 2. 再確認 daemon 真的可連線。orbctl 說 ready 之後仍做一次實測，
#    因為它報的是「engine 起來了」，不是「socket 已經能 accept」。
for i in $(seq 1 30); do
  if docker info >/dev/null 2>&1; then
    break
  fi
  [ "$i" -eq 30 ] && { log "✗ 等了 30 次 docker info 仍連不上。中止。"; exit 1; }
  sleep 2
done
log "✓ engine ready"

# 3. 記錄用的是哪個 context —— 認錯 context 等於把容器起在別的引擎上，
#    而症狀是「ps 明明有容器但這邊的 socket 是空的」。
log "  docker context = $(docker context show 2>/dev/null || echo '?')"
log "  server         = $(docker info --format '{{.ServerVersion}}/{{.OperatingSystem}}' 2>/dev/null || echo '?')"

# 4. 收斂。
if docker compose up -d >>"$LOG" 2>&1; then
  log "✓ docker compose up -d 完成"
  log "  現在：$(docker compose ps --format '{{.Service}}={{.State}}' 2>/dev/null | tr '\n' ' ')"
else
  log "✗ docker compose up -d 失敗（見上 log）。"
  log "  常見原因：.env 缺 POSTGRES_PASSWORD（compose 會直接拒絕啟動）。"
  exit 1
fi

log "── 結束"
