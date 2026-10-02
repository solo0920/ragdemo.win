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
#       從 .env 的 LAW_SYNC_SOURCE（來源機的 qdrant）強制重抓快照，繞過「點數沒變就 skip」
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

# 互斥：避免 cron 重疊或使用者手動同時觸發，跑兩次
#
# ⚠️ macOS 沒有 flock(1)（那是 util-linux 的東西）。2026-10-01 mbp 實測
#    `flock -n 9` 回 127（command not found）→ `! 127` 為真 → 每分鐘都記
#    「另一個 worker 實例執行中，跳過」再 exit 0。**症狀看起來像正常的排程去重，
#    實際是 worker 在 macOS 上一次都沒執行過**，前端「更新」按鈕因此是死的。
#    而且 log 會一直出現，看久了會被當成「正常」。
#
# 有 flock 就走原路（Linux 行為完全不變）；沒有就退回 mkdir —— 在 POSIX 檔案
# 系統上 mkdir 是原子的，所以同樣能當鎖。
LOCKDIR="$OPS/.worker.lock.d"
LOCK_HELD=0
# ⚠️ 這個 trap 必須**先**設：下面第 53 行 `[ -f "$REQ" ] || exit 0`（沒有請求，
#    也就是每分鐘的正常情況）會在取得鎖之後立刻離開。若等到有 $RUN 才設 trap，
#    鎖就會每分鐘洩漏一次，第 54 行起從此再也拿不到鎖 —— 而洩漏的鎖看起來
#    又是「另一個實例執行中」，症狀與上面那個 bug 幾乎一樣難查。
_worker_cleanup() {
  [ -n "${RUN:-}" ] && rm -f "$RUN"
  [ "$LOCK_HELD" = 1 ] && rmdir "$LOCKDIR" 2>/dev/null
  return 0
}
trap _worker_cleanup EXIT

if command -v flock >/dev/null 2>&1; then
  exec 9>"$OPS/.worker.lock"
  if ! flock -n 9; then
    log "另一個 worker 實例執行中，跳過"
    exit 0
  fi
else
  if ! mkdir "$LOCKDIR" 2>/dev/null; then
    owner="$(cat "$LOCKDIR/pid" 2>/dev/null || echo "")"
    if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then
      log "另一個 worker 實例執行中（pid ${owner}），跳過"
      exit 0
    fi
    # pid 已不在 → 上次是被 kill -9，不會跑 EXIT trap，鎖是殘留的。
    # 不清的話這臺機器的 worker 會從此永遠跳過。
    log "清除殘留鎖（pid ${owner:-未知} 已不存在）"
    rm -rf "$LOCKDIR"
    mkdir "$LOCKDIR" 2>/dev/null || { log "另一個 worker 實例執行中，跳過"; exit 0; }
  fi
  LOCK_HELD=1
  printf '%s\n' "$$" >"$LOCKDIR/pid"
fi

[ -f "$REQ" ] || exit 0        # 沒請求 → 正常情況，靜默退出
[ -f "$ENV_FILE" ] || { log "找不到 ${ENV_FILE}，放棄"; exit 1; }

set -a; . "$ENV_FILE"; set +a
TS_IP="${TS_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
# 來源機的 qdrant：只認 .env 裡的 LAW_SYNC_SOURCE，沒有就明確失敗（見下方）。
# 舊版硬寫 x570 的 tailscale IP —— 一台沒設定的備援機按「更新」會去戳不相干的機器。
SRC_Q="${LAW_SYNC_SOURCE:-}"
DST_Q="http://${TS_IP}:6333"
COLLECTION="${COLLECTION:-laws}"

printf '{"started_at":"%s"}\n' "$(date -Iseconds)" >"$RUN"
trap 'rm -f "$RUN"' EXIT

# 角色判斷：以「有沒有 .law_sync.json」為準（來源機跑過 sync_daily 才會有）
if [ -f "$ROOT/data/laws/.law_sync.json" ]; then
  ROLE="source"; CMD="uv run ingest/laws/sync_daily.py --apply"
elif [ -n "$SRC_Q" ]; then
  ROLE="backup";  CMD="scripts/sync-snapshot.sh --force $SRC_Q $DST_Q $COLLECTION"
else
  # 備援機卻沒設 LAW_SYNC_SOURCE：以前會帶著寫死的來源位址跑，結果是
  # 「更新」按了兩分鐘才失敗。現在立刻寫出狀態講清楚缺什麼（前端看得到）。
  write_status() {
    python3 - "$STATUS" "$1" <<'PY'
import json, sys, pathlib, datetime
path, msg = sys.argv[1:3]
pathlib.Path(path).write_text(json.dumps({
    "finished_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "role": "backup", "ok": False, "returncode": 2, "seconds": 0,
    "version": "", "output_tail": msg,
}, ensure_ascii=False, indent=2), encoding="utf-8")
PY
  }
  MSG="這台是備援機，但 .env 沒有 LAW_SYNC_SOURCE；請填來源機的 qdrant 位址（http://<tailscale-ip>:6333）"
  write_status "$MSG"
  log "$MSG"
  exit 1
fi

log "開始更新（角色=${ROLE}）: $CMD"
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
log "更新結束 rc=${RC}（$((END-START))s）version=${TAILOUT:0:0}$(python3 -c "
import json,sys
try: print(json.load(open('$STATUS'))['version'] or '(無)')
except Exception: print('(讀不到)')")"
