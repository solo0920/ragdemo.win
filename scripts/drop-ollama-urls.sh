#!/usr/bin/env bash
# drop-ollama-urls.sh — 三台統一刪除 `OLLAMA_URLS`（2026-10-02 決定）。
#
# ## 為什麼是「三台統一」而不是各台各自決定
#
# 這個鍵的狀態是三台**不一致**的（2026-10-02 三機比對）：
#   * 總表 `wsl_OLLAMA_URLS` / `mbp_OLLAMA_URLS` / `x570_OLLAMA_URLS` 全是空值
#   * wsl 與 mbp 的 `.env` 卻**有值** → 那些值**不是 render 來的**
#     （`env-sync.sh --check` 只收有值的列進 layer，空列根本不比較，所以漂移
#     是隱形的 —— 後來加了 `⚠️ 沿用自己的值` 那條警告才看見）
#   * x570 沒有這一行
#
# 「一台有、另一台沒有」本身就是規格沒達成的形狀：**鍵集合不同 → 版面指紋
# 永遠對不上**。所以決定是**三台全刪**，不是「各自決定要不要留」。
#
# ## 這個鍵是什麼（別在之後忘記）
#
# `OLLAMA_URLS` 是 **gateway 連 ollama 的位址清單**（`gateway.py:72`），
# compose 預設是 `http://host.docker.internal:11434`。刪掉之後：
#
#   * gateway 的 ollama backend 會落到 compose 預設
#   * 2026-10-02 在 wsl 實測過 `http://127.0.0.1:11434` → **HTTP 000（連不上）**
#     —— WSL 的 localhost 到不了 msi 的 ollama
#   * `host.docker.internal` 在容器裡指向 docker host（= WSL），同樣不是 msi
#
# ## ⚠️ 這個鍵不是「可有可無的設定」—— 刪掉會讓**整個 RAG 斷掉**
#
# 2026-10-02 在 wsl 實測（A/B，同一台機、同一個問題、只差這個鍵）：
#
#   刪掉 OLLAMA_URLS → 查詢 **HTTP 500**，log 裡
#                     `httpx.ConnectError: ollama unreachable`
#                     掛在 **`/api/embed`**，不是聊天
#   放回去           → 查詢 **HTTP 200**（2.9s）
#
# 為什麼是嵌入：`EMBED_MODEL=bge-m3:latest` 是 **ollama 模型**，所以每次查詢
# 都會先要 ollama 算嵌入。**沒有 ollama 位址 = 檢索與生成都做不了**，
# 整條 RAG 不動 —— 而症狀是 `/health` 一直 200、只有查詢 500。
#
# **所以 `/health` 抓不到這個。** 唯一抓得到的是真的打一次 `/query`
# （`.githooks/pre-push` 的 HTTP smoke 會，但**預設不跑** —— 需要
# `RAGDEMO_SMOKE=1`）。2026-10-02 這次刪除就是在 `/health` 全綠的情況下
# 完成的，是後來手動查了一次才發現。
#
# ⚠️ **三台同規格有兩種做法，選錯那個會讓 wsl 的 RAG 死掉：**
#
#   (a) 三台全刪（這個腳本做的事）
#       → 規格達成，但 **wsl 沒有 ollama 位址 → RAG 全斷**（已實測）
#   (b) 保留總表的三列，**只把 mbp 那個「總表不管」的值清掉**
#       → 三台的 `.env` 都只有 `OLLAMA_URLS=` 這一行（值可以不同），
#         鍵集合相同 → 指紋相同；mbp 的漂移消失（因為它沒有值了）；
#         **wsl 照常能用**（值由總表的 `wsl_OLLAMA_URLS` 列管）
#
# (b) 同時達成使用者的兩個目標（三台同規格 ＋ 漂移消失）而不弄壞東西。
# 本腳本只實作 (a)，因為那是明確指定的；要在 (b) 與 (a) 之間選請先問。
#
# `OLLAMA`（單一埠，ingest 管線用 `ingest/laws/qdrant_load.py:29`）是**另一個
# 鍵**，本腳本不碰它 —— 刪掉那個會讓 ingest 的嵌入全斷（理由見
# `env-prune.py` 與 `settings/env/ENV-SPEC.md §一 D`）。
#
# ⚠️ 這是使用者的決定。指令是「統一全刪」，本腳本的責任是**照做並回報實測
# 結果**，不是自行保留。
#
# ## 這個腳本怎麼做到「三台都能跑」
#
# 被追蹤的總表只改一次（從任一台改都一樣，因為那是同一個檔案），
# `.env` 則各機自己改 —— `.env` 有 10 把憑證，**不能離開那台機器**。
# 所以同一支腳本在兩種情況下的行為不同，但都是**冪等**的：
#
#   * 總表已經沒有那三列 → 略過（別人已經改過並 push 了）
#   * `.env` 已經沒有那一行 → 略過（自己跑過了）
#
# ⚠️ **bash 3.2 安全**：macOS 的 bash 3.2 會把全形字元當識別字元，
#   `$VAR（…` 會被當成變數名 `VAR（` → `set -u` 下整支腳本死掉。
#   本專案在 2026-10-02 為此踩過（`env-sync.sh:561`）。**一律寫 `${VAR}`。**
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-${0}}")/.." && pwd)"
DOTENV="${ROOT}/.env"
TABLE="${ROOT}/settings/env/hosts.shared.env"
KEY="OLLAMA_URLS"

die() { echo "drop-ollama-urls: $*" >&2; exit 1; }
note() { echo "  $*"; }

[ -f "${DOTENV}" ] || die "找不到 .env —— 請在 repo 根目錄跑"
[ -f "${TABLE}" ] || die "找不到 settings/env/hosts.shared.env"

echo "drop-ollama-urls: 準備移除 ${KEY}"

# ── 1. 總表：移除 <機台>_<鍵>= 的三列 ──────────────────────────────────
# 這是被追蹤的檔案，所以改了要 commit（本腳本不自動 commit —— 那是不可逆的
# 動作，而且三台同時跑會互相衝突）。
if grep -qE '^(x570|mbp|wsl)_'"${KEY}"'=' "${TABLE}"; then
  # ⚠️ **被追蹤的檔案不需要另一份 .bak —— `git` 就是安全網。**
  # 第一版在 repo 內放 `hosts.shared.env.dropbak`：那是個沒在白名單裡的
  # 追蹤檔，會被 `env-sync.sh --check` 擋下（那條檢查抓 per-host 真值外泄，
  # 而檔名完全看不出它是備份）。改放 `~` 之後仍然錯：我實跑過一次就刪了
  # `.dropbak`，等第二次要還原時**已經沒有備份**，最後靠 `git checkout` 取回。
  #
  # 所以：`.env`（未追蹤、有 10 把憑證）**必須**備份到 `~`；
  # 總表（已追蹤）**不要**備份，要還原就用 `git checkout` —— 那才是完整、
  # 有歷史、而且不會被 `.gitignore` 漏掉的版本。
  sed -i.bak -E "/^(x570|mbp|wsl)_${KEY}=/d" "${TABLE}" && rm -f "${TABLE}.bak"
  note "總表：已移除 ${KEY} 的三列（要還原：git checkout settings/env/hosts.shared.env）"
else
  note "總表：已經沒有 ${KEY} 的列（別人已改過）"
fi

# ── 2. .env：移除那一行 ────────────────────────────────────────────────
# 只刪「開頭就是 KEY=」或「註解掉的 # KEY=」的行，避免誤傷理由註解裡提到
# 這個鍵名的文字（`env-prune.py` 的 COMMENT 理由會寫到它）。
if grep -qE '^#? *'"${KEY}"'=' "${DOTENV}"; then
  bak="${HOME}/.ragdemo-env.bak-drop-$(date +%Y%m%d-%H%M%S)"
  cp "${DOTENV}" "${bak}"
  chmod 600 "${bak}"
  # 連帶刪掉緊接在上面的**屬於同一個鍵的**註解行：那是 env-prune 寫的理由，
  # 鍵被刪了理由就懸空。只刪「以 # 開頭且非空」的行，遇到空行就停 ——
  # 那樣不會跨到別的鍵。
  awk -v key="${KEY}" '
    BEGIN { skip = 0 }
    {
      if ($0 ~ ("^#? *" key "=")) { skip = 1; next }
      if (skip && $0 ~ /^#[^=]*$/) { next }      # 同一鍵的理由註解
      skip = 0
      print
    }
  ' "${bak}" > "${DOTENV}.tmp" && mv "${DOTENV}.tmp" "${DOTENV}"
  chmod 600 "${DOTENV}"
  note ".env：已移除該行 → ${bak}"
else
  note ".env：已經沒有 ${KEY} 那一行（自己跑過了）"
fi

# ── 3. 驗證 ───────────────────────────────────────────────────────────
fail=0
if grep -qE '^#? *'"${KEY}"'=' "${DOTENV}"; then
  echo "  ✗ .env 裡還有 ${KEY} 那一行" >&2; fail=1
else
  note "驗證：.env 已無 ${KEY} ✓"
fi
if grep -qE '^(x570|mbp|wsl)_'"${KEY}"'=' "${TABLE}"; then
  echo "  ✗ 總表還有 ${KEY} 的列" >&2; fail=1
else
  note "驗證：總表已無 ${KEY} 的列 ✓"
fi
if [ "${fail}" -ne 0 ]; then
  die "有東西沒清掉 —— 已改的檔案可從上面的備份還原"
fi

echo
# ⚠️ 2026-10-03：原本印的是**裸 `git commit`**，而那會**開啟編輯器**等人寫訊息。
#   2026-10-03 實測：有人照著 repo 印出的指令執行，結果卡在編輯器畫面。
#   **指令被印出來就是會被照著跑**，所以它必須假設自己跑在**沒有人互動**的環境裡
#   （cron、貼進聊天、貼進 issue）。所以：`git commit` 一律帶 `-m`，
#   `git pull` 一律帶 `--ff-only`（那按定義不會生出 merge commit，也不會開編輯器）。
echo "⚠️  如果這是第一次執行，記得 commit 總表的改動："
echo "     git add settings/env/hosts.shared.env && git commit -m 'wsl: 更新 hosts.shared.env'"
echo
echo "接著："
echo "  python3 scripts/env-prune.py          # 清掉其它機器可能殘留的空行"
echo "  bash scripts/env-sync.sh pull && bash scripts/env-sync.sh render"
echo "  bash scripts/env-sync.sh --check | head -1     # 三台指紋必須相同"
