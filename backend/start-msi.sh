#!/usr/bin/env bash
# MSI WSL 啟動 RagDemo API（Windows 開機登入由 startup 呼叫）
# 冪等：8000 已有服務或 pidfile 存活就直接跳過。
set -euo pipefail

BASE=/home/solo/projects/ragdemo.win/backend
PIDFILE="$BASE/.uvicorn.pid"
LOG="$BASE/uvicorn.log"
PORT=8000

if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
  echo "[start-msi] already serving on :${PORT}, skip" >>"$LOG"
  exit 0
fi
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "[start-msi] pidfile alive ($(cat "$PIDFILE")), skip" >>"$LOG"
  exit 0
fi

cd "$BASE"
echo "--- [start-msi] $(date '+%F %T') starting ---" >>"$LOG"
setsid nohup "$BASE/.venv/bin/uvicorn" app.main:app \
  --host 0.0.0.0 --port "$PORT" --env-file .env \
  >>"$LOG" 2>&1 < /dev/null &
PID=$!
echo "$PID" > "$PIDFILE"
echo "[start-msi] started pid=$PID" >>"$LOG"

# 回報結果（供 wsl.exe 啟動器判斷）
sleep 3
if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
  echo "[start-msi] OK after start" >>"$LOG"
  exit 0
else
  echo "[start-msi] FAILED health check" >>"$LOG"
  exit 1
fi