#!/bin/sh
# 本檔的正式副本在 repo：scripts/sd.sh（2026-10-06 納入版控）。
# ~/.local/bin/sd.sh 是指向它的 symlink —— 兩邊都不該各自漂移。
# 為什麼納入版控：這四支是本機日常操作的唯一入口（起／收／關機），
# 卻只存在於 ~/.local/bin。磁碟掛了會全沒，而且下一手讀 handoff 時
# 無從知道它們存在 —— 會誤以為「沒有 watchdog 排程」是故障。
set -e

# 只關機，不碰 stack。
#
# `ragdown.sh` 是「取消排程 + 收 stack」，那個**不關機** —— 要關機自己跑這隻。
# 分開的理由：關機是唯一不可逆的那一步，不該和「收 stack」綁在一起，
# 否則想先收 stack 看看結果就得承擔整台機器的下線。
#
# 免密：`sudo shutdown` 需要 sudoers 規則（見本檔末尾的說明），
# 沒有那條規則時這隻會停下來要密碼 —— 那是**正確**的行為，不要繞。
sudo shutdown -h now