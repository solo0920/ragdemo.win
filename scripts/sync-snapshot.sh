#!/usr/bin/env bash
# sync-snapshot.sh — 把來源機(source) qdrant 的 laws 快照同步還原到本機(備援) qdrant
# 用法： scripts/sync-snapshot.sh [--force] [source_url] [dest_url] [collection]
#   來源機由呼叫者決定（cron / law-update-worker 傳進來），**本檔不猜**。
#   舊版預設 source 寫死 x570 的 tailscale IP，結果是任何新機器的第一次同步都會去
#   戳一台不相干的機器，而且失敗只寫在 log 裡。改成未給就明確失敗。
#   dest 預設本機（http://127.0.0.1:6333）。TS_IP 有設時才綁它，是為了讓呼叫者
#   能指定「本機 qdrant 的 tailscale 位址」；單機部署不設 TS_IP 也完全能跑。
#   --force  強制重抓：跳過「點數沒變就 skip」。給 law-update worker 用 ——
#            使用者按了「更新」但點數未變（例：只換了法規版本、條文數不動）時，
#            沒有 --force 就會靜默 skip，按鈕看起來沒反應。
# 設計：
#   - 來源機離線 → 直接跳過（不破壞本機現有資料，log 記錄）
#   - 用「點數變化」偵測新資料：metadata(.sync-state) 記上次 points_count，
#     來源機點數與上次不同才建新快照→下載→本機刪舊→重建→上傳還原→驗證點數一致才更新 state
#   - 每次同步後順手刪除來源機上的舊快照（只留最新的），避免 stack 無限累積
#   - log 寫 ~/qdrant/sync.log
set -euo pipefail

FORCE=0
if [ "${1:-}" = "--force" ]; then FORCE=1; shift; fi

# ⚠️ SOURCE 的求值在檔尾 `_load_env` **之後**（2026-09-30 修正）。
#   這裡原本寫在檔頭第 23 行，而 `.env` 是到第 114 行才載入 → **.env 裡的
#   LAW_SYNC_SOURCE 永遠讀不到**，而用法訊息卻說「也可用環境變數
#   LAW_SYNC_SOURCE 指定（cron 用這個比較順）」—— 那是假的。
#   症狀：mbp 的快照同步從沒運作過，而 log 是 0 bytes（連「offline skip」
#   都沒印，因為它在讀 SOURCE 之前就 exit 2 了）。
#   為什麼會寫成那樣：檔頭那段是「位置參數優先、env 次之」的常見寫法，
#   但忘了 .env 這個 env 來源本身還沒被讀進來。
#
# 位置參數（$1）仍然優先於 .env —— 那是刻意的（cron 想臨時換來源機時，
# 命令列比 .env 直觀）。
DEST="${2:-}"
COLLECTION="${3:-laws}"
QDIR="$HOME/qdrant"
LOG="$QDIR/sync.log"
STATE="$QDIR/.sync-state"   # 內容 e.g. "3 laws-xxx.snapshot"
TMP="$QDIR/.sync.tmp.snapshot"
TS="$(date '+%F %T')"
# 認證用兩把 key，不要混：
#   QDRANT_API_KEY      本機自己的 qdrant（backend 查詢用同一把）
#   QDRANT_PEER_API_KEY 同步對象（來源機）的 qdrant —— 必須另外設定
# 過去只有一把，得以運作純粹因為三機共用同一把；一旦任一輪換就變成
# 「本機 200、遠端 401」的不對稱（2026-09-26 MSI 實測踩到）。所以拆開。
# 未設定 QDRANT_PEER_API_KEY 時退回 QDRANT_API_KEY，維持舊的單機設定可用。
#
# ⚠️ PEER_KEY 與 AUTH_H 必須在 _load_env **之後**才求值（見下方）。
#    它們一開始宣告在檔案上方，當下 shell 環境還沒有 .env 的值 →
#    `${QDRANT_PEER_API_KEY:-...}` 會在那一刻被展開並永久凍結；
#    後來 _load_env 載入的新 key 完全不會影響已經算好的 AUTH_H。
#    症狀是「.env 明明是新 key、同步卻一直報 401」（2026-09-26 實測）。
#    真正求值放在 _load_env 呼叫之後，函式宣告可以留在前面（尚未展開）。

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
        log "AUTH 失敗（HTTP ${code}）：只有 QDRANT_API_KEY、沒設 QDRANT_PEER_API_KEY。"
        log "  本機 key 只適用於本機 qdrant；要寫入 $SOURCE 必須另外給對方的 key。"
      else
        log "AUTH 失敗（HTTP ${code}）：QDRANT_PEER_API_KEY 與 $SOURCE 的 qdrant 不符。"
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

# 自動載入 .env（單一真相來源＝repo 根那份，與 compose 同檔）。
# 2026-09-26 收斂：原本呼叫端要自己 source backend/.env，crontab 就得寫
# `set -a; . .../backend/.env; set +a;`，而那份與根 .env 是兩份副本 ——
# QDRANT_API_KEY 曾在兩者間漂移，造成「本機 qdrant 200、遠端 401」。
# 現在腳本自己載入根 .env，crontab 不必再 source。
#
# ⚠️ 只填「尚未在環境中」的變數：per-machine 覆寫（例如 crontab 傳入的
# SRC_API_URL）必須優先於 .env，否則設定會被靜默吃掉（2026-09-26 實測：
# 直接 `set -a; . .env` 會覆蓋呼叫端傳入的值）。
#
# 用 sed 過濾註解與空行後 source。比逐行解析可靠，也不會被「註解裡有 =」騙到。
# ⚠️ 不能把過濾結果管給 `source /dev/stdin`：pipe 會讓 source 把 `KEY=value`
#    當成指令執行（`HOST_ID=msi: invalid variable name`（當時代號是 msi），2026-09-26 實踩），
#    所以先收集成字串、再用 here-string 餵進去。
_load_env() {
  local f body out line k
  for f in "$ROOT/.env" "$ROOT/backend/.env"; do
    [ -f "$f" ] || continue
    body="$(sed -e 's/^[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$f")"
    out=""
    while IFS= read -r line; do
      case "$line" in *=*) ;; *) continue ;; esac
      k="${line%%=*}"
      case "$k" in
        [A-Za-z_]*) ;;
        *) continue ;;
      esac
      # 已存在（含空字串）就不覆寫：尊重呼叫端的 per-machine 設定
      [ -n "${!k+x}" ] || out+="$line"$'\n'
    done <<<"$body"
    [ -n "$out" ] && { set -a; . /dev/stdin <<<"$out"; set +a; }
    return 0
  done
  return 0
}
_load_env

# SOURCE / DEST 在此才求值（同上：.env 這時候才載入）。位置參數優先。
SOURCE="${1:-${LAW_SYNC_SOURCE:-}}"
[ -n "$SOURCE" ] || {
  echo "用法: scripts/sync-snapshot.sh [--force] <source_url> [dest_url] [collection]" >&2
  echo "  source_url 必填：要從哪台機器的 qdrant 拉快照（例 http://<tailscale-ip>:6333）" >&2
  echo "  也可用環境變數 LAW_SYNC_SOURCE 指定（cron／launchd 用這個比較順）。" >&2
  exit 2
}
DEST="${2:-http://${TS_IP:-127.0.0.1}:6333}"

# PEER_KEY / AUTH_H 在此才求值：_load_env 已把 .env 的值載入環境，
# 這樣才拿得到「.env 裡那一把」而不是宣告當下的舊值。
PEER_KEY="${QDRANT_PEER_API_KEY:-$QDRANT_API_KEY}"
AUTH_H=()
[ -n "$PEER_KEY" ] && AUTH_H=(-H "api-key: $PEER_KEY")

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
  #
  # ⚠️ 一定要帶 ?probe=0：/status 預設會再去探測「它的」三台主機，實測要 2.7s，
  # 逼近 -m 8 的上限，cron 常常剛好超時而抓不到（2026-09-26 實測：來源機明明有
  # 版本，log 卻一直記「取不到」）。probe=0 只回本機資訊，0.02s。
  #
  # ⚠️ 2026-10-01：三台全開了 Cloudflare Access（Service Auth），SRC_API_URL
  #    是**公網**網址 → 不帶 Service Token 會被擋成 403，症狀極其安靜：
  #    快照照樣 SYNC OK、只有這裡 log「取不到」，於是 .law_version 凍結在舊版。
  #    （實測：21:59 還寫得進，Access 生效後 22:53 就取不到了。）
  #    與 gateway.py 的 _access_headers() 是同一組值、同一個道理。
  # ⚠️ 用 curl 的 -K（config）而不是 "${arr[@]}" 展開陣列：`set -u` 下展開**空**
  #    陣列在 bash 3.2（macOS 內建，mbp 就是）會直接中斷整支腳本，而這支腳本
  #    在 mbp 上有 launchd 排程。-K 讀 stdin，空字串時 curl 當作沒有額外設定。
  _cf_k=""
  if [ -n "${CF_ACCESS_CLIENT_ID:-}" ] && [ -n "${CF_ACCESS_CLIENT_SECRET:-}" ]; then
    _cf_k="header = \"CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}\"
header = \"CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}\""
  fi
  ver="$(printf '%s' "$_cf_k" | curl -sf -m 8 -K - "$SRC_API/status?probe=0" 2>/dev/null \
        | python3 -c 'import sys,json; print((json.load(sys.stdin).get("law_version") or {}).get("update_date") or "")' 2>/dev/null || true)"
  if [ -z "$ver" ]; then
log "law version: 取不到（src_api=${SRC_API}；來源機的 sync_daily.py 還沒跑過、該網址不通、或 Cloudflare Access 拒絕——後者要查 CF_ACCESS_CLIENT_ID/SECRET 有沒有設）"
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
  # 正規化成 ISO：官方 UpdateDate 是中文格式（「2026/9/18 上午 12:00:00」），
  # 前端要比對新舊、判斷本機是否落後，需要可比較的字串。raw 保留原值供稽核。
  raw="$ver"
  ver_iso="$(printf '%s' "$ver" | python3 -c '
import re, sys
s = sys.stdin.read().strip()
m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
if m:
    y, mo, d = (int(g) for g in m.groups())
    print(f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else "")
else:
    print(s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else "")
')"
  if [ -z "$ver_iso" ]; then
    log "law version 無法解析成 ISO 日期（raw=${raw}），不寫入"
    return 0
  fi
  printf '{"update_date": "%s", "raw": "%s", "source": "%s", "synced_at": "%s"}\n' \
    "$ver_iso" "$raw" "$SOURCE" "$(date '+%F %T')" >"$VERSION_FILE.tmp" \
    && mv "$VERSION_FILE.tmp" "$VERSION_FILE" \
    && log "law version updated: $old -> ${ver_iso}（raw=${raw}）"
}

# 1) source 在線？
#    ⚠️ /healthz **不驗證 API key** —— 這是 qdrant 的設計，實測過三種情形
#    （2026-09-30 mbp 回報、wsl 複驗）：
#        /healthz      正確 key → 200   錯的 key → 200   無 header → 200
#        /collections  正確 key → 200   錯的 key → 401   無 header → 401
#    所以這一步通過**只能**證明「TCP 連得到、那台 qdrant 活著」，不能證明
#    「認證沒問題」。真正的把關是下面第 2 步的 `auth_precheck`（它打
#    /collections 並檢查 401/403），而它確實在這裡之後立刻執行。
#
#    為什麼仍然用 /healthz 而不改打 /collections：那一步的用途是「來源機在不在線，
#    不在就靜默 skip（exit 0，不報錯）」。改成 /collections 會讓「離線」與
#    「key 不對」兩種情況都變成同一個失敗路徑，丟失「離線不算錯」那個行為 ——
#    來源機臨時下線時不該讓本機的排程天天報錯。
#
#    不要把這一步的結果當成認證的證據（2026-09-30 有人因此誤判「認證沒生效」，
#    實際上是測了免驗證的 /healthz）。
if ! curl -sf "${AUTH_H[@]}" -m 5 "$SOURCE/healthz" >/dev/null 2>&1; then
  log "source $SOURCE offline, skip（本機備援資料不受影響）"
  exit 0
fi

# 2) 認證 → 點數 → 版本
#    auth_precheck 才驗認證（打 /collections，401/403 會講清楚是哪一把 key 的問題）。
#    順序有意義：認證先於點數，因為「key 錯了卻拿到 -1 個點」會被誤判成
#    「來源機資料變了」而白白重抓一次快照。
auth_precheck || exit 1
# 5 步會「先刪掉本機 collection 再上傳」，所以刪之前必須先確認**本機**那把
# key 打得開 —— 否則刪完才發現上傳會 401，本機就空到下次同步成功為止。
#
# 這不是假想：QDRANT_PEER_API_KEY 與本機 QDRANT_API_KEY 是兩把，而 PEER_KEY
# 對本機（$DEST）也被拿來用（下面 :263 DELETE / :265 upload）。拆分那把
# （compose.yaml:30 的 QDRANT__SERVICE__ALT_API_KEY）若沒在這台部署好，
# 症狀正是「刪得掉、上傳不進來」。
auth_dest_precheck() {
  code="$(curl -s -m 15 -o /dev/null -w '%{http_code}' "${AUTH_H[@]}" "$DEST/collections/$COLLECTION" 2>/dev/null)"
  case "$code" in
    200|404) return 0 ;;          # 404 = 認證通過、只是還沒有這個 collection
    401|403)
      log "AUTH 失敗（HTTP ${code}）：PEER_KEY 連**本機** $DEST 都打不開。"
      log "  刪掉本機 collection 之後就上傳不進來 → 資料空窗，所以在此中止。"
      log "  多半是 compose.yaml:30 的 QDRANT__SERVICE__ALT_API_KEY 沒在這台部署"
      log "  （QDRANT_PEER_API_KEY 拆分那把的槽）。修：docker compose up -d（會重建）。"
      return 1 ;;
    000) log "本機 qdrant ($DEST) 連不上，放棄"; return 1 ;;
    *) return 0 ;;
  esac
}
auth_dest_precheck || exit 1
SRC_PTS="$(pts_of "$SOURCE" "$COLLECTION")"
PREV_PTS="$(awk '{print $1}' "$STATE" 2>/dev/null || echo "")"
if [ "$FORCE" -eq 0 ] && [ "$SRC_PTS" = "$PREV_PTS" ] && [ -n "$PREV_PTS" ]; then
  sync_law_version
  log "unchanged (${SRC_PTS} points), skip"
  exit 0
fi
[ "$FORCE" -eq 1 ] && log "FORCE: 略過 unchanged 檢查（src_pts=$SRC_PTS prev=${PREV_PTS}）"

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

# 4b) 順手清來源機的其他快照（只留剛下載的這份）——即使後續 restore 失敗也不累積
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
#
# ⚠️ 這一步之後本機是「要嘛有完整資料、要嘛空」，**沒有中間狀態也沒有回滾**。
#    原本上傳失敗只印一行 "restore upload failed" 就 exit，而這台機器的存在
#    意義就是「x570 掛了還能答」—— 那種狀態讓它變成空殼，而且 cron／launchd
#    只看得到非零 exit，不會有人去讀 log。
#    兩道處理（2026-10-01）：
#      a) 刪之前先 auth_dest_precheck（見上）——已知最可能的失敗原因提前擋掉，
#         尤其「PEER_KEY 拆了但這台還沒部署 alt_api_key」那個只在刪完才會發現的坑
#      b) 上傳重試一次（逾時／連線中斷這類暫時性失敗），失敗時明確說明本機已空
#    刻意**不**做「先把本機 collection 快照一份再刪」：laws 39,879 筆的快照是
#    數百 MB，每 10 分鐘多下一次不划算，而真正會讓上傳失敗的原因已在 (a) 擋掉。
curl -sf "${AUTH_H[@]}" -m 30 -X DELETE "$DEST/collections/$COLLECTION" >/dev/null 2>&1 \
  && log "deleted local $COLLECTION" || log "delete local: (原本不存在或失敗)"

UPLOAD_OK=0
for attempt in 1 2; do
  if curl -sf "${AUTH_H[@]}" -m 180 -X POST -F "snapshot=@$TMP" \
      "$DEST/collections/$COLLECTION/snapshots/upload?priority=snapshot" >/dev/null 2>&1; then
    UPLOAD_OK=1
    [ "$attempt" = "2" ] && log "restore upload 成功（第 2 次嘗試）"
    break
  fi
  log "restore upload failed（嘗試 $attempt/2）"
done
if [ "$UPLOAD_OK" != "1" ]; then
  log "✗✗ 本機 $COLLECTION 目前是**空的** —— 舊的已刪、新的上傳不進來。"
  log "  這台現在無法回答查詢；若 x570 同時離線就是三台全空。"
  log "  診斷方向：$DEST 的認證／磁碟空間／qdrant 版本。快照仍在來源機，重跑本腳本即可恢復。"
  log "  已下載的快照保留在 ${TMP}（固定路徑，下次執行會覆寫；要現在重試就直接再跑一次）。"
  exit 1
fi

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