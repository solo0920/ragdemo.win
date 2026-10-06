#!/bin/sh
# 本檔的正式副本在 repo：scripts/ragdown.sh（2026-10-06 納入版控）。
# ~/.local/bin/ragdown.sh 是指向它的 symlink —— 兩邊都不該各自漂移。
# 為什麼納入版控：這四支是本機日常操作的唯一入口（起／收／關機），
# 卻只存在於 ~/.local/bin。磁碟掛了會全沒，而且下一手讀 handoff 時
# 無從知道它們存在 —— 會誤以為「沒有 watchdog 排程」是故障。
set -e

# 取消 ensure-stack.sh 的自動重啟。
#
# 為什麼一定要先取消：`*/10 * * * *` 那條會在十分鐘內把 container 拉回來。
# 只做 `docker compose down` 的話，開機後它會自己起來 —— 那「shutdown」
# 就只是暫時的，下次開機 stack 又活了。
_cron="$(mktemp)"
crontab -l 2>/dev/null | grep -v 'ensure-stack\.sh' >"$_cron" || true
crontab "$_cron" 2>/dev/null || true
rm -f "$_cron"

cd ~/projects/ragdemo.win
docker compose down

echo "排程已取消、stack 已收掉。要關機請自己跑 sd.sh"