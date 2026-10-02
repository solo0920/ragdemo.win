#!/usr/bin/env bash
# wait-stack.sh — 等 stack 真的就緒，**不要盲等固定秒數**。
#
# ## 為什麼要有這支
#
# 2026-10-02 量到的成本（wsl，本機磁碟、服務早就熱的狀態）：
#
#     docker compose ps / curl / bash -n / env-sync --check   < 0.1s
#     docker compose up -d ; sleep 22 ; curl /health           22.7s
#     完整 pytest（480 tests）                                  32.8s
#
# 也就是說每次驗證的時間**全部在那個 `sleep`** —— 真正的指令是瞬間完成的。
# 而 `sleep 22` 既是下限也是上限：服務 2 秒就起來時它多花 20 秒，服務 25 秒
# 才起來時它又不夠（於是「驗證失敗」，其實只是還沒好）。
#
# **`sleep` 的兩種失敗都是同一個病**：它假裝自己在等，其實什麼都沒觀察。
#
# ## 這支實際觀察什麼
#
# 1. `docker compose ps --format json` 的 **State / HealthStatus**
# 2. `api` 的 `/health` 回 200
#
# 兩者都滿足才回 0。逾時回 1 並**印出當下的實際狀態** —— 那比「睡醒之後
# 看到 500」有用得多，因為它直接說出是「還在 starting」還是「已經 unhealthy」。
#
# 用法：`bash scripts/wait-stack.sh [--timeout N] [--no-recreate]`
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-${0}}")/.." && pwd)"
cd "${ROOT}"

TIMEOUT=90
RECREATE=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --timeout)     TIMEOUT="$2"; shift 2 ;;
    --no-recreate) RECREATE=0; shift ;;
    -h|--help)     sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "wait-stack: 不認得的引數 $1" >&2; exit 2 ;;
  esac
done

started=$(date +%s)
if [ "${RECREATE}" -eq 1 ]; then
  docker compose up -d 2>&1 | sed 's/^/  /' || true
fi

# 輪詢間隔 0.4s：夠快不會浪費，又不會在剛啟動時把 CPU 燒掉。
# ⚠️ 不用 `sleep 1`：那種整數間隔在「0.9s 就好」的情況下會變成 1s，
#   而這支存在的全部意義就是**不讓等待變成下限**。
prev=""
while :; do
  now=$(date +%s)
  elapsed=$(( now - started ))
  [ "${elapsed}" -ge "${TIMEOUT}" ] && break

  snap=$(docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null || true)
  if [ "${snap}" != "${prev}" ]; then
    echo "  [${elapsed}s] ${snap}" | tr '\n' '|' | sed 's/|$//;s/|/  /g'
    echo
    prev="${snap}"
  fi

  # 三個條件：服務都 running、都 healthy（沒有 healthcheck 的算通過）、
  # api 的 /health 回 200。
  containers_ok=$(docker compose ps --format '{{.State}}' 2>/dev/null \
    | grep -c '^running$' || true)
  total=$(docker compose ps --format '{{.Service}}' 2>/dev/null | grep -c . || true)
  unhealthy=$(docker compose ps --format '{{.Health}}' 2>/dev/null \
    | grep -cE 'unhealthy|starting' || true)

  if [ "${containers_ok}" -eq "${total}" ] && [ "${total}" -gt 0 ] \
     && [ "${unhealthy}" -eq 0 ] \
     && curl -s -o /dev/null -m 3 http://localhost:8000/health; then
    echo "  ✓ 就緒，耗 ${elapsed}s"
    exit 0
  fi
  sleep 0.4
done

echo "  ✗ ${TIMEOUT}s 內沒就緒。當下狀態：" >&2
docker compose ps --format '    {{.Service}}\t{{.State}}\t{{.Health}}' >&2 || true
echo "  最後 20 行 api log：" >&2
docker compose logs --tail 20 api >&2 || true
exit 1
