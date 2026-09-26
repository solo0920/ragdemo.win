#!/usr/bin/env bash
# law-update-worker.sh — 執行 api 排入的「強制更新法規版本」請求
#
# 為什麼需要這個腳本：容器跑不了 ingest 管線（image 沒有 ingest/、data/laws 唯讀、
# 沒裝 duckdb），管線是 host 端的 uv 工具鏈。所以 api 只寫請求檔，實際動作在這裡做。
#
# 依角色分兩種行為（2026-09-26 定案）：
#   來源機（有 data/laws/.law_sync.json）→ uv run ingest/laws/sync_daily.py --apply
#       從 law.moj.gov.tw 重新下載、清洗、寫 parquet/PG、整庫重建 qdrant
#   備援機（無 .law_sync.json）          → scripts/sync-snapshot.sh --force
#       從 x570 強制重抓快照（繞過「點數沒變就 skip」）
#
# 用法： scripts/law-update-worker.sh          # 由 cron 每分鐘呼叫；沒請求就立刻退出
#       scripts/law-update-worker.sh --status # 只印目前狀態
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OPS="$ROOT/data/.ops"
REQ="$OPS/law-update.request"
RUN="$OPS/law-update.running"
STATUS="$OPS/law-update.status"
LOG="$OPS/worker.log"
# 單一真相來源＝repo 根的 .env（compose 也讀這份）。2026-09-26 收斂：
# 原本這裡與 sync-snapshot.sh 讀 backend/.env，與根 .env 形成兩份副本，
# QDRANT_API_KEY 曾在兩者間漂移造成「本機 200、遠端 401」。fallback 保留給
# 尚未遷移的機器（刪掉 backend/.env 後自然走根）。
ENV_FILE="$ROOT/.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$ROOT/backend/.env"

log() { echo "[$(date '+%F %T')] $*" >>"$LOG"; }

# 逾時保護：worker 若被 kill 而沒清掉 .running，會卡住後續所有請求。
STALE_MIN=60
[ -f "$RUN" ] && [ -n "$(find "$RUN" -mmin "+$STALE_MIN" 2>/dev/null)" ] && {
  log "發現過期的 .running（超過 ${STALE_MIN} 分鐘），清除以解除卡住"
  rm -f "$RUN"
}

if [ "${1:-}" = "--status" ]; then
  for f in "$REQ" "$RUN" "$STATUS"; do
    [ -f "$f" ] && { echo "── $(basename "$f")"; cat "$f"; } || echo "── $(basename "$f"): (不存在)"
  done
  exit 0
fi

# flock：避免 cron 重疊或使用者手動同時觸發，跑兩次
exec 9>"$OPS/.worker.lock"
if ! flock -n 9; then
  log "另一個 worker 實例執行中，跳過"
  exit 0
fi

[ -f "$REQ" ] || exit 0        # 沒請求 → 正常情況，靜默退出
[ -f "$ENV_FILE" ] || { log "找不到 $ENV_FILE，放棄"; exit 1; }

set -a; . "$ENV_FILE"; set +a
TS_IP="${TS_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
SRC_Q="${LAW_SYNC_SOURCE:-http://100.119.83.111:6333}"
DST_Q="http://${TS_IP}:6333"
COLLECTION="${COLLECTION:-laws}"

printf '{"started_at":"%s"}\n' "$(date -Iseconds)" >"$RUN"
trap 'rm -f "$RUN"' EXIT

# 角色判斷：以「有沒有 .law_sync.json」為準（來源機跑過 sync_daily 才會有）
if [ -f "$ROOT/data/laws/.law_sync.json" ]; then
  ROLE="source"; CMD="uv run ingest/laws/sync_daily.py --apply"
else
  ROLE="backup";  CMD="scripts/sync-snapshot.sh --force $SRC_Q $DST_Q $COLLECTION"
fi

log "開始更新（角色=$ROLE）: $CMD"
START=$(date +%s)
OUT="$OPS/.last-output.log"
( cd "$ROOT" && eval "$CMD" ) >"$OUT" 2>&1
RC=$?
END=$(date +%s)
# 輸出摘要在「被執行腳本自己的 log」而不是 stdout：sync-snapshot.sh 全程寫
# ~/qdrant/sync.log，stdout 是空的（2026-09-26 實測踩到，status 摘要是空的）。
TAILOUT="$(tail -4 "$OUT" 2>/dev/null | tr '\n' ' ' | cut -c1-300)"
if [ -z "$TAILOUT" ]; then
  TAILOUT="$(tail -3 "$HOME/qdrant/sync.log" 2>/dev/null | tr '\n' ' ' | cut -c1-300)"
fi

# 結果寫成 status（api 的 GET /law-update 會讀它回報給前端）
python3 - "$STATUS" "$ROLE" "$RC" "$((END-START))" "$TAILOUT" <<'PY'
import json, sys, pathlib, datetime
path, role, rc, secs, out = sys.argv[1:6]
ver = ""
p = pathlib.Path("data/laws")
for name in (".law_version", ".law_sync.json"):
    f = p / name
    try:
        if f.exists():
            ver = (json.loads(f.read_text(encoding="utf-8")).get("update_date") or "")
            if ver:
                break
    except Exception:
        pass
pathlib.Path(path).write_text(json.dumps({
    "finished_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "role": role, "ok": rc == "0", "returncode": int(rc),
    "seconds": int(secs), "version": ver, "output_tail": out,
}, ensure_ascii=False, indent=2), encoding="utf-8")
PY

rm -f "$REQ"
log "更新結束 rc=$RC（$((END-START))s）version=${TAILOUT:0:0}$(python3 -c "
import json,sys
try: print(json.load(open('$STATUS'))['version'] or '(無)')
except Exception: print('(讀不到)')")"
