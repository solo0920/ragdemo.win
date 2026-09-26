#!/usr/bin/env bash
# ensure-stack.sh — 確保 ragdemo 三容器健康；異常時強制重建（修開機後網路殘留）
#
# 背景：用 `shutdown now` 關機不等 systemd 收尾，docker 容器網路 sandbox 可能
# 損壞（NetworkSettings.Networks 為 {}），開機後 api 連不到 qdrant 無限重啟，
# 且單純 `docker compose up -d` 無法修復（容器物件存在但沒掛網路）→ 需 force-recreate。
# 本腳本以 /health 為判準，失敗就 force-recreate 一次。
#
# 用法：bash scripts/ensure-stack.sh [--cron]
#   --cron  靜默模式（只在修復時輸出，供 crontab 用）
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/data/laws/ensure-stack.log"
QUIET=0
[ "${1:-}" = "--cron" ] && QUIET=1

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
  curl -sf -m 8 -o /dev/null http://127.0.0.1:8000/health
}

if healthy; then
  log "OK：容器與 /health 正常"
  [ "$QUIET" = 1 ] || echo "OK: stack healthy"
  exit 0
fi

# 3) 不健康 → 記錄現況後強制重建
log "偵測異常，強制重建：$(docker compose ps --format '{{.Name}}={{.State}}' 2>/dev/null | tr '\n' ' ')"
log "qdrant networks=$(docker inspect ragdemo-qdrant-1 --format '{{json .NetworkSettings.Networks}}' 2>/dev/null)"
docker compose up -d --force-recreate >>"$LOG" 2>&1

# 4) 重建後確認
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
