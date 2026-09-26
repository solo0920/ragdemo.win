#!/usr/bin/env bash
# sync-snapshot.sh — 把 x570(主) qdrant 的 laws 快照同步還原到本機(備援) qdrant
# 用法： scripts/sync-snapshot.sh [--force] [source_url] [dest_url] [collection]
#   預設 source=http://100.119.83.111:6333（x570 tailscale） dest=http://127.0.0.1:6333（本機）
#   --force  強制重抓：跳過「點數沒變就 skip」。給 law-update worker 用 ——
#            使用者按了「更新」但點數未變（例：只換了法規版本、條文數不動）時，
#            沒有 --force 就會靜默 skip，按鈕看起來沒反應。
# 設計：
#   - x570 離線 → 直接跳過（不破壞本機現有資料，log 記錄）
#   - 用「點數變化」偵測新資料：metadata(.sync-state) 記上次 points_count，
#     x570 點數與上次不同才建新快照→下載→本機刪舊→重建→上傳還原→驗證點數一致才更新 state
#   - 每次同步後順手刪除 x570 上的舊快照（只留最新的），避免 stack 無限累積
#   - log 寫 ~/qdrant/sync.log
set -euo pipefail

FORCE=0
if [ "${1:-}" = "--force" ]; then FORCE=1; shift; fi

SOURCE="${1:-http://100.119.83.111:6333}"
DEST="${2:-http://${TS_IP:-127.0.0.1}:6333}"
COLLECTION="${3:-laws}"
QDIR="$HOME/qdrant"
LOG="$QDIR/sync.log"
STATE="$QDIR/.sync-state"   # 內容 e.g. "3 laws-xxx.snapshot"
TMP="$QDIR/.sync.tmp.snapshot"
TS="$(date '+%F %T')"
# 認證用兩把 key，不要混：
#   QDRANT_API_KEY      本機自己的 qdrant（backend 查詢用同一把）
#   QDRANT_PEER_API_KEY 同步對象（x570）的 qdrant —— 必須另外設定
# 過去只有一把，得以運作純粹因為三機共用同一把；一旦任一輪換就變成
# 「本機 200、遠端 401」的不對稱（2026-09-26 MSI 實測踩到）。所以拆開。
# 未設定 QDRANT_PEER_API_KEY 時退回 QDRANT_API_KEY，維持舊的單機設定可用。
PEER_KEY="${QDRANT_PEER_API_KEY:-$QDRANT_API_KEY}"

# Qdrant api-key：source/dest 任一啟用認證時需帶；未設定即無 key（舊版相容）。
AUTH_H=()
[ -n "$PEER_KEY" ] && AUTH_H=(-H "api-key: $PEER_KEY")

log() { echo "[$TS] $*" >>"$LOG"; }
pts_of() { curl -sf "${AUTH_H[@]}" -m 10 "$1/collections/$2" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["points_count"])' 2>/dev/null || echo -1; }

# 認證失敗要講清楚是哪一把 key 的問題。症狀（create snapshot failed）看不出
# 「來源離線」和「key 不對」的差別，2026-09-26 兩者都發生過、難以區分。
auth_precheck() {
  code="$(curl -s -m 15 -o /dev/null -w '%{http_code}' "${AUTH_H[@]}" "$SOURCE/collections/$COLLECTION" 2>/dev/null)"
  case "$code" in
    200) return 0 ;;
    401|403)
      if [ -z "${QDRANT_PEER_API_KEY:-}" ]; then
        log "AUTH 失敗（HTTP $code）：只有 QDRANT_API_KEY、沒設 QDRANT_PEER_API_KEY。"
        log "  本機 key 只適用於本機 qdrant；要寫入 $SOURCE 必須另外給對方的 key。"
      else
        log "AUTH 失敗（HTTP $code）：QDRANT_PEER_API_KEY 與 $SOURCE 的 qdrant 不符。"
      fi
      log "  比對方法（不外洩值）：比較兩邊的 sha256 前 12 碼是否一致。"
      return 1 ;;
    000) return 0 ;;   # 連不上，交给呼叫端報 offline
    *) return 0 ;;
  esac
}

[ -d "$QDIR" ] || mkdir -p "$QDIR"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$ROOT/data/laws/.law_version"

# 法規版本：官方 zip 檔名固定是 ChLaw.json.zip（實測 Content-Disposition），
# 拿不到版本意義，所以版本取 ChLaw.json 的 UpdateDate，由來源機的 /status 揭露。
# 備援機不跑 sync_daily.py、快照也不帶 .law_sync.json，只能靠這裡帶過來，
# 寫在 data/laws/ 下 —— 該目錄在容器內是唯讀掛載，但讀不受限，寫入在 host 端。
sync_law_version() {
  [ "$COLLECTION" = "laws" ] || return 0
  # 來源 api 的位置。三台都把 8000 綁在 127.0.0.1、公網只經 cloudflared tunnel
  # （見 ARCHITECTURE「服務埠」），所以 port 改寫在真實部署多半連不上，
  # 必須用 SRC_API_URL 明確指定（per-machine，設在該機 crontab/launchd 環境）。
  # 未設定才退而用 6333→8000 的改寫（適用於自己把 api 發布在 TS_IP 的情況）。
  if [ -n "${SRC_API_URL:-}" ]; then
    SRC_API="$SRC_API_URL"
  else
    case "$SOURCE" in
      *:[0-9]*) SRC_API="${SOURCE%:*}:8000" ;;
      *)        SRC_API="$SOURCE:8000" ;;
    esac
  fi
  # ⚠️ 這行的 `|| true` 不能拿掉：pipefail 下 curl 連不上會讓整個賦值回傳非零，
  # `set -e` 會在到達下面的 if 之前就中止整支腳本 —— 症狀是「快照同步明明成功，
  # 腳本卻 exit 1」（2026-09-26 實測，害 law-update worker 誤報失敗）。
  ver="$(curl -sf -m 8 "$SRC_API/status" 2>/dev/null \
        | python3 -c 'import sys,json; print((json.load(sys.stdin).get("law_version") or {}).get("update_date") or "")' 2>/dev/null || true)"
  if [ -z "$ver" ]; then
    log "law version: 取不到（src_api=$SRC_API；來源機的 sync_daily.py 還沒跑過，或該網址不通）"
    return 0
  fi
  # 版本沒變就不重寫，避免每 10 分鐘動一次 mtime
  old="$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("update_date",""))
except Exception: print("")' "$VERSION_FILE" 2>/dev/null)"
  if [ "$old" = "$ver" ]; then
    log "law version unchanged ($ver)"
    return 0
  fi
  mkdir -p "$(dirname "$VERSION_FILE")"
  printf '{"update_date": "%s", "source": "%s", "synced_at": "%s"}\n' \
    "$ver" "$SOURCE" "$(date '+%F %T')" >"$VERSION_FILE.tmp" \
    && mv "$VERSION_FILE.tmp" "$VERSION_FILE" \
    && log "law version updated: $old -> $ver"
}

# 1) source 在線？
if ! curl -sf "${AUTH_H[@]}" -m 5 "$SOURCE/healthz" >/dev/null 2>&1; then
  log "source $SOURCE offline, skip（本機備援資料不受影響）"
  exit 0
fi

# 2) 點數沒變 → 跳過（但版本仍要更新：資料沒變不代表來源機換了法規版本）
auth_precheck || exit 1
SRC_PTS="$(pts_of "$SOURCE" "$COLLECTION")"
PREV_PTS="$(awk '{print $1}' "$STATE" 2>/dev/null || echo "")"
if [ "$FORCE" -eq 0 ] && [ "$SRC_PTS" = "$PREV_PTS" ] && [ -n "$PREV_PTS" ]; then
  sync_law_version
  log "unchanged (${SRC_PTS} points), skip"
  exit 0
fi
[ "$FORCE" -eq 1 ] && log "FORCE: 略過 unchanged 檢查（src_pts=$SRC_PTS prev=$PREV_PTS）"

# 3) 建新快照
SNAP_JSON="$(curl -sf "${AUTH_H[@]}" -m 30 -X POST "$SOURCE/collections/$COLLECTION/snapshots" 2>/dev/null)" \
  || { log "create snapshot failed"; exit 1; }
SNAP_NAME="$(echo "$SNAP_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["name"])' 2>/dev/null)"
[ -n "$SNAP_NAME" ] || { log "bad snapshot response: $SNAP_JSON"; exit 1; }

# 4) 下載
if ! curl -sf "${AUTH_H[@]}" -m 120 -o "$TMP" "$SOURCE/collections/$COLLECTION/snapshots/$SNAP_NAME"; then
  log "download failed for $SNAP_NAME"; rm -f "$TMP"; exit 1
fi
if command -v stat >/dev/null 2>&1 && stat -c%s "$TMP" >/dev/null 2>&1; then
  SZ="$(stat -c%s "$TMP")"
else
  SZ="$(stat -f%z "$TMP" 2>/dev/null || echo "?")"
fi
log "downloaded $SNAP_NAME ($SZ bytes, src_pts=$SRC_PTS)"

# 4b) 順手清 x570 其他快照（只留剛下載的這份）——即使後續 restore 失敗也不累積
OLD="$(curl -sf "${AUTH_H[@]}" -m 10 "$SOURCE/collections/$COLLECTION/snapshots" \
  | python3 -c "import sys,json; [print(s['name']) for s in json.load(sys.stdin)['result'] if s['name'] != '$SNAP_NAME']" 2>/dev/null)"
if [ -n "$OLD" ]; then
  while IFS= read -r n; do
    curl -sf "${AUTH_H[@]}" -m 30 -X DELETE "$SOURCE/collections/$COLLECTION/snapshots/$n" >/dev/null 2>&1 && log "cleaned remote snapshot $n"
  done <<<"$OLD"
fi

# 5) 本機：刪舊 → 直接上傳還原（不預建 collection！快照含 dense+sparse 雙向量，
#    priority=snapshot 會以快照內建設定重建 collection；2026-09-24 前預建的
#    dense-only config 反而 400 config mismatch → 本機 0 點）
curl -sf "${AUTH_H[@]}" -m 30 -X DELETE "$DEST/collections/$COLLECTION" >/dev/null 2>&1 \
  && log "deleted local $COLLECTION" || log "delete local: (原本不存在或失敗)"
curl -sf "${AUTH_H[@]}" -m 180 -X POST -F "snapshot=@$TMP" \
  "$DEST/collections/$COLLECTION/snapshots/upload?priority=snapshot" >/dev/null \
  || { log "restore upload failed"; rm -f "$TMP"; exit 1; }

# 6) 驗證
DST_PTS="$(pts_of "$DEST" "$COLLECTION")"
if [ "$SRC_PTS" = "$DST_PTS" ] && [ "$SRC_PTS" != "-1" ] && [ "$DST_PTS" != "-1" ]; then
  echo "$SRC_PTS $SNAP_NAME" >"$STATE"
  sync_law_version
  log "SYNC OK: $SNAP_NAME (${DST_PTS} points) local=$DEST ready"
  rm -f "$TMP"
  exit 0
else
  log "VERIFY FAILED: src=$SRC_PTS dst=$DST_PTS ($SNAP_NAME)"
  rm -f "$TMP"
  exit 1
fi