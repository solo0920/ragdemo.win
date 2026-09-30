#!/usr/bin/env bash
# host-doctor — 一次回答「哪台壞了、哪個鍵、要不要輪換」。診斷，只讀，永不印值。
#
# 存在的理由：症狀散在四個地方（git／docker／.env／registry），
# 過去要人工把四份證據湊起來才敢下結論。這支把它收成一份報告。
#
# 紀律（本專案最重要的一條，出過事）：
#   任何 KEY=value 形式的輸出**絕不出現 value**。要顯示就顯示
#   鍵名 ＋ 長度 ＋ sha256 前 12 碼。2026-09-26 三次外洩之一就是診斷指令
#   印出來的值，所以這不是風格偏好。
#   唯一例外是**非憑證**的機器代號（HOST_ID、COLLECTION、版本日期）——
#   沒有它們就答不出「哪台壞了」。那不是憑證值，而且本檔一律不把它們印成
#   KEY=value 形式，只用「鍵名 值」的中文排版。
#
#   證據：--fingerprints 的指紋直接呼叫 env-sync.sh --fingerprints，
#   **不在這裡重算一份**。這裡若另存一份 6 把共用憑證的清單，就是兩份真相，
#   而「少印一把」看起來跟「那台沒設」一樣難查（env-sync.sh 指紋函式註解
#   記的就是這個教訓）。唯一的例外是下面那條輪換規則需要知道兩個鍵名。
#
# 另一條紀律：**沒有資料 ≠ 故障**。api 沒起來的時候查不到心跳，不能報成
# 「三台都離線」—— registry.list_hosts 裡 except Exception: pass 會把
# 「這台連不到共享 pg」也變成空清單，兩者症狀一樣、原因完全不同。
# 所以取不到資料一律 skip（原因寫明），不 fail。
#
# ⚠ bash 3.2 相容（mbp 是 macOS，內建 bash 3.2）：不要用 mapfile／readarray／
#   關聯陣列。用平行索引陣列存結果。
set -euo pipefail

# xtrace 下拒絕：與 env-sync.sh、host-sync.sh 同一道防線、同一種寫法。
case "$-" in
  *x*) echo "host-doctor: refuse to run under xtrace (bash -x leaks secrets)" >&2; exit 1 ;;
esac
case "${SHELLOPTS:-}" in
  *xtrace*) echo "host-doctor: refuse to run under xtrace (SHELLOPTS 含 xtrace)" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${HOST_API_LOCAL:-http://127.0.0.1:8000}"
# registry 的 stale 判定門檻，必須與 registry.py:23 的 REGISTRY_STALE_MIN 同值
# （compose 傳 3）。抄一份在這裡是刻意的：doctor 要在 api 沒起來的時候也能算
# 「心跳該有多舊」，那時沒有 env 可讀。改 compose 那個預設值時要回來改這裡。
STALE_MIN=3
LAW_VERSION_FILE="$ROOT/data/laws/.law_version"
# 快照同步 log 只有「從別台拉快照的備援機」才有（source 機不跑 sync-snapshot.sh，
# 它的 laws 是自己 ingest 進來的）。所以「這台是不是備援」用檔案存在與否判，
# 不用 HOST_ID 白名單 —— 白名單會在加第 4 台時漏掉，然後對 source 機誤報。
SYNC_LOG="$HOME/qdrant/sync.log"
# 快照多久沒成功更新就值得講。sync-snapshot.sh 的 cron 是 */10 分鐘，
# 所以正常情況 synced_at 應該是「小時級」而不是「天級」；6 小時的門檻代表
# 「排程跑了但連續 36 次都沒成功」，遠離 10 分鐘這個尺度，不會誤報。
LAW_SYNC_STALE_H=6
EXIT_OK=0; EXIT_FAIL=1; EXIT_USAGE=2

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'
usage: host-doctor.sh [--json] [-h|--help]
  --json   機器可讀輸出（給未來的 CI／registry 吃）。**同樣不印值**：
           憑證只會以「鍵名 ＋ 長度 ＋ sha12」出現。
  不帶參數 = 人看的報告。
exit: 0 沒有 fail／1 有 fail／2 引數錯誤／1(xtrace) 拒絕執行
EOF
}

JSON=0
while [ $# -gt 0 ]; do
  case "$1" in
    --json) JSON=1; shift ;;
    -h|--help) usage; exit "$EXIT_OK" ;;
    *) printf 'host-doctor: unknown arg: %s\n' "$1" >&2; usage >&2; exit "$EXIT_USAGE" ;;
  esac
done

# 結果收集：平行索引陣列（bash 3.2 沒有關聯陣列）。
N=0
declare -a ST_NAME ST_STAT ST_DET
# $1 name  $2 status(ok|warn|fail|skip)  $3 detail（單行，值已去敏感）
add() {
  # detail 內的換行一律壓成 "；"：JSON 與表格都假設 detail 是單行。
  local d="${3//$'\n'/; }"
  ST_NAME[$N]="$1"; ST_STAT[$N]="$2"; ST_DET[$N]="$d"; N=$((N + 1))
}
FAILS=0
WARNS=0
bump() {
  case "$2" in
    fail) FAILS=$((FAILS + 1)) ;;
    warn) WARNS=$((WARNS + 1)) ;;
  esac
  add "$1" "$2" "$3"
}

host_id_of() {
  [ -f "$ROOT/.env" ] || return 0
  grep -m1 -E '^HOST_ID=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || true
}

# ── 1. git：工作樹乾淨度與目前 ref ───────────────────────────────────────────
ch_repo() {
  if ! git -C "$ROOT" rev-parse --show-toplevel >/dev/null 2>&1; then
    bump repo fail "$ROOT 不是 git repo"; return 0
  fi
  local head br upstream behind ahead dirty
  head="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
  br="$(git -C "$ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || echo '(detached)')"
  dirty="$(git -C "$ROOT" status --porcelain 2>/dev/null || true)"
  # 檔名不是秘密（值才是）。列出來才有用 —— 診斷的第一個問題是「誰改的」。
  if [ -n "$dirty" ]; then
    local names; names="$(printf '%s\n' "$dirty" | cut -c4- | head -12 | tr '\n' ' ')"
    local c; c="$(printf '%s\n' "$dirty" | grep -c . || true)"
    bump repo-state warn "工作樹不乾淨（${c} 項，會擋住 host-sync.sh）: ${names}"
  else
    bump repo-state ok "乾淨"
  fi
  # 落後上游幾個 commit = 「該不該升級」的第一手證據。沒有上游（tag 部署後
  # detached、或還沒設 upstream）不是故障。
  upstream="$(git -C "$ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
  if [ -n "$upstream" ]; then
    behind="$(git -C "$ROOT" rev-list --count "HEAD..${upstream}" 2>/dev/null || echo '?')"
    ahead="$(git -C "$ROOT" rev-list --count "${upstream}..HEAD" 2>/dev/null || echo '?')"
    if [ "$behind" != "0" ] && [ "$behind" != "?" ]; then
      bump upgrade warn "落後 ${upstream} ${behind} 個 commit（跑 host-sync.sh 拉）"
    elif [ "$ahead" != "0" ] && [ "$ahead" != "?" ]; then
      bump upgrade warn "比 ${upstream} 多 ${ahead} 個 commit（這台有別人沒有的東西，別蓋掉）"
    else
      bump upgrade ok "與 ${upstream} 同步"
    fi
  else
    bump upgrade skip "沒有上游可比（分支 ${br} 沒設 upstream，或 HEAD 是 detached）"
  fi
  bump repo-ref ok "HEAD ${head}，分支 ${br}"
}

# ── 2. 工具 ──────────────────────────────────────────────────────────────────
ch_tools() {
  # 變數名大寫的理由見 ch_containers() 裡 ⚠ 那段（env-audit 的 SH_READ 反查）。
  local T MISSING="" OK=""
  for T in git python3 docker sops curl; do
    if command -v "$T" >/dev/null 2>&1; then OK="${OK} ${T}"; else MISSING="${MISSING} ${T}"; fi
  done
  if [ -n "$MISSING" ]; then
    bump tools warn "缺:${MISSING}（有:${OK}）"
  else
    bump tools ok "git python3 docker sops curl 都在"
  fi
  # docker 有這支不代表 daemon 在跑。daemon 沒起來的症狀是
  # 「Cannot connect to the Docker daemon」，而這一句就足以解釋後面所有
  # 容器相關的異常 —— 所以它值得自己一項。
  if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
      bump docker-daemon ok "$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"
    else
      bump docker-daemon fail "daemon 沒在跑（後面所有容器檢查都會失敗）"
    fi
  else
    bump docker-daemon skip "沒有 docker 這支程式"
  fi
}

# ── 3. 三個容器 ──────────────────────────────────────────────────────────────
# 用 compose ps 而不是 docker ps：要看的是「這個專案的三個 service」，
# 不是這台機器上所有容器。--format json 在不同版本可能是 JSON 陣列或
# 每行一個物件，兩種都餵給 python3 解析。
ch_containers() {
  if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    bump containers skip "docker 不可用，無法查容器"
    return 0
  fi
  local out
  # ⚠ 一定要 --all：`docker compose ps` 預設**只看 running**，而「建立過但已停止」
  #   與「從沒建立過」是完全不同的診斷。msi 這三個容器就是 Exited (21h)，
  #   只查 running 會得到空清單，然後被誤讀成「這台從沒部署過」。少了 --all
  #   這一支 doctor 就會在最需要它的情況下說錯話。
  out="$(cd "$ROOT" && docker compose ps --all --format json 2>/dev/null || true)"
  if [ -z "$out" ]; then
    # 空 ≠ 壞。可能是還沒部署過，也可能全部被刪了。
    bump containers warn "這台沒有 ragdemo 的容器紀錄（從沒 up 過，或已被 docker rm）"
    return 0
  fi
  # 每個 service 一項，讓「哪個壞了」可以直接看對應行。State 從欄位拿，
  # 拿不到就看有沒有 Health（有的話 unhealthy 明確是壞的）。
  local summary
  summary="$(printf '%s' "$out" | python3 -c '
import sys, json
# ⚠ 必須「先一次讀完」再判斷格式。`json.load(sys.stdin)` 一旦失敗，stdin 就已被
#   讀走，在 except 裡再 `sys.stdin.read()` 永遠拿到空字串 —— 於是 d=[]，
#   整段容器檢查靜默變成「解析不了」。這是真的踩過：2026-09-27 實測
#   docker compose ps --format json 吐的是 **JSON Lines**（每行一個物件），
#   不是 JSON 陣列，於是每次都走失敗分支。
#   兩種格式都真的存在（不同 compose 版本行為不同），所以兩種都要能吃。
raw = sys.stdin.read()
d = None
try:
    d = json.loads(raw)          # 陣列或單一物件
except Exception:
    try:
        d = [json.loads(l) for l in raw.splitlines() if l.strip()]   # JSON Lines
    except Exception:
        d = None
if d is None:
    sys.exit(0)
if isinstance(d, dict):
    d = [d]
for s in d:
    name = s.get("Service") or s.get("Name") or "?"
    # State 是機器值（exited/running），Status 是人類值（"Exited (0) 21 hours ago"）。
    # 兩個都留：State 給 case 比對，Status 給「多久了」——後者才是診斷需要的。
    state = s.get("State") or "?"
    human = s.get("Status") or ""
    if s.get("Health"):
        state += "/" + s["Health"]
    print(name, state, human)
' 2>/dev/null || true)"
  if [ -z "$summary" ]; then
    # ⚠ 這裡是 fail 不是 skip，而且理由與本檔「沒有資料 ≠ 故障」那條紀律相反：
    #   上游**有**資料（$out 非空，上面擋過了），是我們的 parser 解不開。parser 壞掉
    #   時回報 skip，等於讓「doctor 本身有 bug」看起來像「這台沒資料」—— 真正的壞機
    #   也會顯示 skip，而 skip 在本專案的語意裡是「無辜的」。拿不到的證據要報成拿不到，
    #   不要報成沒問題。
    bump containers fail "docker compose ps 的輸出解析不了（parser 或 compose 版本問題）。$out 有 ${#out} bytes 但解不出 service/State —— 請更新這個 parser，不要當成沒資料"
    return 0
  fi
  # ⚠ while read 的變數名必須大寫。scripts/env-audit.py 的 scan_shell() 靠
  #   SH_READ（`\$\{NAME[:}]` 或 `\$[A-Z][A-Z_0-9]+`）反查「誰讀了什麼環境變數」，
  #   並只扣除「同檔內自己賦值」的名字（比對 SH_ASSIGN）。小寫的 read 變數
  #   因為不符 SH_READ 的第二段（要求全大寫）卻也不在任何賦值節點上 —— 例如
  #   `while read -r svc st human` 是賦值不是 local宣告 —— 於是被判成「讀環境變數」，
  #   假的 `human=`／`mins=`／`empty=` 就被寫進 .env.example。踩過：2026-09-27。
  #   順帶說明：SHELLOPTS 是唯一「真的該被反查到又真的該被忽略」的案例，
  #   它由 bash 繼承、要判 xtrace 卻不該進 .env.example，靠的是 TOOL_ENV 清单。
  while read -r SVC ST HUMAN; do
    [ -n "$SVC" ] || continue
    case "$ST" in
      running*) if [ "${ST#*/}" = "unhealthy" ]; then
        bump "container:$SVC" fail "$ST $HUMAN"
      else
        bump "container:$SVC" ok "$ST $HUMAN"
      fi ;;
      # Exited ≠ 壞掉。刻意不判成 fail：「這台刻意不啟動」是本專案合法的狀態
      # （msi 就是），把它報成 fail 只會讓 doctor 天天紅燈，紅燈久了等於沒燈。
      # 要判斷是刻意還是意外，看 fail/warn 摘要裡的其他項目（容器 fail 一定是意外）。
      exited*|dead*) bump "container:$SVC" warn "$ST $HUMAN" ;;
      *)             bump "container:$SVC" warn "$ST $HUMAN" ;;
    esac
  done <<<"$summary"
}

# ── 4. env-sync --check ──────────────────────────────────────────────────────
# 呼叫既有子命令而不是重算：鍵覆蓋率、總表 schema、per-host 漂移三件事
# 的判斷邏輯都在 env-sync.sh 裡，複製一份必然漂移。
ch_env_check() {
  local out rc=0
  out="$("$ROOT/scripts/env-sync.sh" --check 2>&1)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    bump env-check ok "$(printf '%s' "$out" | tail -1)"
  else
    # 輸出只有鍵名（--check 的設計如此），值不會出現在這裡。
    bump env-check fail "rc=${rc} $(printf '%s' "$out" | tr '\n' ';' | head -c 400)"
  fi
}

# ── 5. 指紋與「要不要輪換」 ─────────────────────────────────────────────────
# 只呼叫 env-sync.sh --fingerprints 既有輸出（鍵名 ＋ 長度 ＋ sha12，無值）。
# 輪換規則只列有根據的幾條，寫在規則旁邊 —— 日後想加規則的人要能看到理由，
# 而不是看到一個沒有來源的 if。
ch_rotate() {
  local out rc=0
  out="$("$ROOT/scripts/env-sync.sh" --fingerprints 2>/dev/null)" || rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$out" ]; then
    bump rotate skip "取不到指紋（.env 不存在，或 env-sync.sh 失敗）"
    return 0
  fi
  # 把指紋表原樣交出去（那是 3 個欄位，不是 KEY=value），人類模式直接顯示。
  # 規則：
  #   MISSING／EMPTY  → 該鍵這台沒有，症狀是 401／心跳失敗，且極難回推是環境變數缺了
  #   peer == self     → 兩把同值即三台鎖步，而 QDRANT_PEER_API_KEY 的值已知外洩
  #                      （ARCHITECTURE.md〈密鑰管理〉／SCOPE.md〈待套用〉）→ 建議輪換
  local MISS="" EMPTY="" SAME=""
  # 迴圈變數刻意叫 FP（fingerprint line）而不是 LINE：`$LINE` 這種全大寫單字
  # 會被 env-audit 的 `$UPPER` 反查誤認（它同時被 while read 賦值、又在本迴圈讀，
  # 而 read 的賦值不在 SH_ASSIGN 的比對範圍內）。踩過：2026-09-27 假的 `LINE=`
  # 被寫進 .env.example。取一個「只有這個迴圈用得到」的名稱最省事。
  while IFS= read -r FP; do
    case "$FP" in
      *MISSING*) MISS="${MISS} $(printf '%s' "$FP" | awk '{print $1}')" ;;
      # 空值與缺鍵要分開報。空＝「設定了但沒給」，在 env-sync 的模型裡是合法狀態
      # （共用層「空值不合併」，各機沿用自己現值；env-sync.sh 註解也記著曾有過
      # 這種狀態）。所以空是 warn 不是 fail —— 報成 fail 會讓這支 doctor 天天紅燈，
      # 而紅燈久了就等於沒有燈。
      *EMPTY*)   EMPTY="${EMPTY} $(printf '%s' "$FP" | awk '{print $1}')" ;;
    esac
  done <<<"$out"
  local PEER SELF
  PEER="$(printf '%s\n' "$out" | awk '$1=="QDRANT_PEER_API_KEY"{for(i=1;i<=NF;i++) if($i ~ /^sha12=/){print $i; exit}}')"
  SELF="$(printf '%s\n' "$out" | awk '$1=="QDRANT_API_KEY"{for(i=1;i<=NF;i++) if($i ~ /^sha12=/){print $i; exit}}')"
  if [ -n "$PEER" ] && [ "$PEER" = "$SELF" ]; then
    SAME="yes"
  fi
  # 數量**從指紋表實際數出來**，不寫死。寫死過（"9 把"／"7 把共用"），而
  # 2026-09-30 把 ZEN_API_KEY 移出 SHARED_SECRETS（7→6 把，連帶 per-host 合計
  # 9→8）之後，這兩行就開始報過時的數字 —— 與本檔第 16 行「不在這裡重算一份
  # 清單」的原則同一件事：數量也是 env-sync.sh 的真相，這裡只負責數。
  local N_TOTAL N_SHARED
  N_TOTAL="$(printf '%s\n' "$out" | awk 'NF && $1 !~ /^#/{n++} END{print n+0}')"
  # 共用層 = 這台應有的那幾把。--fingerprints 的輸出把 per-host 2 把標成
  # "[per-host；...]"，用它數就不必在這裡另存一份 SHARED_SECRETS。
  N_SHARED="$(printf '%s\n' "$out" | awk 'NF && $1 !~ /^#/ && !/per-host；/{n++} END{print n+0}')"
  bump rotate-fp ok "${N_TOTAL} 把憑證的長度＋sha12 已取得（值不顯示）"
  if [ -n "$MISS" ]; then
    bump rotate-key fail "本機缺這些鍵:${MISS}（症狀是 401／心跳失敗，且極難回推是環境變數缺了）"
  else
    bump rotate-key ok "${N_SHARED} 把共用憑證都在（值不顯示，指紋見 rotate-fp）"
  fi
  if [ -n "$EMPTY" ]; then
    bump rotate-empty warn "這些鍵是空值（未設定）:${EMPTY}；共用層空值不合併，各機沿用自己現值"
  fi
  if [ "$SAME" = "yes" ]; then
    bump rotate-hint warn "QDRANT_PEER_API_KEY 與 QDRANT_API_KEY 同值 → 三台鎖步，建議輪換 peer 那把
   （該值已知外洩；拆開的理由見 env-sync.sh PER_HOST_SECRETS 註解與 ARCHITECTURE〈密鑰管理〉）"
  else
    bump rotate-hint ok "peer 與本機 qdrant key 不同值（已拆分，正確）"
  fi
}

# ── 6. registry 心跳 ────────────────────────────────────────────────────────
# 心跳判定用 last_seen 與 STALE_MIN 比，不看 ok 欄位：ok 是「上次心跳時連通了嗎」，
# 對「現在還活著嗎」沒有回答力（那正是 stale 的意義）。
# ⚠ 取不到 /hosts 時一律 skip：list_hosts 裡 except Exception: pass 會把
#   「這台連不到共享 pg」也變成空清單，報成 fail 會把兩個原因混成一個。
ch_registry() {
  local body
  if ! body="$(curl -sf -m 5 "$API_URL/hosts" 2>/dev/null)" || [ -z "$body" ]; then
    bump registry skip "取不到 $API_URL/hosts（api 沒起，或這台連不到共享 pg —— 兩者症狀一樣，原因要分開查）"
    return 0
  fi
  local parsed
  parsed="$(printf '%s' "$body" | python3 -c '
import sys, json, datetime
now = datetime.datetime.now(datetime.timezone.utc)
def age(ts):
    if not ts: return None
    try:
        t = datetime.datetime.fromisoformat(ts)
    except Exception:
        return None
    if t.tzinfo is None: t = t.replace(tzinfo=datetime.timezone.utc)
    return (now - t).total_seconds() / 60.0
try:
    rows = json.load(sys.stdin).get("hosts") or []
except Exception:
    sys.exit(0)
for r in rows:
    a = age(r.get("last_seen"))
    # 刻意不印 ts_ip/machine_id/llm：都不是秘密，但這份報告會被貼進 issue，
    # 少印一個欄位就少一次外洩機會。host_id 與心跳夠回答問題了。
    print(r.get("host_id") or "?", "-" if a is None else ("%.1f" % a), r.get("ok"))
' 2>/dev/null || true)"
  if [ -z "$parsed" ]; then
    bump registry skip "/hosts 回空或解析不了（空清單也可能是共享 pg 連不上，不是「沒人」）"
    return 0
  fi
  local seen_any=0
  while read -r HID MINS OKF; do
    [ -n "$HID" ] || continue
    seen_any=1
    if [ "$MINS" = "-" ]; then
      bump "registry:$HID" warn "有紀錄但 last_seen 為空"
    elif awk "BEGIN{exit !($MINS > $STALE_MIN)}"; then
      bump "registry:$HID" fail "心跳過期 ${MINS} 分（門檻 ${STALE_MIN} 分）"
    else
      bump "registry:$HID" ok "心跳 ${MINS} 分前（門檻 ${STALE_MIN} 分）"
    fi
  done <<<"$parsed"
  [ "$seen_any" -eq 0 ] && bump registry skip "registry 沒有任何主機紀錄"
  return 0
}

# ── 7. 法規版本 ──────────────────────────────────────────────────────────────
# .law_version 這個檔的內容是日期，不是憑證，可以照印。
# 備援機靠 sync-snapshot.sh 寫它、source 機靠 sync_daily 產生，兩條路徑不同，
# 所以「有這個檔」不代表「這台版本是最新的」—— 要比的是 update_date 本身。
ch_law_version() {
  if [ ! -f "$LAW_VERSION_FILE" ]; then
    bump law-version warn "沒有 data/laws/.law_version（備援機靠 sync-snapshot.sh 帶過來；沒有＝快照同步還沒成功過）"
    return 0
  fi
  # ⚠️ `why` 必須列在這行 local 裡，不能只在 case 分支裡 `why="..."`：
  #    case 的分支寫成 `*offline*) why="…"` ，開頭是 `*offline*) ` 而不是 `why=`，
  #    而 env-audit.py 的 SH_ASSIGN 是**行首錨定**的 match，抓不到那個賦值 →
  #    `why` 不算「已宣告」→ 它對 `${why}` 的讀取被誤判成「讀環境變數」→
  #    `.env.example` 多出一個 `why=`，CI 的「.env.example == --template」紅燈。
  #    這是同一個假陽性家族的第三例（前兩例：while read 目標、for 迴圈變數，
  #    都記在 env-audit.py 的註解裡）。正解是宣告變數，不是去改稽核器。
  local v synced age_h lastline why=""
  v="$(python3 -c 'import sys,json
try: print(json.load(open(sys.argv[1])).get("update_date") or "")
except Exception: print("")' "$LAW_VERSION_FILE" 2>/dev/null || true)"
  synced="$(python3 -c 'import sys,json
try: print(json.load(open(sys.argv[1])).get("synced_at") or "")
except Exception: print("")' "$LAW_VERSION_FILE" 2>/dev/null || true)"
  if [ -z "$v" ]; then
    bump law-version warn "data/laws/.law_version 存在但讀不到 update_date（壞掉的 JSON）"
    return 0
  fi
  # ── 陳舊度：update_date 只說「這批法規是哪天的」，不說「本機還在更新」──
  # 2026-09-27 實測踩到：x570 離線、sync-snapshot.sh 每 10 分鐘記一次
  # 「source offline, skip」，但 .law_version 仍留著舊的 synced_at，而原本的
  # 檢查只看檔案在不在 → 報 law-version ok。於是 doctor 說「沒有 fail」，
  # 實際上 RAG 答的是 9 天前的法規，而且**沒有任何症狀**。這比壞掉更糟。
  #
  # 只在這台是「從別台拉快照的備援機」時才判（sync log 存在才成立）。
  # source 機的 synced_at 語意不同（自己 ingest），拿同一條規則去判會誤報 ——
  # 而 source 機此刻離線，無法實測，所以寧可漏判也不亂報。
  if [ -f "$SYNC_LOG" ] && [ -n "$synced" ]; then
    # ⚠️ synced_at 由 sync-snapshot.sh 的 `date '+%F %T'` 產生：**無時區標記的
    #    本機時間**（2026-09-27 實測：msi 是 CST +0800，寫出 "00:40:05" 其實是
    #    16:40 UTC）。所以不能把 naive 當 UTC —— 那會**少算 8 小時**，
    #    6 小時的門檻實際變成 14 小時（第一版就這樣寫錯過，實測報 13h 而非 21.9h）。
    #    正確做法是**兩邊都用本機時間**，同框相減，不去猜時區。
    #    若日後有人改成帶 offset 的 ISO 字串，fromisoformat 會回 aware，
    #    這裡分兩條路處理，不要讓 TypeError 被吞掉變成「靜默跳過檢查」。
    age_h="$(python3 -c 'import sys,datetime
try:
    t=datetime.datetime.fromisoformat(sys.argv[1])
    if t.tzinfo is None:
        age=(datetime.datetime.now()-t).total_seconds()      # naive ↔ 本機
    else:
        age=(datetime.datetime.now(datetime.timezone.utc)-t).total_seconds()
    print(int(age//3600))
except Exception: print("")' "$synced" 2>/dev/null || true)"
    if [ -n "$age_h" ] && [ "$age_h" -ge "$LAW_SYNC_STALE_H" ]; then
      lastline="$(tail -1 "$SYNC_LOG" 2>/dev/null || true)"
      case "$lastline" in
        *offline*) why="；同步 log 最後一行說來源離線（${lastline##*] }）" ;;
        *) why="；同步 log 最後一行：${lastline:-（讀不到）}" ;;
      esac
      bump law-version warn "快照已 ${age_h} 小時沒成功更新（synced_at=${synced}，門檻 ${LAW_SYNC_STALE_H} 小時）——本機 qdrant 仍可查，但法規版本是 ${v} 的，**不是最新的**${why}"
      return 0
    fi
  fi
  if [ -n "$v" ]; then
    if [ -n "$synced" ]; then
      bump law-version ok "data/laws/.law_version = ${v}（synced_at=${synced}）"
    else
      bump law-version ok "data/laws/.law_version = ${v}"
    fi
  fi
}

# ── 跑全部檢查 ───────────────────────────────────────────────────────────────
# ⚠ 這七個 ch_* 各只能呼叫一次。2026-09-27 曾在這裡把整段複製過一份（貼上時重複貼），
# 症狀是每個檢查跑兩次：報告整段印兩遍、`--json` 條目數double、warn 計數翻倍。
# 「JSON 合法」抓不到這種 bug —— 條目重複仍然是合法 JSON。要驗的是**條目數**，
# 不是能不能 parse。修法就是不要複製這段；新增檢查請加在這裡一次。
ch_repo
ch_tools
ch_containers
ch_env_check
ch_rotate
ch_registry
ch_law_version

# ── 輸出 ─────────────────────────────────────────────────────────────────────
# JSON 模式與人模式走**同一份**結果陣列，所以兩個模式不可能對同一件事講不同的話。
# ⚠ --json 同樣不印值：明細裡本來就只有鍵名／長度／sha12／日期／檔名。
# ⚠ JSON 組裝刻意不在 bash 裡手拼字串。細節文字裡有 `（`、`;`、路徑，
#   手拼必然在某個檔名帶引號時產出壞掉的 JSON —— 而「壞掉的 JSON」對
#   CI 來說等於「壞掉的診斷」，比沒有更糟。改成以 \x1f 分隔餵給 python3，
#   讓 json.dumps 負責所有跳脫。\x1f 是控制字元，實務上不可能出現在路徑裡。
emit_json() {
  local i
  {
    printf '%s\x1f%s\x1f%s\x1f%s\n' "header" "host_id" "" "$(host_id_of)"
    printf '%s\x1f%s\x1f%s\x1f%s\n' "header" "api_url" "" "$API_URL"
    printf '%s\x1f%s\x1f%s\x1f%s\n' "header" "commit" "" \
      "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo "")"
    for i in $(seq 0 $((N - 1))); do
      printf '%s\x1f%s\x1f%s\x1f%s\n' "check" "${ST_NAME[$i]}" "${ST_STAT[$i]}" "${ST_DET[$i]}"
    done
  } | python3 -c '
import json, sys
# 逐行讀而不是 json.loads 整份：明細裡的 \x1f 由於是控制字元，實務上不會出現，
# 而且就算出現也只會壞掉那一筆，不會讓整份報告變成垃圾。
meta, checks = {}, []
for line in sys.stdin.read().splitlines():
    if not line:
        continue
    parts = line.split("\x1f")
    if len(parts) != 4:
        continue
    kind, name, status, detail = parts
    if kind == "header":
        meta[name] = detail
    else:
        checks.append({"name": name, "status": status, "detail": detail})
out = {
    "tool": "host-doctor",
    "host_id": meta.get("host_id", ""),
    "api_url": meta.get("api_url", ""),
    "commit": meta.get("commit", ""),
    "summary": {
        "fail": sum(1 for c in checks if c["status"] == "fail"),
        "warn": sum(1 for c in checks if c["status"] == "warn"),
        "ok": sum(1 for c in checks if c["status"] == "ok"),
        "skip": sum(1 for c in checks if c["status"] == "skip"),
    },
    "checks": checks,
}
json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
print()
'
}

emit_human() {
  printf '=== host-doctor（本機 %s）===\n' "$(host_id_of)"
  printf 'repo %s  api %s\n' "$ROOT" "$API_URL"
  local i
  for i in $(seq 0 $((N - 1))); do
    case "${ST_STAT[$i]}" in
      ok)   printf '  [ ok ] %-18s %s\n' "${ST_NAME[$i]}" "${ST_DET[$i]}" ;;
      warn) printf '  [warn] %-18s %s\n' "${ST_NAME[$i]}" "${ST_DET[$i]}" ;;
      fail) printf '  [FAIL] %-18s %s\n' "${ST_NAME[$i]}" "${ST_DET[$i]}" ;;
      *)    printf '  [skip] %-18s %s\n' "${ST_NAME[$i]}" "${ST_DET[$i]}" ;;
    esac
  done
  printf '\n-- 結論 --\n'
  if [ "$FAILS" -gt 0 ]; then
    printf '  %s 項 fail。照順序修：先 docker daemon，再 env-sync --check，再 registry。\n' "$FAILS"
  else
    printf '  沒有 fail。'
  fi
  [ "$WARNS" -gt 0 ] && printf '（%s 項 warn 見上）' "$WARNS"
  printf '\n'
  printf '  值一律不顯示；要比對三台用 scripts/env-sync.sh --fingerprints（鍵名＋長度＋sha12）。\n'
}

if [ "$JSON" -eq 1 ]; then emit_json; else emit_human; fi
[ "$FAILS" -gt 0 ] && exit "$EXIT_FAIL"
exit "$EXIT_OK"
