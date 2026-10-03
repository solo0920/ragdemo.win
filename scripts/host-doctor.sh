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
#   **不在這裡重算一份**。這裡若另存一份 8 把共用憑證的清單，就是兩份真相，
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
# **角色**判斷（source 還是備援）：law-update-worker.sh 用的是這一個檔
# （有 → source，沒有 → 備援）。與 SYNC_LOG 分開宣告，因為兩者回答的是
# 不同的問題，而且各有失效模式：SYNC_LOG 可能在排程還沒跑過時就不存在
# （那時 ROLE 判斷會說錯話），.law_sync.json 則是 ingest 真的跑過才有。
LAW_SYNC_FILE="$ROOT/data/laws/.law_sync.json"
# ⚠️⚠️ 2026-10-02 修正原本的理由 —— **那個前提是錯的**。
#
# 原本寫：「sync-snapshot.sh 的 cron 是 */10 分鐘，所以正常情況 synced_at 應該是
# 『小時級』而不是『天級』；6 小時的門檻代表『排程跑了但連續 36 次都沒成功』」。
#
# 但 `sync-snapshot.sh:185` 是：
#     if [ "$old" = "$ver" ]; then log "law version unchanged ($ver)"; return 0; fi
# —— **版本沒變就直接 return，不寫 synced_at**（註解明說是為了「避免每 10 分鐘動
# 一次 mtime」）。
#
# 所以 **`synced_at` 是「上次法規版本變了」的時間戳，不是「上次跑了」的時間戳**。
# 上游只要沒發新版，它就永遠不動 —— 而那正是**正常狀態**。
# 拿它的年齡當健康信號，結果是**結構性的假警報**：mbp 2026-10-02 回報的
# 「快照已 25 小時沒成功更新」就是這個，**不是 mbp 的問題**。
#
# 真正的健康信號是「排程有沒有在跑」，那要看 **log 的最後時間戳**，不是檔案裡的
# 欄位。判斷已改（見 ch_law_version）。這裡保留門檻，意義變成「log 靜默多久」：
# cron 是 */10 分鐘，靜默 6 小時（= 36 個週期）才值得講，這個尺度是對的。
LAW_SYNC_STALE_H=6

# ── 共用：naive 時間戳的年齡（小時）─────────────────────────────────────────
# ⚠️ 抽成共用函式，因為今天有**兩個地方各寫一次，其中一份寫錯了** ——
#    而寫錯的那份正是**今天新寫的**。同一件事只能有一個實作。
#
# `sync-snapshot.sh` 用 `date '+%F %T'`、`sync_daily.py` 寫 ISO 而無 offset，
# **兩者都是本機時間的 naive 字串**（2026-09-27 實測：wsl 是 CST +0800，寫出
# "00:40:05" 其實是 16:40 UTC）。
#
# 把 naive 當 UTC 會**少算整個 UTC offset**（本機 8 小時）。實測同一個值：
#     正確（naive ↔ 本機）: 71.7 小時
#     當成 UTC          : 63.7 小時      ← 差 8.0，就是 CST 的 offset
# 門檻 6 小時時，6h 實際變成 14h —— **檢查會晚 8 小時才響**。ch_law_version 的
# 註解裡記著「第一版就這樣寫錯過」；而 ch_source_sync 又犯了一次。
#
# 規則：**兩邊用同一個時區相減，不要猜**。naive ↔ 本機；aware ↔ UTC。
# 若日後改成帶 offset 的 ISO，fromisoformat 會回 aware，兩條路都處理 ——
# 不要讓 TypeError 被吞掉變成「靜默跳過檢查」，那比報錯更糟。
age_h_local() {
  python3 -c 'import sys,datetime as d
try:
    t=d.datetime.fromisoformat(sys.argv[1].strip())
    if t.tzinfo is None:
        age=(d.datetime.now()-t).total_seconds()          # naive ↔ 本機
    else:
        age=(d.datetime.now(d.timezone.utc)-t).total_seconds()
    print(f"{age/3600:.1f}")
except Exception:
    print("")' "$1" 2>/dev/null || true
}

# ── 共用：同步 log 最後一筆距今幾小時；讀不到就印空字串 ────────────────────
# 這是「排程有沒有在跑」唯一的可靠證據 —— 檔案裡的 synced_at 只在版本變化時更新。
law_log_last_attempt_h() {
  local lg f="" t=""
  for lg in "$SYNC_LOG" "$ROOT/data/laws/sync.log"; do
    if [ -f "$lg" ]; then f="$lg"; break; fi
  done
  [ -n "$f" ] || return 0
  t="$(tail -60 "$f" 2>/dev/null \
       | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}(:[0-9]{2})?' \
       | tail -1)"
  [ -n "$t" ] || return 0
  age_h_local "$t"
}
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
  #   與「從沒建立過」是完全不同的診斷。wsl 這三個容器就是 Exited (21h)，
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
      # （wsl 就是），把它報成 fail 只會讓 doctor 天天紅燈，紅燈久了等於沒燈。
      # 要判斷是刻意還是意外，看 fail/warn 摘要裡的其他項目（容器 fail 一定是意外）。
      exited*|dead*) bump "container:$SVC" warn "$ST $HUMAN" ;;
      *)             bump "container:$SVC" warn "$ST $HUMAN" ;;
    esac
  done <<<"$summary"
}

# ── 3b. 執行中的程式碼 vs 工作區（2026-10-02 加，x570 實測踩到才補）────────
#
# 為什麼要有這道：x570 的後端**長達數天**跑著 2026-09-30 拆分重構**之前**的
# 架構 —— 容器裡沒有 `gateway.py`／`retrieve.py`／`cn_parse.py`／`common/`，
# `rag.py` 是 85KB 的舊單體版而工作區是 46KB 拆分版。
#
# 它為什麼一路綠：舊版本**自洽**，所以 /health、/query、peer 探測、registry
# 心跳全部正常。而當時 `ch_repo` 回「乾淨」、`ch_containers` 回三個 running、
# `ch_env_check` 回一致 —— **每一道現有的檢查都是綠的**，因為它們全部只看
# 「repo 乾淨嗎」「容器跑著嗎」「設定對嗎」，沒有一道問「容器裡跑的是不是
# 這個 repo」。`ch_repo` 的「與上游同步」講的是 git，不是映像。
#
# 為什麼會踩到：`docker compose up -d` 只 Recreate 容器（換環境變數），
# **不重建映像**（`--build` 才重建）。所以 pull 完程式碼、跑 up -d，
# 會得到「新環境變數 ＋ 舊程式碼」的混合體 —— 而且完全沒有症狀。
#
# 判準是**內容**，不是時間戳：比對 backend/app 下每個 .py 的 sha256。
# 時間戳（image Created vs commit date）會被時區/git 設定搞錯，而且
# 「從舊 checkout 重建」時間戳會是新的而內容是舊的 —— 正好漏掉最壞的情況。
ch_code_drift() {
  local hash_cmd="" ws ctr
  # 雜湊工具：Linux 是 sha256sum，macOS（mbp）只有 shasum -a 256。
  if command -v sha256sum >/dev/null 2>&1; then
    hash_cmd="sha256sum"
  elif command -v shasum >/dev/null 2>&1; then
    hash_cmd="shasum -a 256"
  else
    bump code-drift skip "這台沒有 sha256sum／shasum，比不了"
    return 0
  fi
  # 容器沒跑就別比（ch_containers 已經報過那件事，這裡不重複報）
  if ! docker compose -f "$ROOT/compose.yaml" ps --status running --services 2>/dev/null \
       | grep -qx api; then
    bump code-drift skip "api 容器沒在跑，沒有可比對的程式碼"
    return 0
  fi
  # Dockerfile 只 COPY app/，所以 app/**/*.py 就是完整的不變量。
  # 兩邊都相對 app/ 列出，所以路徑形式一致（/app/app → app）。
  ws="$(cd "$ROOT/backend" && find app -name '*.py' -type f | LC_ALL=C sort \
       | xargs $hash_cmd 2>/dev/null || true)"
  ctr="$(docker compose -f "$ROOT/compose.yaml" exec -T api \
          sh -c 'cd /app && find app -name "*.py" -type f | LC_ALL=C sort \
                 | xargs sha256sum' 2>/dev/null || true)"
  if [ -z "$ws" ] || [ -z "$ctr" ]; then
    bump code-drift warn "比對不了（工作區或容器沒讀到檔案清單）—— 可能是 api 剛啟動"
    return 0
  fi
  if [ "$ws" = "$ctr" ]; then
    local n; n="$(printf '%s\n' "$ws" | grep -c . || true)"
    bump code-drift ok "容器內 app/ 的 ${n} 個 .py 與工作區逐位元相同"
    return 0
  fi
  # 不一致：把差異講清楚。只印**檔名**，不印雜湊值以外的任何東西 ——
  # 這些是 .py 檔名，不是憑證。
  local only_ws only_ct
  only_ws="$(comm -23 <(printf '%s\n' "$ws" | cut -c67- | LC_ALL=C sort) \
                      <(printf '%s\n' "$ctr" | cut -c67- | LC_ALL=C sort) \
             | tr '\n' ' ' || true)"
  only_ct="$(comm -13 <(printf '%s\n' "$ws" | cut -c67- | LC_ALL=C sort) \
                      <(printf '%s\n' "$ctr" | cut -c67- | LC_ALL=C sort) \
             | tr '\n' ' ' || true)"
  local detail="容器裡跑的 app/ 與工作區不一致"
  [ -n "$only_ws" ] && detail="${detail}；只在工作區有: ${only_ws}"
  [ -n "$only_ct" ] && detail="${detail}；只在容器有: ${only_ct}"
  detail="${detail}。**這台後端在跑舊程式碼** —— 每個健康檢查都會是綠的，因為舊版本自洽。修法: docker compose up -d --build api"
  bump code-drift fail "$detail"
}

# ── 4. env-sync --check ──────────────────────────────────────────────────────
# 呼叫既有子命令而不是重算：鍵覆蓋率、總表 schema、per-host 漂移三件事
# 的判斷邏輯都在 env-sync.sh 裡，複製一份必然漂移。
# ── 檢查 4b. 「工具存在」與「工具能用」是兩件事 ─────────────────────────
#
# 2026-10-02：`ch_tools` 只用 `command -v` 確認 sops **執行檔**在，那**證明不了
# 任何事** —— 私鑰不在、recipient 對不上、`$SOPS_AGE_KEY_FILE` 指錯，一樣是
# 「sops 在」而 `pull` 會失敗。而這是本專案反覆在收的那一型（2026-10-02 的
# 逐鍵比對指紋、版面指紋，都是同一個道理：證明**形狀**不等於證明**行為**）。
#
# 所以這裡做的是**真的解密一次**，而且把值**立刻銷毀**：
#   * 不印值（只印長度）
#   * 不留在磁碟（`shred -u`，macOS 沒有 shred 就用 `rm -f`）
#   * 失敗時把 sops 的訊息帶回來 —— 「解不開」與「不能解」是兩件事
# ── 檔案權限，跨 BSD/GNU ────────────────────────────────────────────────
# ⚠️ 2026-10-02 實測踩到：`stat -f '%Lp' || stat -c%a` 這個寫法在 **Linux** 上
# 是錯的 —— GNU 的 `stat -f` 不是「filesystem」，是**檔案系統狀態**，於是它
# **成功**了（exit 0）並印出整份 filesystem 報告，短路讓 `||` 的 fallback
# 永遠不會執行。那不是報錯，是**印出一堆不相干的東西並看起來像通過**。
#
# 反過來 macOS 的 `stat -c` 不存在。所以必須**先探測哪個旗標可用**，而不是
# 靠 `||` 串（`||` 只在「前者失敗」時才走，而這裡前者是「成功但答非所問」）。
perm_of() {
  local f="$1"
  if stat -c '%a' "$f" >/dev/null 2>&1; then
    stat -c '%a' "$f" 2>/dev/null
  elif stat -f '%Lp' "$f" >/dev/null 2>&1; then
    stat -f '%Lp' "$f" 2>/dev/null
  else
    echo '?'
  fi
}

# ── 可攜的 timeout ──────────────────────────────────────────────────────
# ⚠️ 2026-10-02 實測踩到：**macOS 沒有 `timeout`**（那是 GNU coreutils 的，
# macOS 上通常也沒有 `gtimeout`）。我寫 `timeout 60 sops ...` 時沒查，結果在
# mbp 上 `command not found` → rc=127 → 那條檢查**永遠 fail**，而且 fail 的
# 理由看起來像「sops 解不開」，實際是「指令不存在」。
#
# 那是本專案第三次只會在 macOS 炸的錯誤（前面兩個：`$VAR（全形`、GNU 的
# `stat -f`）。**前兩個是引號／旗標的問題，這個是「指令是否存在」的問題 ——
# 而我現有的守衛（`test_bash32_fullwidth.py`）只查引號，涵蓋不到。**
#
# 處置順序：系統的 `timeout` → `gtimeout`（coreutils）→ 沒有就**跑但自己計時**。
# 最後那條很重要：寧可沒有上限，也不能因為「沒有 timeout」就不檢查 ——
# 那會讓 sops 在卡住時把整支腳本拖死。
run_with_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
  else
    "$@"                                   # 沒有 timeout 指令就跑；見上方說明
  fi
}

ch_sops() {
  command -v sops >/dev/null 2>&1 || { bump sops skip "沒有 sops"; return; }
  command -v age >/dev/null 2>&1 || { bump age skip "沒有 age（sops 的預設收件人解密需要它）"; }

  local enc="$ROOT/settings/env/secrets.common.enc.env"
  [ -f "$enc" ] || { bump sops skip "沒有加密檔 ${enc##*/}（尚未 --init-secrets）"; return; }

  # 私鑰：SOPS_AGE_KEY_FILE > 預設位置。**不印路徑以外的任何東西**。
  local keyf="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
  if [ -r "$keyf" ]; then
    # ⚠️ `${keyf}（` 不是 `$keyf（` —— 後面緊接全形括號時，macOS 的 bash 3.2
    # 會把全形當成變數名的一部分（找 `keyf（` 這個變數）→ `set -u` 下整支死掉。
    # 本專案在 `env-sync.sh:561` 為此踩過，`tests/test_bash32_fullwidth.py`
    # 守著。**一律寫 `${VAR}`。**
    bump age-privkey ok "${keyf}（$(wc -l < "$keyf" | tr -d ' ') 行, mode $(perm_of "$keyf")）"
  else
    bump age-privkey fail "讀不到 age 私鑰：$keyf"
    bump sops warn "沒有私鑰就不可能解密；§12b 災難復原會失敗"
    return
  fi

  # ⚠️ 挑一個**一定存在**的鍵。寫死鍵名會漂 —— 改成從加密檔的檔頭取第一個鍵。
  local key
  key="$(grep -m1 -E '^[A-Za-z_][A-Za-z_0-9]*=' "$enc" | cut -d= -f1)"
  [ -n "$key" ] || { bump sops warn "加密檔裡找不到任何鍵名，無法試解密"; return; }

  local tmp rc=0
  tmp="$(mktemp "${TMPDIR:-/tmp}/ragdemo-sopsprobe.XXXXXX")"
  chmod 600 "$tmp"
  if out="$(SOPS_AGE_KEY_FILE="$keyf" run_with_timeout 60 sops --decrypt --extract "[\"$key\"]" "$enc" 2>&1)"; then
    bump sops ok "真的解密成功（試 ${key}，len=${#out}）"
    # out 已在記憶體；把它寫到 tmp 只為量長度是多余的，直接銷毀 tmp。
    :
  else
    rc=$?
    bump sops fail "解密 ${key} 失敗 rc=${rc}：$(printf '%s' "$out" | tr '\n' ';' | head -c 300)"
  fi
  unset out
  if command -v shred >/dev/null 2>&1; then shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  else rm -f "$tmp"; fi
}

# ── 檢查 4c. qdrant 的**兩個**讀寫槽 ────────────────────────────────────
#
# 2026-10-02 實測教訓：`QDRANT__SERVICE__API_KEY` 有、`QDRANT__SERVICE__ALT_API_KEY`
# 沒有，是**可以正常運作**的狀態 —— 本機查詢全對，只有 `sync-snapshot.sh` 跨機
# 寫入時 401。而症狀是「某台的同步靜默失敗」，不會有人聯想到 qdrant 少了第二把
# key。所以這裡直接看**容器裡實際收到的值**（只印長度），不看 `.env` ——
# `.env` 有值不代表容器收到（`up -d` 才會 Recreate）。
ch_qdrant_keys() {
  local cid
  cid="$(docker compose -f "$ROOT/compose.yaml" ps -q qdrant 2>/dev/null | head -1)"
  [ -n "$cid" ] || { bump qdrant-keys skip "qdrant 容器沒在跑"; return; }
  local api alt
  api="$(docker exec "$cid" sh -c 'echo "${#QDRANT__SERVICE__API_KEY}"' 2>/dev/null)"
  alt="$(docker exec "$cid" sh -c 'echo "${#QDRANT__SERVICE__ALT_API_KEY}"' 2>/dev/null)"
  case "$api" in ''|*[!0-9]*) bump qdrant-keys fail "讀不到 API_KEY 長度（容器沒起來？）"; return ;; esac
  if [ "${api:-0}" -eq 0 ]; then
    bump qdrant-keys fail "QDRANT__SERVICE__API_KEY 是空的 —— 本機 qdrant 沒有認證"
  else
    bump qdrant-api-key ok "API_KEY len=${api}"
  fi
  case "$alt" in ''|*[!0-9]*) bump qdrant-keys warn "讀不到 ALT_API_KEY 長度"; return ;; esac
  if [ "${alt:-0}" -eq 0 ]; then
    # ⚠️ **不是 fail**：本機查詢不需要它（見函式上方）。但一定要講清楚缺了會壞什麼。
    bump qdrant-alt-key warn "ALT_API_KEY 是空的 → 這台**不能**作為 sync-snapshot 的寫入端（跨機會 401）。本機查詢不受影響。"
  else
    bump qdrant-alt-key ok "ALT_API_KEY len=${alt}"
  fi
}

# ── 檢查 4d. readiness 與「真的打一次查詢」 ────────────────────────────
#
# ⚠️⚠️ **這是本專案目前最大的盲點，而 `/health` 抓不到。**
#
# 2026-10-02 實測：刪掉 `OLLAMA_URLS` 之後 `/health` **全程回 200**，而
# `POST /query` 全部 500（`httpx.ConnectError: ollama unreachable`，掛在
# `/api/embed`）。是後來手動打了一次查詢才發現整條 RAG 已經斷了。
#
# `/health` 回 200 是**設計如此**（它的語意是「進程活著且可達」，前端同儕探測
# 依賴那個語意 —— 見 `frontend/src/routes/api/[...path]/+server.ts` 的 guard）。
# 所以**不能**把依賴探測塞進 `/health`，要另外有 `/ready` 與真查詢。
# ── API 身分與 port 占用者（2026-10-03，mbp 回報的真實事故）───────────────
#
# ⚠️ 為什麼需要：`ch_readiness` 只看 HTTP code。2026-10-03 mbp 上有另一個程式
# （Homebrew 的 `omlx-server`，MLX 推論伺服器）**搶先綁定 8000**，我們的 api
# 容器之後 publish **沒有報錯但被遮蔽**（OrbStack 的 port 轉發是 userspace proxy，
# 搶輸時不報錯）。於是：
#
#   · `localhost:8000/ready` → **404**（那是 omlx 的回應）
#   · `localhost:8000/query`  → **404**
#   · 而容器內的 api **完全健康**（`code-drift` 仍綠，因為它是 docker exec）
#
# doctor 報的是「readiness 404 / query 404」—— **症狀，不是原因**。人會去查
# 「為什麼 /ready 沒了」，而真正的事實是「這個 port 上根本不是本專案的 API」。
#
# 更嚴重的是 blast radius：`cloudflared` 的 `service: http://localhost:8000`
# 讓 `api-mbp.ragdemo.win` **對外服務的是 omlx**，而 mbp 在 Pages worker 的
# `API_ORIGINS` 輪詢清單裡 —— worker failover 到它會拿到錯的程式，
# 症狀是「200 但內容不對」，比 502 更難查。
#
# 所以加兩條：**port 是誰的**（指名），以及 ** responding 的是不是我們的**（認身分）。

# 我們的 /health 一定有的欄位 —— 拿來認出身分。omlx 回的是
# {"status":"healthy","default_model":null,"engine_pool":{…}}，兩個都沒有。
API_IDENTITY_FIELDS='collection|host_id|"host"'

_api_port() {
  # 從 API_URL 取 port（預設 8000）。不用複雜解析 —— 這只是要一個數字。
  printf '%s' "${API_URL##*:}" | tr -dc '0-9' | head -c 5
  [ -n "$(printf '%s' "${API_URL##*:}" | tr -dc '0-9' | head -c 5)" ] || printf '8000'
}

ch_api_identity() {
  # ** responding 的到底是不是本專案的 API。**
  #
  # 這不是「多檢查一次」而是**換一個判準**：原來只看 HTTP code，而別的程式
  # 完全可以回 200（omlx 的 /health 就是 200）。所以要比對**內容**。
  local port body code
  port="$(_api_port)"
  body="$(curl -s -m 15 -o /tmp/.id.$$ -w '%{http_code}' "${API_URL}/health" 2>/dev/null)" || body=""
  if [ -z "$body" ] || [ "$body" = "000" ]; then
    bump api-identity skip "打不到 ${API_URL}/health（api 沒起來？）"
    rm -f "/tmp/.id.$$"; return
  fi
  if grep -Eq "$API_IDENTITY_FIELDS" "/tmp/.id.$$" 2>/dev/null; then
    bump api-identity ok "回應含本專案的欄位（collection／host_id）—— 確認是 ragdemo 的 API"
    rm -f "/tmp/.id.$$"; return
  fi
  # ⚠️ 走到這裡 = port 上是**別的東西**。把它的樣子帶回去（截斷，不整份貼）。
  local peek=""
  peek="$(head -c 160 "/tmp/.id.$$" 2>/dev/null | tr -d '\n' | tr -s ' ')"
  bump api-identity fail "HTTP ${body}，但**回應裡沒有本專案的欄位** → 這個 port 上**不是** ragdemo 的 API。看到的內容：${peek:-（空）}　處置：看下一項 api-port-owner 是誰佔著"
  rm -f "/tmp/.id.$$"
}

ch_api_port_owner() {
  # **誰佔著 API 的 port。** 有了名字才能行動（否則只能猜）。
  #
  # 可攜性：macOS 與 Linux 都用 `lsof`；`ss` 只有 Linux 有。這正是本專案踩過的
  # 「指令存在性」那一類（`timeout` 只有 GNU coreutils 有）—— 所以兩個都試。
  #
  # ⚠️ 但還有**第三種情況**別忘了：WSL／Docker 的 port 轉發跑在**另一個
  # namespace**，所以那台上 `lsof` **跑得掉但看不到任何東西**，
  # 而 `ss -ltn` 看得到監聽、`ss -ltnp` 的 process 欄卻是空的。
  # 2026-10-03 在 wsl 實測就是這個樣子。
  #
  # 所以**不能把「看不到」都說成「沒有 lsof／ss」** —— 那會讓人去裝工具，
  # 而真正的事實是「有人在聽，只是這個 namespace 指不到」。
  local port named="" listeners="" how=""
  port="$(_api_port)"

  if command -v lsof >/dev/null 2>&1; then
    how="lsof"
    # ⚠️ `|| true` 不是多餘的：**lsof 沒有相符時回 1**，`set -o pipefail` 會把
    # 那個 1 傳成整條管線的結果 → 指派那行回 1 → `set -e` **整支腳本死掉**。
    # 症狀是「stdout 零行、stderr 零行、exit 1」，完全看不出是哪一行。
    named="$( { lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true; } \
               | tail -n +2 | awk '{print $1" (pid "$2")"}' | sort -u | tr '\n' ' ' || true)"
  fi

  if [ -z "$named" ] && command -v ss >/dev/null 2>&1; then
    [ -n "$how" ] || how="ss"
    listeners="$( { ss -ltn 2>/dev/null || true; } \
                  | awk -v p=":$port\$" '$4 ~ p {print $4}' | sort -u | tr '\n' ' ' || true)"
    if [ -n "$listeners" ]; then
      # ⚠️⚠️ **必須要求 NF>=7**。2026-10-03 在 wsl 實測：`ss -ltnp` 那一行只有
      # **5 個欄位**（沒有 process 資訊，因為 port 轉發在別的 namespace），
      # 而 `$NF` 會抓到**最後一個有值的欄位** —— 也就是 peer address
      # `0.0.0.0:*`。於是它被當成「監聽者名字」印出來。
      #
      # **症狀是垃圾進 → 看起來很合理的錯輸出**（一句話解釋了 8000 是誰在用，
      # 而那是錯的）。這比報錯更糟，因為它會讓人照著錯的名字去查。
      # 有 process 時欄位是 7 個：State/Recv-Q/Send-Q/Local/Peer/Process。
      named="$( { ss -ltnp 2>/dev/null || true; } \
                 | awk -v p=":$port\$" '$4 ~ p && NF>=7 {print $7}' | sort -u | tr '\n' ' ' || true)"
    fi
  fi

  if [ -n "$named" ]; then
    bump api-port-owner ok "${port} 的監聽者（${how}）：${named}　⚠️ 若出現**不是**容器／proxy 的程式，它可能遮蔽了 api（見 api-identity）"
  elif [ -n "$listeners" ]; then
    bump api-port-owner warn "${port} **有東西在聽**：${listeners} —— 但指不到 process（${how} 在這個 namespace 看不到；WSL/Docker 的 port 轉發常如此）。有人佔用**不等於**有問題；真正的判準在 api-identity 那條。"
  elif [ -z "$how" ]; then
    bump api-port-owner skip "這台沒有 lsof／ss，看不到誰佔著 ${port}（手動：ss -ltnp | grep :${port}）"
  else
    bump api-port-owner skip "${port} 沒有監聽者 —— api 還沒 publish（或根本沒跑）"
  fi
}

ch_readiness() {
  local body rc=0
  # ⚠️ 2026-10-03：原本寫死 `http://localhost:8000/ready`，而 `API_URL` 是第 38 行
  #   的變數（可用 `HOST_API_LOCAL` 覆寫）。**寫死等於讓那個覆寫對這一項失效** ——
  #   改埠之後 doctor 會繼續打舊埠，症狀是「明明改了卻沒生效」。
  body="$(curl -s -m 20 -o /tmp/.rd.$$ -w '%{http_code}' "${API_URL}/ready" 2>/dev/null)" || rc=1
  if [ "$rc" -ne 0 ] || [ -z "$body" ]; then
    bump readiness skip "打不到 /ready（api 沒起來？）"
    rm -f "/tmp/.rd.$$"; return
  fi
  if [ "$body" = "200" ]; then
    local det
    det="$(python3 -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    print(""); raise SystemExit
c=(d.get("checks") or {})
bad=[k for k,v in c.items() if v.get("required") and v.get("verdict")!="up"]
print("全 up" if not bad else "非 up: "+",".join(bad))' "/tmp/.rd.$$" 2>/dev/null)"
    if [ -z "$det" ]; then
      bump readiness ok "HTTP 200（讀不出逐項明細 —— 格式變了？）"
    else
      bump readiness ok "$det"
    fi
  else
    local det
    det="$(python3 -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    print(""); raise SystemExit
c=(d.get("checks") or {})
out=[]
for k,v in c.items():
    if v.get("required") and v.get("verdict")!="up":
        out.append(f"{k}={v.get(chr(118)+chr(101)+chr(114)+chr(100)+chr(105)+chr(99)+chr(116))}: {str(v.get(chr(100)+chr(101)+chr(116)+chr(97)+chr(105)+chr(108)))[:90]}")
print("; ".join(out))' "/tmp/.rd.$$" 2>/dev/null)"
    bump readiness fail "HTTP ${body}${det:+ — ${det}}"
  fi
  rm -f "/tmp/.rd.$$"
}

# ⚠️ 這條會**真的打一次**提問（約 3 秒），而且它**會用掉一點額度**。
# 這是刻意的取捨：`/health` 與 `/ready` 都抓不到「實際查詢會不會成功」。
# 可以用 `RAGDEMO_NO_QUERY=1` 跳過。
ch_query() {
  if [ -n "${RAGDEMO_NO_QUERY:-}" ]; then
    bump query skip "RAGDEMO_NO_QUERY 有設"
    return
  fi
  local out code
  # ⚠️ 2026-10-03：同上，這裡原本也寫死 8000（見 ch_readiness 的說明）。
  out="$(curl -s -m 120 -o /tmp/.qq.$$ -w '%{http_code}' \
        -X POST "${API_URL}/query" \
        -H 'Content-Type: application/json' \
        -d '{"question":"健康檢查：請用一句話說明民法第18條","top_k":2}' 2>/dev/null)"
  code="$out"
  case "$code" in
    200)
      local n
      n="$(python3 -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    print("?"); raise SystemExit
print(len(d.get("answer") or ""))' "/tmp/.qq.$$" 2>/dev/null)"
      if [ "${n:-0}" -ge 10 ] 2>/dev/null; then
        bump query ok "HTTP 200，答案 ${n} 字"
      else
        bump query warn "HTTP 200 但答案只有 ${n} 字 —— 檢索可能沒撈到東西"
      fi ;;
    '')  bump query fail "查詢逾時（120s）或連不上 —— ⚠️ /health 與 /ready 都不會抓到這種情況" ;;
    *)   bump query fail "HTTP ${code}：$(head -c 200 "/tmp/.qq.$$" | tr '\n' ' ')" ;;
  esac
  rm -f "/tmp/.qq.$$"
}

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
  # ⚠️ 2026-10-01：這條 warn 的建議**不完整，照字面做會讓兩台備援機永久 401**。
  #    這裡只比對指紋、看不到那把 key 的語意：`QDRANT_PEER_API_KEY` 的定義就是
  #    「能認證到來源機（x570）qdrant 的 key」，而 x570 只認 compose.yaml:12 的
  #    QDRANT__SERVICE__API_KEY —— 同值是結構性必然。要拆開必須先有
  #    QDRANT__SERVICE__ALT_API_KEY（M2 範圍，qdrant v1.19.1 的第二個讀寫槽）。
  #    完整分析見 settings/env/README.md §7〈peer 那把的語意〉。
  #    另一件事：那個外洩值同時是來源機自己的 api_key，只輪換 peer **不會吊銷它**，
  #    這條 warn 轉綠只代表指紋不同。
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
    bump rotate-hint warn "QDRANT_PEER_API_KEY 與 QDRANT_API_KEY 同值 → 三台鎖步，該值已知外洩
   ⚠️ 但不要直接輪換 peer 那把：同值是結構性必然，拆開需要 compose.yaml 先有
      QDRANT__SERVICE__ALT_API_KEY（M2 範圍），否則兩台備援機永久 401。
      順序與驗證見 settings/env/README.md §7〈peer 那把的語意〉。"
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
# ── 檢查 8b. source 機的每日同步**真的有在跑** ──────────────────────────
#
# ⚠️⚠️ 2026-10-02：`ch_law_version` 對 source 機（`.law_sync.json` 存在）
#   **只看檔案在不在**，完全沒有檢查 `last_checked`。而它自己的註解寫著
#   「每日 `sync_daily` 有在跑（.law_sync.json 的 last_checked 是新的）」——
#   **那句話沒有任何程式碼在驗**。
#
#   後果：x570 的 crontab 有兩個錯誤（工作路徑少 `.win`、`.venv-ingest` 不存在）
#   時，`host-doctor` **回 ok**。而那正是使用者要確認的事 —— 工具說「好」，
#   它卻壞著。**一個宣稱一個沒驗證過的東西，比沒有那個檢查更糟。**
#
# 這個檢查要回答兩個獨立問題，而且**要分得開**：
#   (a) **上次真的同步過嗎** → `last_checked` / `applied_at` 新不新鮮
#   (b) **明天的 crontab 會成功嗎** → 那條指令的**每個路徑都存在嗎**
#   (b) 是靜態檢查，不需要等它跑；(a) 要等。**兩個都做，因為它們的失效
#   症狀不同**：只有 (a) 過但 (b) 壞 = 下次就會壞；只有 (b) 過但 (a) 舊 =
#   已經壞了一段時間。
ch_source_sync() {
  # 非 source 機 → 這條不適用（備援機靠 sync-snapshot.sh，那由 ch_law_version 管）
  if [ ! -f "$LAW_SYNC_FILE" ]; then
    bump source-sync skip "這台不是 source 機（沒有 .law_sync.json）—— 由 ch_law_version 檢查快照"
    return
  fi

  # ── (a) 上次同步的時間 ──
  local lc ac
  lc="$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("last_checked") or "")
except Exception: print("")' "$LAW_SYNC_FILE" 2>/dev/null)"
  ac="$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("applied_at") or "")
except Exception: print("")' "$LAW_SYNC_FILE" 2>/dev/null)"
  # 門檻 26 小時：每日 06:30 跑，26h 給「昨天跑了但今天還沒到 06:30」與
  # 「真的跳過了至少一次」之間留一個身分的餘裕。太短會在每天下午誤報。
  #
  # ⚠️⚠️ 2026-10-02：**這一版原本把 naive 當 UTC**（`t.replace(tzinfo=utc)`），
  #   那是 ch_law_version 註解裡記錄過的同一個錯誤 —— **少算整個 UTC offset**
  #   （本機 8 小時）。實測同一個 `last_checked=2026-09-29T23:39:23`：
  #   正確 71.7 小時、這裡算 63.7 小時。**26h 的門檻實際變成 34h**。
  #   現在改用共用的 `age_h_local()`（見檔頭），naive ↔ 本機相減。
  local age_h="?"
  if [ -n "$lc" ]; then
    age_h="$(age_h_local "$lc")"
    [ -z "$age_h" ] && age_h="?"
  fi
  # ⚠️ 2026-10-02 修正訊息：第一版寫「沒在跑，或 crontab 有錯」——
  #   而 wsl 實測是**有在跑、但失敗**（上游 law.moj.gov.tw 回 HTTP 500）。
  #   那兩件事的處置完全不同：前者看 crontab，後者看上游。把它們混成一句
  #   會讓人去查錯方向 —— 這個專案反覆在修的正是這一類。
  #
  #   所以：先看 log 最後一筆的時間戳（共用 `law_log_last_attempt_h()`）。
  #   **有近期嘗試 → 跑了但失敗**；**沒有 → 真的沒跑**。
  local logline=""
  for lg in "$ROOT/data/laws/sync.log" "$SYNC_LOG"; do
    [ -f "$lg" ] && { logline="$(tail -3 "$lg" 2>/dev/null | tr '\n' ' ' | tail -c 220)"; break; }
  done
  local recent="no" log_age=""
  log_age="$(law_log_last_attempt_h)"
  if [ -n "$log_age" ]; then
    if python3 -c "import sys; sys.exit(0 if float('$log_age') < 26 else 1)" 2>/dev/null; then
      recent="yes"
    fi
  fi
  case "$age_h" in
    '?')  bump source-sync-last fail "讀不到 last_checked（$LAW_SYNC_FILE 的格式變了？）" ;;
    *)    if python3 -c "import sys; sys.exit(0 if float('$age_h') < 26 else 1)" 2>/dev/null; then
            bump source-sync-last ok "上次同步 ${age_h} 小時前（last_checked=${lc}）"
          elif [ "$recent" = "yes" ]; then
            bump source-sync-last fail "資料停在 ${age_h} 小時前，但 log 顯示**最近有在嘗試** → 是**跑了但失敗**，不是沒跑。處置看 log 最後幾行（常見原因：上游回 5xx）: ${logline}"
          else
            bump source-sync-last fail "資料停在 ${age_h} 小時前，且 log 沒有近期嘗試 → **每日 sync_daily 沒在跑**。看下面 source-sync-cron 那幾項（路徑／解譯器／腳本）"
          fi ;;
  esac
  [ -n "$ac" ] && bump source-sync-applied ok "applied_at=${ac}" || \
    bump source-sync-applied warn "沒有 applied_at —— 同步可能從沒成功套用過"

  # ── (b) 明天的 crontab 會成功嗎（靜態）──
  local crontab_out=""
  crontab_out="$(crontab -l 2>/dev/null || true)"
  if [ -z "$crontab_out" ]; then
    bump source-sync-cron fail "crontab 是空的 —— 沒有任何排程，而這台是 source 機"
    return
  fi
  # 只看含 sync_daily 的那條（source 機該跑的是它，不是 ensure-stack）
  local line
  line="$(printf '%s\n' "$crontab_out" | grep -vE '^\s*#|^\s*$' | grep -F 'sync_daily' | head -1)"
  if [ -z "$line" ]; then
    bump source-sync-cron fail "crontab 沒有 sync_daily 的排程 —— 這台是 source 機卻沒人更新法規"
    return
  fi
  bump source-sync-cron ok "找到 sync_daily 的排程"

  # ⚠️ **逐個路徑檢查**。2026-10-02 x570 的兩個錯誤就是這兩種：
  #   (1) 工作目錄少 `.win` → `cd` 失敗，後面的指令根本沒跑
  #   (2) `.venv-ingest/bin/python` 不存在 → python 找不到
  # 而症狀都是「沒有任何錯誤輸出，只是法規沒更新」—— cron 失敗的信會寄到
  # 郵件，而多半沒人看。所以在這裡靜態驗一次。
  local bad=0 checked=0
  local p
  # (1) cd 的目標
  local cdpath
  # ⚠️ 2026-10-02 修正（第一版在 wsl 上**假失敗**）：`[^&|;]+` 是貪婪的，會把
  # `&&` 前面的空白一起吃進去，於是 cdpath 變成 `/…/ragdemo.win `（帶尾隨空白）
  # → `[ -d ]` 為假 → 回報「cd 目標不存在」，而那個目錄明明存在。
  # 這一支的全部用途就是告訴人「你的 cron 會不會失敗」，**假失敗會讓它被忽略** ——
  # 那比沒有這個檢查更糟。所以一律 trim。
  cdpath="$(printf '%s' "$line" | sed -nE 's/.*\bcd[[:space:]]+([^&|;]+)[[:space:]]*&&.*/\1/p' | head -1 | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [ -n "$cdpath" ]; then
    checked=$((checked+1))
    if [ -d "$cdpath" ]; then
      bump source-sync-cwd ok "${cdpath}"
    else
      bump source-sync-cwd fail "cd 目標不存在：${cdpath} ← **cron 會整條失敗**（常見原因：路徑少了副檔名）"
      bad=1
    fi
  else
    bump source-sync-cwd skip "那條排程沒有 cd（直接在 cron 的工作目錄跑）—— 相對路徑要靠 $ROOT 之外的 cwd，這種寫法本身就脆弱"
  fi
  # (2) 直譯器（第一個看起來像路徑的 ./*/bin/* 或 /usr/bin/*）
  local ipath
  ipath="$(printf '%s' "$line" | tr ' ' '\n' | grep -E '(^|/)(python3?|uv)$|/\.venv[^/]*/bin/python' | head -1 | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [ -n "$ipath" ]; then
    checked=$((checked+1))
    case "$ipath" in
      /*) if [ -x "$ipath" ]; then
             bump source-sync-interp ok "${ipath}"
           else
             bump source-sync-interp fail "解譯器不存在或不可執行：${ipath} ← **cron 會整條失敗**"
             bad=1
           fi ;;
      *)  # 相對路徑 → 相對於 cd 目標（若有的話）
           local abs="$ipath"
           [ -n "$cdpath" ] && abs="${cdpath}/${ipath}"
           if [ -x "$abs" ]; then
             bump source-sync-interp warn "解譯器是相對路徑（${ipath}）—— 只在 cd 成功時才找得到；建議改用絕對路徑"
           else
             bump source-sync-interp fail "解譯器是相對路徑（${ipath}）且在 ${cdpath} 下找不到 ← **cron 會整條失敗**（常見原因：venv 名稱拼錯）"
             bad=1
           fi ;;
    esac
  else
    bump source-sync-interp skip "那條排程看不出用哪個解譯器（用 PATH 裡的）"
  fi
  # (3) 腳本本身
  local spath
  spath="$(printf '%s' "$line" | tr ' ' '\n' | grep -E 'sync_daily\.py$' | head -1 | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [ -n "$spath" ]; then
    checked=$((checked+1))
    local sabs="$spath"
    case "$spath" in /*) ;; *) [ -n "$cdpath" ] && sabs="${cdpath}/${spath}" ;; esac
    if [ -f "$sabs" ]; then
      bump source-sync-script ok "${spath}"
    else
      bump source-sync-script fail "腳本不存在：${sabs} ← **cron 會整條失敗**"
      bad=1
    fi
  fi
  [ "$checked" -eq 0 ] && bump source-sync-paths warn "沒從那條排程解析出任何路徑 —— 靜態檢查等於沒做，請確認排程寫法"
  [ "$bad" -eq 0 ] && bump source-sync-verdict ok "靜態檢查：${checked} 個路徑都存在" || \
    bump source-sync-verdict fail "有路徑不存在 —— **明天的排程會失敗**，症狀是「沒有錯誤、只是法規沒更新」"
}

ch_law_version() {
  if [ ! -f "$LAW_VERSION_FILE" ]; then
    # 2026-10-02：這台是 **source 機**（有 .law_sync.json）還是備援機？
    #
    # 為什麼要分：`law-update-worker.sh` 的角色判斷**只看這一個檔在不在**
    # （`:18` 附近），有 → source，沒有 → 備援。而 `.law_version` 只由
    # `sync-snapshot.sh` 寫 —— source 機**本來就不走那條路**，所以它永遠
    # 不會有這個檔。舊訊息對所有機器都說「沒有＝快照同步還沒成功過」，
    # 對 source 機是**結構性錯誤** —— 一台完全正常的機器被報成故障。
    # 這是「報告沒有分角色」家族的又一例（前一例是 code-drift：問錯對象）。
    #
    # 實測：x570 的 host-doctor 長期回這條 warn，而 x570 是 source 機、
    # 每日 `sync_daily` 有在跑（.law_sync.json 的 last_checked 是新的）。
    if [ -f "$LAW_SYNC_FILE" ]; then
      bump law-version ok "這台是 source 機（law-update-worker.sh 的角色判斷依 .law_sync.json），版本由 sync_daily 直接產生，不需要 .law_version"
    else
      bump law-version warn "沒有 data/laws/.law_version（這台是備援機，靠 sync-snapshot.sh 帶過來；沒有＝快照同步還沒成功過）"
    fi
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
    #    本機時間**（2026-09-27 實測：wsl 是 CST +0800，寫出 "00:40:05" 其實是
    #    16:40 UTC）。所以不能把 naive 當 UTC —— 那會**少算 8 小時**，
    #    6 小時的門檻實際變成 14 小時（第一版就這樣寫錯過，實測報 13h 而非 21.9h）。
    #    現在走共用的 `age_h_local()`，規則只有一份，**不會再各寫一次各錯一次**。
    age_h="$(age_h_local "$synced")"
    # ⚠️⚠️ 2026-10-02 **修正判準本身**（不只是修訊息）。原本是
    #     `if age_h >= 6 → warn「快照已 N 小時沒成功更新」`，
    # 那個判準**結構性地會誤報**：`sync-snapshot.sh:185` 在版本沒變時直接
    # `return 0`，**不寫 synced_at**。所以 synced_at 是「上次**版本變了**」的時間戳，
    # 不是「上次**跑了**」。上游沒發新版時它永遠不動 —— 而那是正常狀態。
    #
    # 實測（2026-10-02 mbp 回報）：log 最後一行
    #     [2026-10-02 23:17:29] unchanged (39879 points), skip
    # —— **排程有在跑而且成功**，只是內容沒變。doctor 卻說「快照已 25 小時沒成功
    # 更新」。**那句話把「版本沒變」講成了「沒成功更新」，方向完全相反。**
    #
    # 健康信號是「排程有沒有在跑」，證據在 log 最後時間戳：
    #   · log 有近期嘗試 → 排程正常；synced_at 老 = 上游沒發新版 → **資訊，不是問題**
    #   · log 沒近期嘗試 → 排程真的沒跑 → **warn**
    log_age="$(law_log_last_attempt_h)"
    if [ -n "$log_age" ] && [ -n "$age_h" ] \
       && python3 -c "import sys; sys.exit(0 if float('$log_age') < $LAW_SYNC_STALE_H else 1)" 2>/dev/null; then
      bump law-version ok "法規版本 ${v}（快照於 ${synced} 最後更新）；排程正常：同步 log ${log_age} 小時前有跑 —— 上游沒發新版所以內容沒變（正常）"
      return 0
    fi
    # ⚠️ 2026-10-02 **把 log 放在 synced_at 前面**。原本這句以
    #   「快照已 N 小時沒成功更新（synced_at=…）」開頭 —— 而 synced_at **只在版本
    #   變化時才寫**，所以「N 小時」講的是「上游多久沒發新版」，**不是**排程狀態。
    #   實測 wsl：訊息說 24.1 小時，但**真正可行動的事实**是「同步 log 靜默
    #   13.5 小時，而這台根本沒有 sync-snapshot.sh 的 crontab 條目」。
    #   先講錯的那個，會讓人去查上游而不是查排程。
    if [ -n "$log_age" ] && python3 -c "import sys; sys.exit(0 if float('$log_age') >= $LAW_SYNC_STALE_H else 1)" 2>/dev/null; then
      lastline="$(tail -1 "$SYNC_LOG" 2>/dev/null || true)"
      case "$lastline" in
        *offline*) why="；log 最後一行說來源離線（${lastline##*] }）" ;;
        *) why="；log 最後一行：${lastline:-（讀不到）}" ;;
      esac
      bump law-version warn "同步排程已 ${log_age} 小時沒動（門檻 ${LAW_SYNC_STALE_H} 小時）—— **先確認這台有沒有 sync-snapshot.sh 的排程**（crontab／launchd），再談版本${why}；快照最後更新於 ${synced}，法規版本 ${v}"
      return 0
    fi
    if [ -n "$age_h" ] && python3 -c "import sys; sys.exit(0 if float('$age_h') >= $LAW_SYNC_STALE_H else 1)" 2>/dev/null; then
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
ch_code_drift
# 2026-10-02 新增。順序有意義：**先確認工具真的能用（ch_sops），再看依賴
# （ch_qdrant_keys）、再看 readiness、最後才真的打一次查詢** —— 前面的失敗會讓
# 後面的結果沒有意义（例如 api 沒起來時 ch_query 必然失敗，那不是新問題）。
ch_sops
ch_qdrant_keys
ch_api_port_owner
ch_api_identity
ch_readiness
ch_query
ch_env_check
ch_rotate
ch_registry
ch_source_sync
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
