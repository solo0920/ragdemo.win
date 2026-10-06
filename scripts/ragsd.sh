#!/bin/sh
# 本檔的正式副本在 repo：scripts/ragsd.sh（2026-10-06 納入版控）。
# ~/.local/bin/ragsd.sh 是指向它的 symlink —— 兩邊都不該各自漂移。
# 為什麼納入版控：這四支是本機日常操作的唯一入口（起／收／關機），
# 卻只存在於 ~/.local/bin。磁碟掛了會全沒，而且下一手讀 handoff 時
# 無從知道它們存在 —— 會誤以為「沒有 watchdog 排程」是故障。
set -e

# 完整收機：取消排程 → 收 stack → 關機。
#
# 就是 ragdown.sh + sd.sh 串起來。用絕對路徑而不是靠 PATH ——
# 這只在開機／關機前跑，那種時刻本來就最不該假設環境是乾淨的。
"$HOME/.local/bin/ragdown.sh"
"$HOME/.local/bin/sd.sh"