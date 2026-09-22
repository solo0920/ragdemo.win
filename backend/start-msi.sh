#!/usr/bin/env bash
# MSI WSL 啟動本機 qdrant（x570 離線備援）＋ RagDemo API（Windows 開機登入由 startup 呼叫）
# 冪等：已啟動就跳過。qdrant log → ~/qdrant/qdrant.log；uvicorn log → backend/uvicorn.log。
set -euo pipefail

BASE=/home/solo/projects/ragdemo.win/backend
PIDFILE="$BASE/.uvicorn.pid"
LOG="$BASE/uvicorn.log"
PORT=8000
QDRANT_DIR="$HOME/qdrant"
QDRANT_PIDFILE="$QDRANT_DIR/.qdrant.pid"

# ---- 1) 本機 qdrant（備援）----
ensure_qdrant() {
  [ -x "$QDRANT_DIR/qdrant" ] || { echo "[start-msi] qdrant binary missing at $QDRANT_DIR/qdrant" >>"$LOG"; return 0; }
  if curl -sf http://localhost:6333/healthz >/dev/null 2>&1; then
    echo "[start-msi] qdrant already up on :6333, skip" >>"$LOG"
    return 0
  fi
  if [ -f "$QDRANT_PIDFILE" ] && kill -0 "$(cat "$QDRANT_PIDFILE" 2>/dev/null)" 2>/dev/null; then
    echo "[start-msi] qdrant pidfile alive, skip" >>"$LOG"
    return 0
  fi
  cd "$QDRANT_DIR"
  setsid nohup "$QDRANT_DIR/qdrant" --disable-telemetry \
    >>"$QDRANT_DIR/qdrant.log" 2>&1 < /dev/null &
  echo $! > "$QDRANT_PIDFILE"
  echo "[start-msi] qdrant started pid=$(cat "$QDRANT_PIDFILE")" >>"$LOG"
  sleep 2
}

# ---- 2) API ----
if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
  echo "[start-msi] already serving on :${PORT}, skip" >>"$LOG"
  curl -sf http://localhost:6333/healthz >/dev/null 2>&1 || ensure_qdrant
  exit 0
fi
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "[start-msi] api pidfile alive, skip" >>"$LOG"
  curl -sf http://localhost:6333/healthz >/dev/null 2>&1 || ensure_qdrant
  exit 0
fi

ensure_qdrant

cd "$BASE"
echo "--- [start-msi] $(date '+%F %T') starting ---" >>"$LOG"
setsid nohup "$BASE/.venv/bin/uvicorn" app.main:app \
  --host 0.0.0.0 --port "$PORT" --env-file .env \
  >>"$LOG" 2>&1 < /dev/null &
PID=$!
echo "$PID" > "$PIDFILE"
echo "[start-msi] api started pid=$PID" >>"$LOG"

# 回報結果（供 wsl.exe 啟動器判斷）— uvicorn 啟動稍慢，重試 10 次
for i in $(seq 1 10); do
  if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    echo "[start-msi] OK after start (try $i)" >>"$LOG"
    exit 0
  fi
  sleep 2
done
echo "[start-msi] FAILED health check after retries" >>"$LOG"
exit 1