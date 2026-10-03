#!/usr/bin/env bash
# rotate-secret.sh — 輪換一把共用憑證的值（改值 + 重加密 + 驗證）
#
# 為什麼要有這支：2026-09-30 x570 實測，曝露的憑證該換但沒換。
# 原因不是忘了，是**流程太痛** —— 要手動 sops -d、改檔、sops -e 回去、
# 而且不知道會不會把 recipients 或其他鍵弄壞。痛到會被拖延，拖延到變成風險。
#
# 這支把那些步驟收成：拿新值 → 換 → 驗 → 印出該做什麼。
#
# ⚠️ 這支**只換值**，不做 rotation（重新加密的資料金鑰）。sops 的 rotate 會換
#   檔案的資料金鑰；換憑證值不需要那個，而且 rotate 會讓「三台 pull 之前」的
#   中間狀態更難推理。憑證值換掉 + 重加密就夠了。
#
# ⚠️ 絕不用 --init-secrets（那是「建立加密檔」，會覆蓋整份，含沒換的那些）。
#   那是本專案 2026-09-27 才有的坑，寫在這裡免得下次有人想「乾脆重建」。
#
# 2026-10-02 加 `--add`：一把新的共用憑證要進加密檔時用。
#   為什麼需要：原本這支碰到加密檔裡沒有的鍵會 die（"key not found"），
#   所以「新增」只能靠 --init-secrets（毀滅性）或手動 sops -d／改檔／sops -e。
#   實測的後果是 CF_ACCESS_CLIENT_ID/SECRET 在 sops 層**缺席一天**，
#   mbp／x570 只能人手貼值（貼反的症狀與「Access 沒開」一樣），而且
#   --fingerprints 看不到那兩把 → 兩台不一致是無聲的。
#   加了 --add 之後，新增走同一條「重加密 → 驗三件事 → 原子取代」的路徑，
#   不再需要動 --init-secrets 那把大錘。
set -euo pipefail

# ROTATE_SECRET_ROOT_OVERRIDE 是**測試專用**（tests/test_rotate_secret.py），
# 讓測試能在 tmp 裡放一份假 repo 而不必碰真實的加密檔。
# 為什麼需要它：2026-09-30 第一版測試直接跑本腳本，路徑寫死 → 測試真的把
# 測試值輪換進了版控中的加密檔，而那檔是加密的，git diff 看不出值變了。
# 它刻意**不讀 .env**（不像其他設定）—— 正式使用時不該有人設它，
# 而 env-audit 的「每個變數都要有讀取處」規則也會要求它出現在 .env.example，
# 那等於在文件裡邀請人設一個只給測試用的變數。
if [ -n "${ROTATE_SECRET_ROOT_OVERRIDE:-}" ]; then
  ROOT="$ROTATE_SECRET_ROOT_OVERRIDE"
else
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi
# sops 找 .sops.yaml 是**從 cwd 往上走**，不是從被加密的檔案的位置。所以在哪個
# 目錄執行這支腳本，會決定用哪一份 creation_rules —— 在 repo 外面跑就會拿到
# 「config file not found, or has no creation rules」。
#
# 這是真實會發生的：2026-09-30 寫測試時在 tmp 造了一份假 repo，卻因為 cwd 還是
# repo 根，sops 用了**真的** .sops.yaml（三把 recipients），於是「輪換後
# recipients 數量 1 → 3」而測試丟出 recipient count changed。症狀完全看不出
# 是「用錯了 config」。
#
# 明顯指定 --config 才不會受 cwd 影響。
SOPS_CONFIG="$ROOT/.sops.yaml"
[ -f "$SOPS_CONFIG" ] || die "no such file: $SOPS_CONFIG"
SOPS_ARGS=(--config "$SOPS_CONFIG")
ENC="$ROOT/settings/env/secrets.common.enc.env"
KEYS_FILE="$ROOT/settings/env/secrets.common.env.example"
DOTENV="$ROOT/.env"

# 與 env-sync.sh 同一份真相，不在這裡重列一份（列一份 = 兩處會漂移；
# 2026-09-30 mbp 就發現 host-doctor.sh 硬寫「7 把」會隨增減過時）。
SHARED_SECRETS=$(sed -n 's/^SHARED_SECRETS="\(.*\)"$/\1/p' "$ROOT/scripts/env-sync.sh")
PER_HOST_SECRETS=$(sed -n 's/^PER_HOST_SECRETS="\(.*\)"$/\1/p' "$ROOT/scripts/env-sync.sh")

die() { echo "rotate-secret: $*" >&2; exit 1; }
info() { echo "  $*"; }

usage() {
  # 只印檔頭那塊說明（到第一個非註解行為止），不要 `sed -n '2,/^set -euo/p'` ——
  # 那會把 `set -euo pipefail` 這行程式碼也印出來（2026-09-30 實測）。
  sed -n '2,${/^#/!q;s/^# \{0,1\}//p;}' "$0"
  cat <<'EOF'
usage: rotate-secret.sh <KEY> --from-stdin | --from-file FILE [--add]
       rotate-secret.sh --list
       rotate-secret.sh --help

  --list             列出可輪換的鍵（共用憑證；per-host 兩把不適用，見下）
  --from-stdin       從標準輸入讀新值（建議；不會留在 shell history）
  --from-file FILE   從檔案讀。**不會刪掉那個檔**（只提醒你它是明文）
  --add              這把在加密檔裡**還沒有**，把它加進去（新增共用憑證用）
                     已存在時會 die —— 免得「以為加了」其實什麼都沒變

per-host 兩把（QDRANT_API_KEY / POSTGRES_PASSWORD）**不能用這支換** ——
它們不在加密檔裡（各機自己的值，刻意不進共用層），請直接改該機的 .env。
EOF
}

# ── 判斷鍵是否可輪換 ─────────────────────────────────────────────────────
list_keys() {
  for k in $SHARED_SECRETS; do echo "  $k"; done
  echo
  echo "  per-host（不適用本腳本，各機 .env 自行改）："
  for k in $PER_HOST_SECRETS; do echo "    $k"; done
}

[ $# -ge 1 ] || { usage; exit 1; }

case "$1" in
  --help|-h) usage; exit 0 ;;
  --list) list_keys; exit 0 ;;
esac

KEY="$1"; shift
SRC=""
ADD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --from-stdin)  SRC="stdin" ;;
    --from-file)   [ $# -ge 2 ] || die "--from-file needs a path"
                    SRC="$2"; shift ;;
    --add)         ADD=1 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done
[ -n "$SRC" ] || { usage; exit 1; }

# ── 前置檢查 ─────────────────────────────────────────────────────────────
command -v sops >/dev/null 2>&1 || die "missing tool: sops（見 settings/env/README.md §11）"
[ -f "$ENC" ] || die "no such file: $ENC"
[ -f "$KEYS_FILE" ] || die "no such file: $KEYS_FILE"

# 鍵必須是共用憑證之一。用比對而不是 grep -q $KEY（會把 ADMIN 誤中 ADMIN_TOKEN2）。
valid=0
for k in $SHARED_SECRETS; do [ "$k" = "$KEY" ] && valid=1; done
if [ "$valid" -ne 1 ]; then
  die "'$KEY' is not a shared secret. 可輪換的鍵："
  list_keys >&2
fi

# 私鑰：updatekeys / re-encrypt 都要有。缺了就在這裡講清楚，不要讓 sops 報
# 「no identity matched any of the recipients」—— 那個訊息同時涵蓋
# 「名單裡沒我」和「我沒私鑰」，2026-09-30 wsl 就是後者（重灌沒還原私鑰）。
KEYFILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
if [ ! -f "$KEYFILE" ]; then
  die "no age private key at $KEYFILE
  這支腳本需要**任一把**在 .sops.yaml recipients 裡的私鑰（不必是 $KEY 那把的）。
  沒有私鑰的症狀是 sops 報 'no identity matched any of the recipients'，
  那句話聽起來像「名單裡沒有我」，實際可能是「我在名單裡但私鑰不見了」。
  見 settings/env/README.md §11 步驟 2。"
fi

# ── 讀新值 ───────────────────────────────────────────────────────────────
tmp_in=""; tmp_work=""
cleanup() {
  for f in $tmp_in $tmp_work; do
    [ -n "$f" ] && [ -f "$f" ] && { shred -u "$f" 2>/dev/null || rm -f "$f"; }
  done
}
trap cleanup EXIT

if [ "$SRC" = "stdin" ]; then
  tmp_in="$(mktemp "${TMPDIR:-/tmp}/.newval.XXXXXX")"
  chmod 600 "$tmp_in"
  cat > "$tmp_in"
else
  [ -f "$SRC" ] || die "no such file: $SRC"
  tmp_in="$(mktemp "${TMPDIR:-/tmp}/.newval.XXXXXX")"
  chmod 600 "$tmp_in"
  cat "$SRC" > "$tmp_in"
  # **不 shred 來源檔**。曾經 shred 過，後來改掉：那是「猜使用者想要什麼」。
  # 來源檔可能是使用者刻意留在某處的（版本控制用的範本、貼給別台的片段），
  # 刪掉是無法復原的資料損失。只提醒，不動手。
  echo "  提醒：來源檔 $SRC 仍留在磁碟（明文）。刪除請自己處理：" >&2
  echo "        shred -u '$SRC'" >&2
fi

# 換行是 key 的一個字元，不是排版 —— 用 printf 不加 \n，並去掉尾端換行。
NEW="$(cat "$tmp_in")"
NEW="${NEW%$'\n'}"
[ -n "$NEW" ] || die "new value is empty（輪換成空值等於刪掉這把，請確認）"
# 值裡有換行 → 多半是把整個 .env 貼進來了，那是 --init-secrets 的事不是這支的
case "$NEW" in
  *$'\n'*) die "new value contains newlines —— 貼到整份 .env 了？這支只換一個鍵的值" ;;
esac

# ── 解密 → 換一行 → 重加密 ───────────────────────────────────────────────
tmp_work="$(mktemp "${TMPDIR:-/tmp}/.rot.XXXXXX")"
chmod 600 "$tmp_work"
SOPS_AGE_KEY_FILE="$KEYFILE" sops --decrypt "${SOPS_ARGS[@]}" --output "$tmp_work" "$ENC"

# sha12 印出來供跨機比對，但**值本身絕不印**（2026-09-26 三次憑證外洩都是
# 「查證時列印了值」，其中一次就是把整份 .env cat 出來）。
fp() { python3 -c "import hashlib,sys; v=sys.argv[1]; print('len=%d sha12=%s' % (len(v), hashlib.sha256(v.encode()).hexdigest()[:12]))" "$1"; }

OLD="$(python3 -c "
import sys,re
for l in open(sys.argv[1],encoding='utf-8'):
    m=re.match(r'^'+re.escape(sys.argv[2])+r'=(.*)$', l.rstrip('\n'))
    if m: print(m.group(1)); break
" "$tmp_work" "$KEY")"

# --add：這把在加密檔裡還沒有。兩種誤用都要在這裡擋下來，而不是靜默成功 ——
#   該加的沒加（漏了 --add）→ die，症狀是「我明明加了但 pull 不到」
#   不該加的重複加（其實是換值卻忘了拿掉 --add）→ die，否則會換成靜默 no-op，
#     使用者以為換掉了，實際上舊值還在流（這比 die 危險得多）
if [ "$ADD" = "1" ]; then
  [ -z "$OLD" ] || die "$KEY 已經在加密檔裡了（len=${#OLD}）—— 那是換值，不是新增；拿掉 --add"
  echo "rotate-secret: ${KEY}（新增）"
  info "加密檔裡原本沒有這把；其他鍵不動"
else
  [ -n "$OLD" ] || die "key not found in decrypted file: $KEY
  這把還不在加密檔裡。要新增請加 --add（那是「加進去」，不是「換掉」）：
      rotate-secret.sh $KEY --add --from-stdin
  另一條路 --init-secrets 會覆蓋整份，別用（見檔頭）。"
fi

if [ -n "$OLD" ]; then
  echo "rotate-secret: $KEY"
  info "舊值 $(fp "$OLD")"
  info "新值 $(fp "$NEW")"
  [ "$OLD" != "$NEW" ] || die "新舊值相同（指紋一樣）—— 確定要輪換的是這一把嗎？"
else
  info "新值 $(fp "$NEW")"
fi

# 只改那一行，其餘原樣。python 而非 sed：值裡可能有 / 與 & 等 sed 特殊字元。
# --add 時該鍵不存在 → append；存在已被上面的 die 擋掉，所以這裡只會是 0 或 1。
python3 - "$tmp_work" "$KEY" "$NEW" "$ADD" <<'PY'
import re, sys
path, key, new, add = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
lines = open(path, encoding="utf-8").read().splitlines()
pat = re.compile(r"^" + re.escape(key) + r"=")
hit = sum(1 for l in lines if pat.match(l))
if add == "1":
    if hit != 0:
        sys.exit(f"--add but {key} already present ({hit} lines)")
    lines.append(f"{key}={new}")
else:
    if hit != 1:
        sys.exit(f"expected exactly 1 line for {key}, found {hit}")
    lines = [f"{key}={new}" if pat.match(l) else l for l in lines]
open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY

# 重加密到暫存檔再取代原檔：直接寫原檔的話，中途失敗會留下半個壞掉的加密檔。
# ⚠️ --filename-override 是必要的，不是多餘的：sops 是**拿檔名**去比對
#   .sops.yaml 的 creation_rules（path_regex: settings/env/secrets\..*\.enc\.env$），
#   暫存檔在 /tmp 不符合 → 報 "no matching creation rules found"。
#   2026-09-30 第一次寫就是踩這個。
tmp_enc="$(mktemp "${TMPDIR:-/tmp}/.enc.XXXXXX")"
chmod 600 "$tmp_enc"
SOPS_AGE_KEY_FILE="$KEYFILE" sops --encrypt "${SOPS_ARGS[@]}" \
  --filename-override "$ENC" \
  --input-type dotenv --output-type dotenv \
  --output "$tmp_enc" "$tmp_work"

# 驗三件事，缺一不可 —— 這是這支腳本存在的主要理由。
#   1) 檔案裡的鍵數與名單一致（不能少一把或多一把）
#   2) recipients 沒被動到（sops -e 會重跑 creation_rules；保險起見驗）
#   3) 換完仍解得開，且換的那把指紋是新值、其餘不變
n_expected=$(python3 -c "
import sys
print(len([l for l in open(sys.argv[1],encoding='utf-8').read().splitlines()
           if l.strip() and not l.startswith('sops_')]))" "$tmp_work")
n_got=$(python3 -c "
import sys
print(len([l for l in open(sys.argv[1],encoding='utf-8').read().splitlines()
           if l.strip() and not l.startswith('sops_')]))" "$tmp_enc")
[ "$n_expected" = "$n_got" ] || die "key count changed: $n_expected → $n_got"

tmp_check="$(mktemp "${TMPDIR:-/tmp}/.chk.XXXXXX")"
chmod 600 "$tmp_check"
# --filename-override 在 decrypt 這邊同樣必要，但理由不同：mktemp 產生的檔名
# 沒有 .enc.env 結尾，sops 會**靠副檔名猜輸入格式**、猜不到就當 JSON 解析 →
# 報 "Could not unmarshal input data: invalid character 'Q'"（2026-09-30 實測，
# Q 是 QDRANT_PEER_API_KEY 的第一個字母）。
SOPS_AGE_KEY_FILE="$KEYFILE" sops --decrypt "${SOPS_ARGS[@]}" \
  --filename-override "$ENC" --input-type dotenv \
  --output "$tmp_check" "$tmp_enc" \
  || die "re-encrypted file cannot be decrypted — aborting, original untouched"

python3 - "$tmp_check" "$tmp_work" "$KEY" "$NEW" "$SHARED_SECRETS" "$ADD" <<'PY'
import hashlib, re, sys
# ⚠️ shared 一定要 .split()。原本寫成 sys.argv[4].split()，改成 unpack 形式時
#   漏掉，症狀是 `[k for k in shared]` 走成**逐字元** → 報
#   missing keys: ['Q','D','R','A','N','T',...]（每個字母一個元素）。
#   那句錯誤訊息看起來像「加密檔壞了」，實際是這裡少一個 .split()。
path, before_path, key, new = sys.argv[1:5]
shared = sys.argv[5].split()
add = sys.argv[6]


def parse(p):
    d = {}
    for l in open(p, encoding="utf-8").read().splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9_]*)=(.*)$", l)
        if m: d[m.group(1)] = m.group(2)
    return d


vals, before = parse(path), parse(before_path)

# 不變量只有一個，而且對兩種模式都要成立：**沒有任何一把在改動中消失**。
# 「SHARED_SECRETS 每把都要在場」是換值時額外想要的 lint（宣告了卻沒建檔＝有人
# 改了清單漏了 run），但對 --add 會誤傷：新增一把時加密檔裡本來就還缺著其他
# 「已宣告待補」的鍵。實測 2026-10-02 加 CF_ACCESS_CLIENT_ID/SECRET 兩把時，
# 這條 lint 讓第一把失敗、第二把又因為另一把不在而失敗 —— 兩把都加不進去，
# 症狀是「不對稱錯誤：說缺的不是我剛加的那把」。那些鍵的缺席由
# `env-sync.sh --check` 報，不是這支的責任。
lost = [k for k in before if k not in vals]
if lost:
    sys.exit(f"keys lost during operation: {lost}")
if add != "1":
    missing = [k for k in shared if k not in vals]
    if missing:
        sys.exit(f"missing keys after rotation: {missing}")
if vals.get(key) != new:
    sys.exit("target key did not take the new value")
def fp(v): return "len=%d sha12=%s" % (len(v), hashlib.sha256(v.encode()).hexdigest()[:12])
for k in sorted(vals):
    print(f"  {k:<24} {fp(vals[k])}")
PY

# recipients 數量：改前 vs 改後
r_old=$(grep -c '^sops_age__list_[0-9]*__map_recipient=' "$ENC" || true)
r_new=$(grep -c '^sops_age__list_[0-9]*__map_recipient=' "$tmp_enc" || true)
[ "$r_old" = "$r_new" ] || die "recipient count changed: $r_old → $r_new"

mv "$tmp_enc" "$ENC"
shred -u "$tmp_check" 2>/dev/null || rm -f "$tmp_check"
info "✓ 已寫入 $ENC"

if [ "$ADD" = "1" ]; then
cat <<EOF

  $KEY 已加入加密檔（三台會拿到同一個值）。剩下四步：

  1. **確認鍵名宣告也同步了**（漏這步的症狀是 --check 報「該機缺鍵」）：
       settings/env/secrets.common.env.example  加一行 $KEY=
       scripts/env-sync.sh 的 SHARED_SECRETS     加 $KEY
       tests/test_env_sync.py 的 SHARED_SECRETS  加 $KEY
     （前兩份與第三份必須一致，測試會鎖；只改一份就會漂移）

  2. 本機 pull（自己也要拿值，本機 .env 可能有舊值／空值）：
       bash scripts/env-sync.sh pull

  3. 其他兩台 pull ＋ 重建容器（憑證是啟動參數，不重啟不生效）：
       git pull --ff-only && bash scripts/env-sync.sh pull && docker compose up -d

  4. 三台驗證一致（不印值）：
       bash scripts/env-sync.sh --fingerprints
     三台的 $KEY 指紋必須相同。不同 = 有人沒 pull。

  提醒：這支沒有 commit，也沒有 push。檢視過 diff 再自己提交。
EOF
  exit 0
fi

cat <<EOF

  換好了，但**還沒生效**。剩下三步：

  1. 本機（以及其他兩台）pull：
       git pull --ff-only && bash scripts/env-sync.sh pull
  2. 重建容器 —— 憑證是啟動參數，不重啟不生效：
       docker compose up -d
  3. 驗證三台一致（不印值）：
       bash scripts/env-sync.sh --fingerprints
     三台的 $KEY 必須指紋相同。不同 = 有人沒 pull。

  提醒：這支沒有 commit，也沒有 push。檢視過 diff 再自己提交。
EOF
