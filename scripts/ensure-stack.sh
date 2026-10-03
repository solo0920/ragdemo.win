#!/usr/bin/env bash
# ensure-stack.sh — 確保 ragdemo 三容器可用；異常時建立或重建
#
# 背景（一）：用 `shutdown now` 關機不等 systemd 收尾，docker 容器網路 sandbox 可能
# 損壞（NetworkSettings.Networks 為 {}），開機後 api 連不到 qdrant 無限重啟，
# 且單純 `docker compose up -d` 無法修復（容器物件存在但沒掛網路）→ 需 force-recreate。
# 本腳本以 /health 為判準。
#
# 用法：bash scripts/ensure-stack.sh [--cron] [--grace N]
#   --cron      靜默模式（只在修復時輸出，供 crontab 用）
#   --grace N   冷卻期秒數（預設 60）。容器比較多的機器要調大；
#               測試用它跑得完（預設值的行為由靜態斷言守住）。
set -uo pipefail

# 背景（二）：`restart: always` **只重啟「已存在」的容器，不會「建立」容器**。
#   2026-10-02 wsl 實測：WSL 重啟後 `docker compose ps` 是空的，而 image 與
#   volume 都還在 —— 沒有任何東西會把它們建回來，所以要有這支。
#
# 背景（三）：**「還在啟動」≠「壞掉」**（2026-10-02 wsl 實測踩到）。
#   原版是 docker 一就緒就立刻判健康，判定失敗就 `--force-recreate`。
#   但 @reboot 那時容器正由 `restart: always` 拉起來、/health 還沒 200
#   （實測冷啟動到 200 要 8 秒）→ watchdog 把它們判成故障 → **每開一次機
#   就無謂地重建三個容器**，而重建後它們確實會好，於是回報 exit 0 ——
#   「成功修好一個其實沒壞的東西」，watchdog 變成破壞者。
#
# 背景（四）：`--force-recreate` 只該用於**容器已存在但網路 sandbox 損壞**
#   （本檔開頭那個 NetworkSettings.Networks 為 {} 的情境）。
#   「容器根本不存在」用 `docker compose up -d` 就好 —— 那是建立，不是重建。
#   實測兩者的差別：容器不存在時直接 force-recreate 等於剛建好的三個容器
#   又被刪掉重建（ID 全變）。所以分成兩條路。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/data/laws/ensure-stack.log"
QUIET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cron) QUIET=1 ;;
    --grace) [ $# -ge 2 ] || { echo "ensure-stack: --grace needs a value" >&2; exit 2; }
             GRACE_OVERRIDE="$2"; shift ;;
    --grace=*) GRACE_OVERRIDE="${1#--grace=}" ;;
    *) echo "ensure-stack: unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
GRACE_OVERRIDE="${GRACE_OVERRIDE:-}"
# 冷卻期：docker 就緒後先給這麼多秒讓 stack 自己好起來，再判定是否故障。
# 實測冷啟動到 /health 200 是 8 秒，60 有很大餘裕（容器多的機器要調大）。
GRACE_SEC="${GRACE_OVERRIDE:-60}"
case "$GRACE_SEC" in
  ''|*[!0-9]*) echo "ensure-stack: --grace must be a non-negative integer" >&2; exit 2 ;;
esac

log() { mkdir -p "$(dirname "$LOG")"; echo "[$(date '+%F %T')] $*" >>"$LOG"; }

cd "$ROOT" || exit 1

# 1) 等 docker daemon 就緒（開機時 docker 可能還在啟動中）
for i in $(seq 1 30); do
  docker info >/dev/null 2>&1 && break
  sleep 2
done
if ! docker info >/dev/null 2>&1; then
  log "docker daemon 未就緒，放棄（下次排程再試）"
  [ "$QUIET" = 1 ] || echo "docker 未就緒"
  exit 1
fi

# 2) 健康判準：容器全 running 且 /health 200
healthy() {
  local st
  st=$(docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null | awk '{print $2}')
  echo "$st" | grep -q restarting && return 1
  [ "$(echo "$st" | grep -c running)" -ge 3 ] || return 1
  # ⚠️ 2026-10-03：原本寫死 `127.0.0.1:8000`。`HOST_API_LOCAL` 是既有的 per-host
  #   覆寫變數（`.env` 有、host-doctor.sh:38 與 host-sync.sh:47 都讀），但這裡沒讀 ——
  #   於是**改埠之後這裡會繼續打舊埠**，症狀是「stack 一直等不到就緒」。
  curl -sf -m 8 -o /dev/null "${HOST_API_LOCAL:-http://127.0.0.1:8000}/health"
}

# 3) 先等冷卻期。healthy 一開始就成立時這裡立刻返回，所以對正常狀態
#    零延遲；只有在「現在不健康」時才會花時間等。
for i in $(seq 1 "$GRACE_SEC"); do
  if healthy; then
    log "OK：容器與 /health 正常"
    [ "$QUIET" = 1 ] || echo "OK: stack healthy"
    exit 0
  fi
  sleep 1
done

# 4) 冷卻期過了還是不健康 → 真的故障。分兩條路：
#    容器根本不存在 → 建立；容器存在但壞掉 → 重建（網路 sandbox 那種）。
#    混在一起做的後果（實測）：剛 `up -d` 起来的容器立刻被 force-recreate，
#    三個 ID 全換，等於把「建立」做成「建立完馬上刪掉重建」。
n_all=$(docker compose ps -aq 2>/dev/null | grep -c . || true)
if [ "${n_all:-0}" -eq 0 ]; then
  log "偵測異常：容器不存在（down 過或從未建立）→ docker compose up -d 建立"
  docker compose up -d >>"$LOG" 2>&1
else
  log "偵測異常，強制重建：$(docker compose ps --format '{{.Name}}={{.State}}' 2>/dev/null | tr '\n' ' ')"
  log "qdrant networks=$(docker inspect ragdemo-qdrant-1 --format '{{json .NetworkSettings.Networks}}' 2>/dev/null)"
  docker compose up -d --force-recreate >>"$LOG" 2>&1
fi

# 5) 重建後確認
for i in $(seq 1 15); do
  if healthy; then
    log "已修復：/health 正常"
    [ "$QUIET" = 1 ] || echo "已修復: stack healthy"
    exit 0
  fi
  sleep 4
done

log "重建後仍不健康，請人工檢查（docker compose logs api）"
[ "$QUIET" = 1 ] || echo "仍不健康，見 $LOG"
exit 1
