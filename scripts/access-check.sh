#!/usr/bin/env bash
# access-check.sh — 驗證 Cloudflare Access 的 Service Token 對三台後端都有效
#
# 為什麼要有這支（2026-10-01）：三台各自有獨立的 Access app，而 Service Token
# 是**逐一註冊**到 app 的。Pages 帶 token → wsl 回 200 **只證明 wsl 那個 app
# 認那組 token**，x570／mbp 有沒有註冊完全未知。所以在把 x570／mbp 加回
# Pages 的 API_ORIGINS 之前，必須先驗過 —— 否則加回去會立刻壞，而症狀是
# 「面板顯示連線失敗」，看不出是 Access 擋的還是主機掛的。
#
# 為什麼用 read -rs：token 經過命令行會進 shell history，而這個專案 2026-09-26
# 的三次憑證外洩都是終端機紀錄造成的。read -rs 讓它**完全不落地**。
#
# 用法：
#   bash scripts/access-check.sh                 # 互動式讀 token（推薦）
#   bash scripts/access-check.sh --no-secret     # 只驗 403 守衛，不問 token
#
# 輸出只有狀態碼與「哪幾台通過」，**不印任何憑證值**。
set -uo pipefail

HOSTS="${ACCESS_HOSTS:-api-x570.ragdemo.win api-wsl.ragdemo.win api-mbp.ragdemo.win}"
# worker 的 probe() 要求 status==200 **且** content-type 含 application/json。
# 只看 200 會被 Access 的「302 → 登入頁 → 200 + text/html」騙過去。
CT_JSON='content-type: application/json'

say() { printf '%s\n' "$*"; }

probe() {  # $1=host  $2..=額外 header
  local h="$1"; shift
  curl -sS -m 10 -o /dev/null -w '%{http_code} %{content_type}' "$@" "https://$h/health" 2>/dev/null \
    || printf '000 '
}

# 回傳 "OK" / "BLOCKED" / "BAD"；BAD = 過了 Access 但不是預期的 JSON
verdict() {
  local code="${1%% *}" ct="${1#* }"
  if [ "$code" = "200" ] && [[ "$ct" == *application/json* ]]; then printf 'OK'
  elif [ "$code" = "200" ]; then printf 'JSON?(%s)' "$ct"
  else printf 'BLOCKED(%s)' "$code"
  fi
}

say "=== 1) 守衛：完全不帶 token 必須被擋（403）==="
say "    這是「Access 有生效」的證據。若這裡是 200，代表 Access 沒護到這台。"
GUARD_OK=1
for h in $HOSTS; do
  r="$(probe "$h")"
  printf '  %-28s %s\n' "$h" "$(verdict "$r")"
  case "$(verdict "$r")" in BLOCKED*) ;; *) GUARD_OK=0 ;; esac
done

say ""
say "=== 2) 守衛：帶**假的** Service Token 也必須被擋（403）==="
say "    只擋「沒帶 header」不足以證明 Service Auth 真的在驗簽 ——"
say "    一個錯誤的 token 也被擋，才是它在驗簽而不是只看 header 存在。"
FAKE_OK=1
for h in $HOSTS; do
  r="$(probe "$h" -H 'CF-Access-Client-Id: definitely.not.a.real.id' \
                -H 'CF-Access-Client-Secret: definitely-not-a-real-secret')"
  printf '  %-28s %s\n' "$h" "$(verdict "$r")"
  case "$(verdict "$r")" in BLOCKED*) ;; *) FAKE_OK=0 ;; esac
done

if [ "${1:-}" = "--no-secret" ]; then
  say ""
  say "（--no-secret：略過第 3 項。token 不在這台就沒辦法驗。）"
  [ "$GUARD_OK" = 1 ] && say "✓ 守衛 1 通過" || say "✗ 守衛 1 失敗 —— 有台沒被 Access 護住"
  [ "$FAKE_OK" = 1 ] && say "✓ 守衛 2 通過" || say "✗ 守衛 2 失敗 —— Service Auth 沒在驗簽"
  exit $(( (GUARD_OK && FAKE_OK) ? 0 : 1 ))
fi

say ""
say "=== 3) 真正的測試：帶真 token 必須 200 + JSON ==="
say "    輸入不會回音、不進 shell history。Ctrl-C 可放棄。"
printf '  Service Token Client Id: '
IFS= read -rs CF_ID
printf '\n  Service Token Client Secret: '
IFS= read -rs CF_SECRET
printf '\n'
if [ -z "${CF_ID:-}" ] || [ -z "${CF_SECRET:-}" ]; then
  say "✗ 沒讀到 token，放棄第 3 項。"
  exit 1
fi

REAL_OK=1
for h in $HOSTS; do
  r="$(probe "$h" -H "CF-Access-Client-Id: $CF_ID" \
                -H "CF-Access-Client-Secret: $CF_SECRET")"
  v="$(verdict "$r")"
  printf '  %-28s %s\n' "$h" "$v"
  [ "$v" = "OK" ] || REAL_OK=0
done
unset CF_ID CF_SECRET

say ""
say "-- 結論 --"
[ "$GUARD_OK" = 1 ] && say "✓ 守衛 1：三台都不帶 token 都被擋" || say "✗ 守衛 1：有台沒被 Access 護住"
[ "$FAKE_OK" = 1 ] && say "✓ 守衛 2：三台都拒絕假 token（Service Auth 有驗簽）" || say "✗ 守衛 2：Service Auth 沒在驗簽"
if [ "$REAL_OK" = 1 ]; then
  say "✓ 真 token 對三台都有效 —— 可以安全地把 x570／mbp 加回 API_ORIGINS"
  exit 0
fi
say "✗ 真 token 對至少一台無效 —— **不要**把那些台加回 API_ORIGINS。"
say "  先去 Cloudflare Access → 該台的 app → Service Auth policy 加上這組 token。"
exit 1