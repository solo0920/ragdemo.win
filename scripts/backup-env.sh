#!/usr/bin/env bash
# backup-env.sh — 把**只有這台有、不可重建**的東西加密備份一份
#
# 為什麼只備份 2 把，而不是整份 .env：
#   .env 的 39 個鍵分三層，只有中間那層是「不可重建」的。
#
#   1. 共用憑證 8 把     → 另兩台的 .env 也有一份（`pull` 寫進去的明文）。
#                         全滅的話走 settings/env/README.md §12b 的災難復原。
#   2. **per-host 2 把** → **只有這台有**。刻意不進 sops（「不分發」是這兩把的
#                         定義），別台的 .env 裡沒有，加密檔裡也沒有（實測 0 次）。
#                         丟了 = 這台的 qdrant 讀不到、pg 打不開，而且**沒有任何
#                         來源可以重建** —— 值是當初隨機選出來的。
#   3. 設定值 29 鍵      → 可由 `settings/env/hosts.shared.env` 的總表
#                         `env-sync.sh render` 重建（空值＝沿用本機現值那幾鍵
#                         除外，但那幾鍵不值錢）。
#
# 加密給**誰**：這台自己的 age 公鑰（`-r`）。
#   ⚠️ 為什麼不是給別台：per-host 機密「不分發」是這個專案刻意維持的邊界 ——
#      給別台的公鑰等於讓別台能解密它，那條線就破了。而「自己解自己的」在
#      實務上完全夠用：本機磁碟掛掉時，**age 私鑰也一起沒了**，但私鑰另有備份
#      （見下），所以鏈是：磁碟掛 → 從私鑰備份拿回私鑰 → 解開這份備份。
#   ⚠️ 因此這份備份的可靠性**完全取決於 age 私鑰備份**。私鑰備份沒做或過期，
#      這份備份就是一份解不開的檔案。兩者必須一起做。
#
# 用法：bash scripts/backup-env.sh [--out DIR] [--print-fingerprints]
#   --out DIR   輸出目錄（預設 ~/ragdemo-backup）。**必須在 repo 之外** ——
#               放進 repo 會被 git add 看到，而那是個會誘使人 commit 的檔案。
#   --print-fingerprints  印出這台的 HOST_ID 與備份檔的指紋（不印值），供回報
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEYS_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
OUT_DIR="$HOME/ragdemo-backup"

# 與 env-sync.sh 的 PER_HOST_SECRETS 同一份真相，不在這裡重列一份
# （列一份 = 兩處會漂移；host-doctor.sh 曾因此硬寫「7 把」而過時）。
PER_HOST_SECRETS=$(sed -n 's/^PER_HOST_SECRETS="\(.*\)"$/\1/p' "$ROOT/scripts/env-sync.sh")

die() { echo "backup-env: $*" >&2; exit 1; }
info() { echo "  $*"; }

PRINT_FP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --out) [ $# -ge 2 ] || die "--out needs a value"; OUT_DIR="$2"; shift ;;
    --print-fingerprints) PRINT_FP=1 ;;
    -h|--help) sed -n '2,/^set -/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

command -v age >/dev/null 2>&1 || die "missing tool: age（見 settings/env/README.md §11）"
[ -f "$KEYS_FILE" ] || die "no age private key at $KEYS_FILE
  這支腳本要用 age 公鑰加密，而公鑰是從私鑰檔推不出來的（只能讀 keys.txt 裡
  那一行公鑰）。見 settings/env/README.md §11 步驟 2。"
# 只取第一把公鑰：keys.txt 是「註解行 + 公鑰行」交錯的格式
PUB="$(grep -oE 'age1[0-9a-z]+' "$KEYS_FILE" | head -1)"
[ -n "$PUB" ] || die "keys.txt 裡找不到 age 公鑰（格式不對？）：$KEYS_FILE"

ENV_FILE="$ROOT/.env"
[ -f "$ENV_FILE" ] || die "no such file: $ENV_FILE"

HOST_ID="$(grep -m1 -E '^HOST_ID=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)"
[ -n "$HOST_ID" ] || die ".env 沒有 HOST_ID（無法命名備份檔；先跑 env-sync.sh pull）"

# ⚠️ 拒絕寫進 repo：這個檔案叫「.env 的備份」，放在 repo 裡時
#    `git add -A` 會把它撈進去，而它的檔名不帶任何「這是密文」的線索。
case "$(cd "$OUT_DIR" 2>/dev/null && pwd || echo "$OUT_DIR")" in
  "$ROOT"|"$ROOT"/*) die "輸出目錄在 repo 內（${OUT_DIR}）。
    備份檔必須在 repo 之外 —— 它是明文 .env 的加密檔，放進 repo 會被 git add
    撈走，而 commit 出去就等於把『三把共用憑證的副本』放上公開版控。
    換一個 --out，或用預設的 ~/ragdemo-backup。" ;;
esac

# 抽值。只抽 PER_HOST_SECRETS 那幾行，不整份複製 —— 整份會把 8 把共用憑證
# 也放進這份備份，而它們另有來源（別台的 .env／§12b 災難復原），且那會讓
# 這份檔案變成第二份共用憑證副本，多一份要輪換的東西。
TMP="$(mktemp "${TMPDIR:-/tmp}/.bk.XXXXXX")"
chmod 600 "$TMP"
cleanup() { [ -f "$TMP" ] && { shred -u "$TMP" 2>/dev/null || rm -f "$TMP"; }; }
trap cleanup EXIT

missing=""
for k in $PER_HOST_SECRETS; do
  v="$(grep -m1 -E "^${k}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2-)"
  if [ -n "$v" ]; then
    printf '%s=%s\n' "$k" "$v" >>"$TMP"
  else
    missing="$missing $k"
  fi
done

if [ -n "$missing" ]; then
  die ".env 缺少 per-host 機密的值：$missing
  沒有值就不會寫進備份 —— 那會產出一份『看起來完整、實際少了東西』的備份，
  而缺值要等到復原當天才發現。該鍵是空的話先確認 .env 真的有設定。"
fi

n=$(grep -c . "$TMP" || true)
[ "$n" -eq 2 ] || die "抽出 $n 行，預期 2 行 —— PER_HOST_SECRETS 的解析或 .env 格式有問題"

mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"
OUT="$OUT_DIR/${HOST_ID}-perhost-secrets.env.age"
TMP2="$(mktemp "${TMPDIR:-/tmp}/.bk2.XXXXXX")"
chmod 600 "$TMP2"
# -a：不把檔名/時間戳寫進 age 的 header。帶 metadata 的話，備份檔會洩漏
# 「這是什麼檔案、最後修改什麼時候」—— 對一個公開 repo 的解密材料而言，
# 那是免費送出去的資訊。
age -r "$PUB" -a -o "$TMP2" "$TMP"
chmod 600 "$TMP2"
mv "$TMP2" "$OUT"
shred -u "$TMP2" 2>/dev/null || rm -f "$TMP2"

fp() { python3 -c 'import hashlib,sys; print("len=%d sha12=%s" % (len(sys.argv[1]), hashlib.sha256(sys.argv[1].encode()).hexdigest()[:12]))' "$1"; }

info "已寫入 $OUT"
info "加密給本機 age 公鑰 sha12=$(printf '%s' "$PUB" | sha256sum | cut -c1-12)（前 12 = ${PUB:0:12}…）"
info "內容：$(printf '%s' "$PER_HOST_SECRETS" | tr ' ' ',')（值不印）"
info "⚠️ 這份備份要用 age 私鑰才解得開 —— 私鑰備份沒做的話，它是解不開的檔案。"

if [ "$PRINT_FP" = 1 ]; then
  echo
  echo "  請回報這三行（不要回報任何值）："
  while IFS= read -r line; do
    printf '  %-24s %s\n' "$(printf '%s' "${line%%=*}" | cut -c1-24)" "$(fp "$(printf '%s' "${line#*=}" | cut -d= -f2-)")"
  done <"$TMP"
  echo "  備份檔 sha12=$(sha256sum "$OUT" | cut -c1-12)"
fi

exit 0
