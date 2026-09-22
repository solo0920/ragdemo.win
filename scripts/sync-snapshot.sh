#!/usr/bin/env bash
# sync-snapshot.sh — 把 Linux(主) qdrant 的 laws 快照同步還原到本機(備援) qdrant
# 用法： scripts/sync-snapshot.sh [source_url] [dest_url]
#   預設 source=http://100.119.83.111:6333（Linux tailscale） dest=http://127.0.0.1:6333（本機）
# 設計：
#   - Linux 離線 → 直接跳過（不破壞本機現有資料，log 記錄）
#   - 用「點數變化」偵測新資料：metadata(.sync-state) 記上次 points_count，
#     Linux 點數與上次不同才建新快照→下載→本機刪舊→重建→上傳還原→驗證點數一致才更新 state
#   - 每次同步後順手刪除 Linux 上的舊快照（只留最新的），避免 stack 無限累積
#   - log 寫 ~/qdrant/sync.log
set -euo pipefail

SOURCE="${1:-http://100.119.83.111:6333}"
DEST="${2:-http://127.0.0.1:6333}"
COLLECTION="${3:-laws}"
QDIR="$HOME/qdrant"
LOG="$QDIR/sync.log"
STATE="$QDIR/.sync-state"   # 內容 e.g. "3 laws-xxx.snapshot"
TMP="$QDIR/.sync.tmp.snapshot"
TS="$(date '+%F %T')"

log() { echo "[$TS] $*" >>"$LOG"; }
pts_of() { curl -sf -m 10 "$1/collections/$2" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["points_count"])' 2>/dev/null || echo -1; }

[ -d "$QDIR" ] || mkdir -p "$QDIR"

# 1) source 在線？
if ! curl -sf -m 5 "$SOURCE/healthz" >/dev/null 2>&1; then
  log "source $SOURCE offline, skip（本機備援資料不受影響）"
  exit 0
fi

# 2) 點數沒變 → 跳過
SRC_PTS="$(pts_of "$SOURCE" "$COLLECTION")"
PREV_PTS="$(awk '{print $1}' "$STATE" 2>/dev/null || echo "")"
if [ "$SRC_PTS" = "$PREV_PTS" ] && [ -n "$PREV_PTS" ]; then
  log "unchanged (${SRC_PTS} points), skip"
  exit 0
fi

# 3) 建新快照
SNAP_JSON="$(curl -sf -m 30 -X POST "$SOURCE/collections/$COLLECTION/snapshots" 2>/dev/null)" \
  || { log "create snapshot failed"; exit 1; }
SNAP_NAME="$(echo "$SNAP_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["name"])' 2>/dev/null)"
[ -n "$SNAP_NAME" ] || { log "bad snapshot response: $SNAP_JSON"; exit 1; }

# 4) 下載
if ! curl -sf -m 120 -o "$TMP" "$SOURCE/collections/$COLLECTION/snapshots/$SNAP_NAME"; then
  log "download failed for $SNAP_NAME"; rm -f "$TMP"; exit 1
fi
if command -v stat >/dev/null 2>&1 && stat -c%s "$TMP" >/dev/null 2>&1; then
  SZ="$(stat -c%s "$TMP")"
else
  SZ="$(stat -f%z "$TMP" 2>/dev/null || echo "?")"
fi
log "downloaded $SNAP_NAME ($SZ bytes, src_pts=$SRC_PTS)"

# 5) 本機：刪舊 → 重建 → 上傳還原
curl -sf -m 30 -X DELETE "$DEST/collections/$COLLECTION" >/dev/null 2>&1 \
  && log "deleted local $COLLECTION" || log "delete local: (原本不存在或失敗)"
curl -sf -m 30 -X PUT "$DEST/collections/$COLLECTION" \
  -H 'content-type: application/json' \
  -d '{"vectors":{"size":1024,"distance":"Cosine"}}' >/dev/null \
  || { log "create local collection failed"; rm -f "$TMP"; exit 1; }
curl -sf -m 120 -X POST -F "snapshot=@$TMP" \
  "$DEST/collections/$COLLECTION/snapshots/upload?priority=snapshot" >/dev/null \
  || { log "restore upload failed"; rm -f "$TMP"; exit 1; }

# 6) 驗證
DST_PTS="$(pts_of "$DEST" "$COLLECTION")"
if [ "$SRC_PTS" = "$DST_PTS" ] && [ "$SRC_PTS" != "-1" ] && [ "$DST_PTS" != "-1" ]; then
  echo "$SRC_PTS $SNAP_NAME" >"$STATE"
  log "SYNC OK: $SNAP_NAME (${DST_PTS} points) local=$DEST ready"
  # 清理 Linux 舊快照，只留最新（避免無限累積）
  OLD="$(curl -sf -m 10 "$SOURCE/collections/$COLLECTION/snapshots" \
    | python3 -c "import sys,json; [print(s['name']) for s in json.load(sys.stdin)['result'] if s['name'] != '$SNAP_NAME']" 2>/dev/null)"
  if [ -n "$OLD" ]; then
    while IFS= read -r n; do
      curl -sf -m 30 -X DELETE "$SOURCE/collections/$COLLECTION/snapshots/$n" >/dev/null 2>&1 && log "cleaned remote snapshot $n"
    done <<<"$OLD"
  fi
  rm -f "$TMP"
  exit 0
else
  log "VERIFY FAILED: src=$SRC_PTS dst=$DST_PTS ($SNAP_NAME)"
  rm -f "$TMP"
  exit 1
fi