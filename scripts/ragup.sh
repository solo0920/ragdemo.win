#!/bin/sh
# 本檔的正式副本在 repo：scripts/ragup.sh（2026-10-06 納入版控）。
# ~/.local/bin/ragup.sh 是指向它的 symlink —— 兩邊都不該各自漂移。
# 為什麼納入版控：這四支是本機日常操作的唯一入口（起／收／關機），
# 卻只存在於 ~/.local/bin。磁碟掛了會全沒，而且下一手讀 handoff 時
# 無從知道它們存在 —— 會誤以為「沒有 watchdog 排程」是故障。
set -e

cd ~/projects/ragdemo.win

# 把 ensure-stack.sh 的自動修護排程寫回來 —— `ragdown.sh` 會移除它，
# 否則收掉 stack 之後就再也沒有「容器掉了自己回來」這件事了。
#
# 冪等：已經有就不重複寫。否則每跑一次 ragup 就多累積一條一樣的。
_line="*/10 * * * * cd $HOME/projects/ragdemo.win && $HOME/projects/ragdemo.win/scripts/ensure-stack.sh --cron >>$HOME/projects/ragdemo.win/data/ensure-stack.log 2>&1"
if crontab -l 2>/dev/null | grep -q 'ensure-stack\.sh'; then
    echo "排程已存在，沒重複加"
else
    (crontab -l 2>/dev/null; printf '%s\n' "$_line") | crontab -
    echo "已加回 ensure-stack 的排程"
fi

bash scripts/ensure-stack.sh