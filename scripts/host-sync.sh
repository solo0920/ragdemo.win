#!/usr/bin/env bash
# host-sync — 冪等部署的單一入口：git 到位 → env-sync 分發 → compose 起來 → 驗收。
#
# 存在的理由：升級過去是「一個人照 HOST-UPGRADE.md 的散文做 20 個步驟」，
# 全 repo 沒有任何 git pull。這支把 20 個步驟收成 1 個命令，讓 replica
# 只吃 tag（見 SCOPE.md scope F：--ref 存在的目的就是讓 replica 不吃半成品）。
#
# 步驟順序**不是**隨便排的，每一個都有理由：
#   1. preflight  先確認不會踩到本機未提交的東西 —— 踩到了就拒絕，不覆蓋。
#   2. fetch      --tags --prune：tag 是部署單位，prune 掉被刪的遠端 tag，
#                  否則 refs/tags 會愈積愈多，--ref 打錯字時解析到舊 tag 還以為成功。
#   3. ref        必須在 4 之前：env-sync.sh pull 讀的是 repo 裡的檔
#                  （settings/env/*），ref 沒切過去就等於用舊版的規則合併新 .env。
#   4. env-sync   三機共用層分發。改 .env 不改這裡＝另外兩台拿不到。
#   5. compose    up -d --build。⚠ qdrant 的 api-key 與 pg 密碼是**啟動參數**，
#                  改 .env 而不重啟容器等於沒換（ARCHITECTURE 陷阱表）。
#   6. verify     分開回報「部署已完成」與「驗收未通過」，理由見下方 verify 註解。
#
# 安全規則（違反過的才寫下來，來源見 env-sync.sh 同一段）：
#   - 絕不在 stdout/stderr 印任何憑證值；本檔全程不讀 .env 的值，只讀 HOST_ID。
#   - 在 `bash -x`（xtrace）下直接拒絕執行 —— 2026-09-26 三次外洩之一就是 bash -x。
#   - 絕不用任何 force flag 蓋掉未提交改動。工作樹不乾淨就 exit 非 0。
#
# 冪等的定義：連跑兩次，第二次 diff 為空。所以每一步都要先問「已經是目標狀態了嗎」。
# --ref 指向的 commit 就是目標狀態；HEAD 已經在那裡就跳過 checkout 並明講。
#
# ⚠ bash 3.2 相容（mbp 是 macOS，內建 bash 3.2）：不要用 mapfile／readarray／
#   關聯陣列／${v^^}。這支刻意只用到 while read 迴圈與索引陣列。
set -euo pipefail

# xtrace 下拒絕：與 env-sync.sh 同一道防線、同一種寫法。$- 含 x 表示 set -x 生效中
# （bash -x 與 set -o xtrace 兩者都會讓它出現）。這段必須排在所有會碰 .env 的程式碼
# 之前 —— 它自己只印一行字，不含秘密，所以被 xtrace 印出來也無妨。
case "$-" in
  *x*) echo "host-sync: refuse to run under xtrace (bash -x leaks secrets)" >&2; exit 1 ;;
esac
# 第二道交叉檢查（多餘、成本為零）。SHELLOPTS 是繼承下來的選項集；實測它含 xtrace
# 的時機與上面的 $- 含 x 完全相同，所以這行不會比上面多擋到任何情況。留著的理由是
# 「上面那行若哪天被改壞，還有一層可讀的痕跡」，而不是以為這裡才是防線。
case "${SHELLOPTS:-}" in
  *xtrace*) echo "host-sync: refuse to run under xtrace (SHELLOPTS 含 xtrace)" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 本機 api。compose 把 api 綁在 127.0.0.1:8000（不是 TS_IP —— 公網只經 cloudflared），
# 所以驗收一定走 127.0.0.1。可用 HOST_API_LOCAL 覆寫給 tunnel 情境。
API_URL="${HOST_API_LOCAL:-http://127.0.0.1:8000}"
# 驗收等容器的重試：秒數 × 次數。剛 up -d 完 api 還沒起來，單次 curl 會產生
# 假失敗，而「部署失敗要看得見」最怕的就是這種自己嚇自己的失敗。
VERIFY_TRIES=12
VERIFY_WAIT=5

# exit code 契約（寫在這裡是因為呼叫者只看得見數字）：
#   0  部署完成且驗收通過
#   1  在 xtrace 下拒絕執行（與 env-sync.sh 同碼：同一道防線，不另立新規則）
#   2  引數錯誤
#   3  preflight 阻擋（工作樹不乾淨／缺工具／HOST_ID 不合法）
#   4  步驟失敗（fetch／ref／env-sync／compose）
#   5  部署完成，但**驗收未通過**
#   6  部署完成，**驗收被略過**（--skip-verify）
# 5 與 6 分開是刻意的：wsl 目前沒有容器在跑，呼叫者會需要略過驗收；
# 把「略過」回成 0 就等於把沒驗裝成驗過，那是本專案最恨的靜默劣化。
EXIT_OK=0; EXIT_XTRACE=1; EXIT_USAGE=2; EXIT_PREFLIGHT=3
EXIT_STEP=4; EXIT_VERIFY=5; EXIT_SKIPPED=6

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'
usage: host-sync.sh [--ref <tag|branch|sha>] [--dry-run] [--skip-verify]
  --ref         部署到指定 ref。tag/sha → detached HEAD（版本不可變，符合
                「replica 只吃 tag」）；branch → 切過去後 fast-forward。
                不給就 pull --ff-only 目前的分支。
  --dry-run     只印會做什麼。不 fetch、不切 ref、不 pull、不動容器、不寫檔。
                preflight 仍會照跑並回報（那正是「這樣跑會不會成功」的答案）。
  --skip-verify 略過第 6 步。**exit 6，不是 0** —— 略過必須看得出是略過。
EOF
}

say()  { printf '%s\n' "$*"; }
hdr()  { printf '\n== %s ==\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
die()  { local c="$1"; shift; warn "$*"; exit "$c"; }

# 步驟失敗一律走這裡，不要直接呼叫 mutating 指令。
# 為什麼需要它：set -e 會在指令回傳非零的**那一行**就中止整支腳本，
# 結果 exit code 是 bash 給的 1 —— 跟「xtrace 拒絕執行」的 1 撞掉，
# 呼叫者分不出「有人用 bash -x 跑」還是「compose 失敗」。
# 把每個 mutating 指令包進 `if ! ...` 裡，errexit 就不會先一步把腳本收走，
# 我們才拿得到 EXIT_STEP。訊息也順便講清楚「後面的步驟沒跑」。
must() {
  local what="$1"; shift
  if ! "$@"; then
    die "$EXIT_STEP" "host-sync: ${what} 失敗 —— 步驟已在此停止，後面的步驟都沒有跑。"
  fi
}

# 讀 .env 的 HOST_ID。只讀這一個鍵，且只用來「確認這台是誰」——
# 這不是憑證，是機器代號，印出來是診斷要用的（見 host-doctor.sh 同一段說明）。
# 寫法與 env-sync.sh:283 local_host() 是孿生檔：改一處就要改另一處。
# 真的收斂成單一真相要等 env-sync.sh 出一個唯讀的查詢子命令（本 scope 不做）。
host_id_of() {
  [ -f "$ROOT/.env" ] || return 0
  grep -m1 -E '^HOST_ID=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || true
}

# ── 引數 ───────────────────────────────────────────────────────────────────────
REF=""; DRY=0; SKIP_VERIFY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ref)         [ $# -ge 2 ] || die "$EXIT_USAGE" "host-sync: --ref 需要一個值"; REF="$2"; shift 2 ;;
    --ref=*)       REF="${1#--ref=}"; shift ;;
    --dry-run)     DRY=1; shift ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    -h|--help)     usage; exit "$EXIT_OK" ;;
    *)             die "$EXIT_USAGE" "host-sync: unknown arg: $1" ;;
  esac
done

# ── 步驟 1：preflight ─────────────────────────────────────────────────────────
# 這一步決定「動手」還是「拒絕」。三個阻擋條件都不可繞過，也都不提供 force：
#   a) 不在 repo 裡          → 後面每個指令的假設都不成立
#   b) 工作樹不乾淨          → 切 ref 會帶著改動走；要蓋就得 force，不可以
#   c) 缺工具／HOST_ID 不合法 → 一定會在某一步炸，與其炸在那不如現在講清楚
# 回傳 0 = 可部署；回傳非 0 = 有阻擋。dry-run 也照跑（預覽就该包含「會不會被擋」）。
PREFLIGHT_BLOCKERS=""
preflight() {
  local dirty n MISSING=""
  if ! git -C "$ROOT" rev-parse --show-toplevel >/dev/null 2>&1; then
    warn "preflight: $ROOT 不是 git repo"
    return 1
  fi
  # b) 乾淨度。--porcelain 同時涵蓋已追蹤的改動與未追蹤檔，兩者都算。
  #    刻意不提供 --force / --discard / stash：那些都是「蓋掉別人的東西」，
  #    而這支腳本不該有權力決定要蓋誰。
  dirty="$(git -C "$ROOT" status --porcelain 2>/dev/null || true)"
  if [ -n "$dirty" ]; then
    n="$(printf '%s\n' "$dirty" | grep -c . || true)"
    warn "preflight: 工作樹不乾淨（${n} 個項目，含未追蹤檔）"
    printf '%s\n' "$dirty" | sed 's/^/    /' >&2
    warn "preflight: 拒絕部署。切 ref 會把這些改動帶著走；要蓋掉就需要 force，"
    warn "preflight: 而那正是這支腳本不該做的事。請先 commit／stash／刪除再跑。"
    PREFLIGHT_BLOCKERS="${PREFLIGHT_BLOCKERS}dirty-tree "
  fi
  # HOST_ID：認得的三台之外的值會讓 env-sync render 直接 die（表只有三列），
  # 與其在那裡炸出一句難懂的信息，不如在這裡擋下來並說清楚。
  local hid; hid="$(host_id_of)"
  case "$hid" in
    # ⚠️ 2026-10-01：`msi` → `wsl`。這是**會擋下流程的**白名單，不是註解 ——
    #    留著 `msi` 不換，`HOST_ID=wsl` 會被判成非法機台，preflight 直接擋住。
    #    機台清單的真相是 settings/env/hosts.shared.env 的 `HOSTS=`。
    x570|mbp|wsl) ;;
    *) warn "preflight: .env 的 HOST_ID 是 '${hid:-（空）}'，須為 x570/mbp/wsl"
       warn "preflight: .env 不存在或沒有 HOST_ID → 先備好 .env（不要 commit 它）"
       PREFLIGHT_BLOCKERS="${PREFLIGHT_BLOCKERS}host-id " ;;
  esac
  # 工具：依「這次真的會走到的步驟」列，不多不少。
  # docker compose 是 plugin，沒有獨立執行檔，所以查 docker 即可（真正缺時
  # 會在第 5 步報 "docker: 'compose' is not a docker command"，那時已太晚）。
  # 變數名大寫的理由：scripts/env-audit.py 的 scan_shell() 靠 SH_READ
  # （`\$\{NAME[:}]` 或 `\$[A-Z][A-Z_0-9]+`）反查「誰讀了什麼環境變數」，只扣除
  # 同檔內自己賦值的名字。小寫的 loop 變數不符 SH_READ 第二段（要求全大寫），
  # 卻也不是 SH_ASSIGN 的賦值節點，於是會被寫成假的 `missing=` 進 .env.example。
  # 踩過：2026-09-27（`--list` 跑出 SHELLOPTS 之外的空/missing 假變數）。
  for T in git python3 docker sops curl; do
    command -v "$T" >/dev/null 2>&1 || MISSING="${MISSING} ${T}"
  done
  if [ -n "$MISSING" ]; then
    warn "preflight: 缺工具:${MISSING}"
    PREFLIGHT_BLOCKERS="${PREFLIGHT_BLOCKERS}tools "
  fi
  [ -z "$PREFLIGHT_BLOCKERS" ]
}

# ── 步驟 2：fetch ─────────────────────────────────────────────────────────────
# dry-run 不 fetch：fetch 會寫 .git/FETCH_HEAD 與 refs/，與「不寫任何檔案」衝突。
# 連帶後果（要明講，否則預覽會給人錯的保證）：dry-run 只能用**本地已知**的 ref
# 判斷「已在該 ref」，所以本地沒 fetch 過的新 tag 它看不到。
step_fetch() {
  if [ "$DRY" -eq 1 ]; then
    say "  [dry-run] 將執行: git fetch --tags --prune（dry-run 不 fetch，只用本地已知 ref 判斷）"
  else
    say "  git fetch --tags --prune"
    must "git fetch --tags --prune" git -C "$ROOT" fetch --tags --prune
  fi
}

# ── 步驟 3：ref ───────────────────────────────────────────────────────────────
# 三種情形，各自冪等：
#   --ref 是本地分支 → checkout 該分支（已在就跳過）+ 對上游 fast-forward
#   --ref 是 tag/sha → checkout --detach 到那個 commit（tag 部署＝版本不可變）
#   沒給 --ref        → pull --ff-only 目前的分支
# 為什麼 branch 與 tag 分開處理：tag/sha 走 detached 是刻意的（部署到不可變版本），
# 但若 --ref 給分支也 detach，下次沒帶 --ref 的執行就會卡在「HEAD 是 detached，
# git pull 說不出要 pull 哪裡」—— 那正是本專案最恨的靜默失敗。所以分支就留在分支上。
resolve_ref() { git -C "$ROOT" rev-parse --verify --quiet "$1^{commit}" 2>/dev/null || true; }
cur_branch() { git -C "$ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || true; }
is_branch()   { git -C "$ROOT" show-ref --verify --quiet "refs/heads/$1"; }

step_ref() {
  local want head b
  head="$(git -C "$ROOT" rev-parse HEAD)"
  if [ -n "$REF" ]; then
    want="$(resolve_ref "$REF")"
    [ -n "$want" ] || die "$EXIT_STEP" \
      "host-sync: 解析不了 ref '$REF'（fetch 過了嗎？tag 拼錯字是最常見的原因）"
    if [ "$want" = "$head" ]; then
      say "  已在該 ref（${REF} → ${want:0:7}），略過切換"
      return 0
    fi
    if is_branch "$REF"; then
      if [ "$DRY" -eq 1 ]; then
        say "  [dry-run] 將執行: git checkout ${REF}（分支，留在分支上以支援後續的 pull）"
        if git -C "$ROOT" rev-parse --verify --quiet "refs/remotes/origin/${REF}" >/dev/null; then
          say "  [dry-run] 將執行: git merge --ff-only origin/${REF}"
        fi
      else
        say "  git checkout ${REF}"
        must "git checkout ${REF}" git -C "$ROOT" checkout "$REF"
        # 有上游就快進；沒有就只是切過去（離線分支、還沒 push 的分支都算）。
        # 用 merge --ff-only 而不是 pull：fetch 剛做過，再 fetch 一次只是浪費，
        # 而 --ff-only 保證不會在部署機上生出 merge commit。
        if git -C "$ROOT" rev-parse --verify --quiet "refs/remotes/origin/${REF}" >/dev/null; then
          say "  git merge --ff-only origin/${REF}"
          must "git merge --ff-only origin/${REF}" git -C "$ROOT" merge --ff-only "origin/${REF}"
        else
          say "  （${REF} 沒有 origin/${REF} 上游，只切過去不快進）"
        fi
      fi
    else
      if [ "$DRY" -eq 1 ]; then
        say "  [dry-run] 將執行: git checkout --detach ${REF}（tag/sha → 版本不可變）"
      else
        say "  git checkout --detach ${REF}（tag/sha → 版本不可變）"
        must "git checkout --detach ${REF}" git -C "$ROOT" checkout --detach "$REF"
      fi
    fi
  else
    b="$(cur_branch)"
    if [ -z "$b" ]; then
      die "$EXIT_STEP" "host-sync: HEAD 是 detached（${head:0:7}），沒有分支可 pull --ff-only。
       要在這台維持 detached 請改用顯式的 --ref <tag|sha>；那樣每次部署都指定版本，
       比較可重現。"
    fi
    if [ "$DRY" -eq 1 ]; then
      say "  [dry-run] 將執行: git pull --ff-only（分支 ${b}）"
    else
      say "  git pull --ff-only（分支 ${b}；--ff-only 確保不會生出 merge commit）"
      must "git pull --ff-only（分支 ${b}）" git -C "$ROOT" pull --ff-only
    fi
  fi
}

# ── 步驟 4：env-sync ──────────────────────────────────────────────────────────
# 必須在 ref 之後（見檔頭）。這一步會改 .env —— .env 是 gitignored 的執行期真相，
# 所以它改的東西不在「工作樹不乾淨」那條防線的管轄內，而這正是它該做的事：
# 把共用憑證與 per-host 值從 repo 裡的分發層搬到本機 .env。
step_env_sync() {
  if [ "$DRY" -eq 1 ]; then
    say "  [dry-run] 將執行: scripts/env-sync.sh pull（改 .env，不改任何追蹤檔）"
  else
    say "  env-sync.sh pull"
    must "env-sync.sh pull（三機共用層分發）" "$ROOT/scripts/env-sync.sh" pull
  fi
}

# ── 步驟 5：compose ───────────────────────────────────────────────────────────
# 在 ROOT 底下跑（subshell 的 cd），因為 compose 要同時找到 compose.yaml 與 .env。
step_compose() {
  if [ "$DRY" -eq 1 ]; then
    say "  [dry-run] 將執行: docker compose up -d --build（不動容器）"
  else
    say "  docker compose up -d --build（換掉 api-key／密碼要重啟才生效，up 會自動 recreate）"
    # subshell + `if !`：兩者缺一不可。subshell 是為了 cd 不影響呼叫者的目錄；
    # `if !` 是為了讓 errexit 不要在 compose 回傳非零時先收走腳本（否則 exit 1）。
    if ! (cd "$ROOT" && docker compose up -d --build); then
      die "$EXIT_STEP" "host-sync: docker compose up -d --build 失敗 —— 步驟已在此停止。"
    fi
  fi
}

# ── 步驟 6：verify ────────────────────────────────────────────────────────────
# 為什麼分開回報：前 5 步失敗與「部署成功但驗收不過」是兩件事。前者要人立刻處置，
# 後者多半是「起了但還沒 ready」或「.env 與容器不一致」—— 混成同一個 exit code
# 會讓呼叫者以為部署失敗而重跑，重跑不會解決驗收問題，只會多動一次容器。
# 這一步**不回滾**前 5 步：回滾一台已經在跑舊版本的機器，比留著一台跑新版本但
# 驗收紅燈更危險。
jget() { python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
p=sys.argv[1].split(".")
for k in p:
    d = d.get(k) if isinstance(d, dict) else None
    if d is None: break
print(d if isinstance(d, str) else "")' "$1" 2>/dev/null || true; }

step_verify() {
  local i body="" got want lver=""
  if [ "$DRY" -eq 1 ]; then
    # dry-run 不真的打 API：兩個理由。一是 --dry-run 的契約是不對外部做任何事，
    # 打 60 秒的迴圈等於偷偷做了一次真實的驗收（而且會等滿 60 秒，因為本機
    # 沒有容器）；二是 dry-run 的目的只是「展示計畫」。
    say "  [dry-run] 將執行: 輪詢 $API_URL/health 直到 ready（最多 ${VERIFY_TRIES}×${VERIFY_WAIT}s）"
    say "  [dry-run] 然後比對 host_id 是否等於 .env 的 HOST_ID，並檢查 /status?probe=0 的 law_version"
    return 0
  fi
  say "  等 $API_URL 起來（最多 ${VERIFY_TRIES}×${VERIFY_WAIT}s；剛 up 完還沒 ready 是正常的）"
  for i in $(seq 1 "$VERIFY_TRIES"); do
    if body="$(curl -sf -m 3 "$API_URL/health" 2>/dev/null)"; then
      break
    fi
    [ "$i" -lt "$VERIFY_TRIES" ] && sleep "$VERIFY_WAIT"
  done
  if [ -z "$body" ]; then
    warn "verify: $API_URL/health 沒有回應（等滿 ${VERIFY_TRIES}×${VERIFY_WAIT}s）"
    warn "verify: 容器沒起來，或 api 沒綁在 127.0.0.1:8000（compose 只綁本機）"
    return 1
  fi
  # host_id 必須是本機宣告的身分。這是「靜默裝錯角色」的唯一攔截點：
  # registry.py 拿不到 HOST_ID 時會 fallback，而症狀是這台的資料寫進別台的名下
  # （/hosts 看起來正常，資料卻混了）。錯了就講清楚兩個身分各是什麼。
  got="$(printf '%s' "$body" | jget host_id)"
  want="$(host_id_of)"
  if [ "$got" != "$want" ]; then
    warn "verify: /health 的 host_id 是 '${got:-（空）}'，但 .env 寫的是 '${want}'"
    warn "verify: 這台的資料會被登記到別的名下。確認 .env 的 HOST_ID 與 compose 有沒有生效"
    return 1
  fi
  say "  ok host_id=${got}"
  # law_version 在 /status 上，**不在** /health（查過 main.py：/health 回的是
  # ok/collection/llm/llm_src/host_id/hostname/machine_id/ips）。要帶 probe=0：
  # 預設的 probe=1 會去探測另外兩台，A→B→C→A 互相探測請求數指數成長，
  # 而且慢到會撞上逾時（sync-snapshot.sh 註解裡也踩過）。
  body="$(curl -sf -m 5 "$API_URL/status?probe=0" 2>/dev/null || true)"
  lver="$(printf '%s' "$body" | jget law_version.update_date)"
  if [ -z "$lver" ]; then
    warn "verify: law_version 沒有值（/status?probe=0）"
    warn "verify: source 機還沒跑過 sync_daily.py，或備援機的 sync-snapshot.sh 還沒同步過"
    return 1
  fi
  say "  ok law_version=${lver}"
  return 0
}

# ── 主流程 ───────────────────────────────────────────────────────────────────
hdr "host-sync on $(host_id_of)（${ROOT}）"
if [ "$DRY" -eq 1 ]; then
  say "模式: dry-run（不寫檔、不 fetch、不切 ref、不動容器）"
fi

hdr "1/6 preflight"
if preflight; then
  say "  ok 可以部署"
  PREFLIGHT_RC=0
else
  PREFLIGHT_RC=1
  if [ "$DRY" -eq 1 ]; then
    say "  阻擋（dry-run 仍會走完第 2-6 步好讓你看到完整計畫，但實際執行會在這裡 exit ${EXIT_PREFLIGHT}）"
  fi
fi

# 為什麼 dry-run 即使被阻擋也要走完：--dry-run 的用途就是「印出會做什麼」，
# 而「會不會被擋」本身就是計畫的一部分。只印到阻擋點為止等於回答一半。
# 安全前提：dry-run 底下第 2-5 步的 mutating 部分都換成了 echo，
# 唯一還真的在執行的只有 rev-parse / symbolic-ref（純讀取）。
# 真實模式一律在阻擋時直接停 —— 那裡沒有任何「只讀」的藉口。
if [ "$PREFLIGHT_RC" -eq 0 ] || [ "$DRY" -eq 1 ]; then
  hdr "2/6 fetch"
  step_fetch
  hdr "3/6 ref"
  step_ref
  hdr "4/6 env-sync"
  step_env_sync
  hdr "5/6 compose"
  step_compose
  hdr "6/6 verify"
  if [ "$SKIP_VERIFY" -eq 1 ]; then
    say "  --skip-verify：略過。"
    say "  這**不是**驗收通過。本機沒有容器在跑時用這個（例如 wsl 目前的狀態）。"
    say "  要真的驗：容器起來後跑 scripts/host-doctor.sh"
    VERIFY_RC=2
  elif [ "$DRY" -eq 1 ]; then
    step_verify
    VERIFY_RC=4
  else
    if step_verify; then
      VERIFY_RC=0
    else
      VERIFY_RC=1
    fi
  fi
else
  say "  （preflight 阻擋，略過第 2-6 步；不會有任何改動）"
  VERIFY_RC=3
fi

# ── 摘要 ─────────────────────────────────────────────────────────────────────
hdr "摘要"
say "  ref        ${REF:-（未指定，跟目前分支走）}"
say "  commit     $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
say "  驗收       $(case "$VERIFY_RC" in
    0) echo 通過 ;;
    1) echo 未通過 ;;
    2) echo 略過（--skip-verify） ;;
    3) echo 未執行（前面的步驟沒跑） ;;
    4) echo '預覽（dry-run：以上都還沒真的做）' ;;
  esac)"

# 這條提醒放這裡是因為它只在「剛部署完」這個時機有意義：第 5 步重啟了容器，
# 但 pg 的 role 密碼只在 volume 首次初始化時由 POSTGRES_PASSWORD 寫進去。
if [ "$VERIFY_RC" -ne 3 ] && [ "$DRY" -eq 0 ]; then
  say "  提醒      換了 POSTGRES_PASSWORD 的話，compose up 不會更新 pg role"
  say "            （要 ALTER USER rag PASSWORD）。本腳本刻意不做 —— 那是 M1 的領域。"
fi

if [ "$PREFLIGHT_RC" -ne 0 ]; then
  exit "$EXIT_PREFLIGHT"
fi
case "$VERIFY_RC" in
  0) exit "$EXIT_OK" ;;
  1) warn "host-sync: 部署步驟全部成功，但**驗收未通過**。容器留在新版本上，"
     warn "host-sync: 請跑 scripts/host-doctor.sh 看是哪一台／哪個鍵。"
     exit "$EXIT_VERIFY" ;;
  2) exit "$EXIT_SKIPPED" ;;
  4) exit "$EXIT_OK" ;;   # dry-run：預覽本身算成功；真正的驗收在非 dry-run 才有
  *) exit "$EXIT_STEP" ;;
esac
