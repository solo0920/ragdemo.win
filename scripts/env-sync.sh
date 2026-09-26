#!/usr/bin/env bash
# env-sync — 三機共用憑證的分發與合併（sops + age）。
#
# 解決的問題：QDRANT_API_KEY / POSTGRES_PASSWORD 等 9 把憑證必須三台一致，
# 過去靠人工複製，2026-09-26 已造成兩次不對稱 401／心跳失敗。
# 分層（看 settings/env/README.md）：
#   settings/env/common.env                  追蹤，明文，只放共用非敏感鍵
#   settings/env/hosts/<id>.env.example      追蹤，明文，per-machine 範本
#   settings/env/secrets.common.enc.env      追蹤，sops+age 加密，共用憑證真值
#   .env（repo 根）                          不追蹤，chmod 600，執行期唯一真相
#
# 安全規則（違反過的才寫下來）：
#   - 絕不在 stdout/stderr 印任何值；指紋只印 sha256 前 12 碼＋長度。
#   - 在 `bash -x`（xtrace）下直接拒絕執行 —— 2026-09-26 三次外洩之一就是 bash -x。
#   - 解密只落 mktemp 暫存檔，trap 保證 shred；絕不寫固定路徑的明文。
set -euo pipefail

# xtrace 下拒絕：$- 含 x 表示 set -x 生效中（bash -x 也一樣）。
case "$-" in
  *x*) echo "env-sync: refuse to run under xtrace (bash -x leaks secrets)" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 測試覆寫點（預設行為不變）：tests/test_env_sync.py 用 fixture 目錄隔離執行。
ENV_DIR="${ENV_SYNC_DIR:-$ROOT/settings/env}"
DOTENV="${ENV_SYNC_ENV:-$ROOT/.env}"

# 必須三台一致的憑證（與 secrets.common.env.example 同步；改了一處要改另一處，
# tests/test_env_sync.py 會鎖）。per-machine 鍵永遠不在此列。
SHARED_SECRETS="QDRANT_API_KEY QDRANT_PEER_API_KEY POSTGRES_PASSWORD ADMIN_TOKEN CF_AIG_TOKEN HF_TOKEN NVIDIA_API_KEY TYPESAFE_API_KEY ZEN_API_KEY"
# 共用非敏感鍵（與 common.env 同步；空值不合併，只補「本機沒寫」的鍵）。
SHARED_CONFIG="COLLECTION EMBED_MODEL RERANK_MODEL JEV_BANK_MIN JEV_VERIFY_MIN OPENROUTER_GATEWAY_URL ZEN_BASE_URL"
MANAGED_MARK="# --- managed by env-sync.sh (shared layers; do not edit below) ---"

need() { command -v "$1" >/dev/null 2>&1 || { echo "env-sync: missing tool: $1" >&2; exit 1; }; }

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//'
  echo "usage: $(basename "$0") [pull|--check|--fingerprints|--init-secrets [--force]|--host ID|--help]"
}

# 以指紋比對，不印值：輸出「鍵名 長度 sha12」。值缺失顯示缺失，不報錯。
fingerprints() {
  local f="${1:-$DOTENV}"
  [ -f "$f" ] || { echo "env-sync: no such file: $f" >&2; exit 1; }
  python3 - "$f" <<'PY'
import re, sys, hashlib
vals = {}
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
    if m:
        vals[m.group(1)] = m.group(2)
for k in ("QDRANT_API_KEY QDRANT_PEER_API_KEY POSTGRES_PASSWORD ADMIN_TOKEN "
          "CF_AIG_TOKEN HF_TOKEN NVIDIA_API_KEY TYPESAFE_API_KEY ZEN_API_KEY").split():
    v = vals.get(k)
    if v is None:
        print(f"{k:<20} MISSING")
    elif v == "":
        print(f"{k:<20} EMPTY")
    else:
        print(f"{k:<20} len={len(v):<4} sha12={hashlib.sha256(v.encode()).hexdigest()[:12]}")
PY
}

# 行保留合併：layer 檔的非空值覆蓋 .env 同名鍵；缺的鍵附加到 MANAGED_MARK 下；
# .env 獨有的鍵（含 per-machine 與註解）原樣保留。絕不印值。
merge_layer() {
  python3 - "$DOTENV" "$1" "$MANAGED_MARK" <<'PY'
import re, sys
env_path, layer_path, mark = sys.argv[1], sys.argv[2], sys.argv[3]
layer = {}
for line in open(layer_path, encoding="utf-8").read().splitlines():
    m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
    # 空值不合併：範本檔的值是空的，合進去等於清空本機真值。
    if m and m.group(2) != "":
        layer[m.group(1)] = m.group(2)
if not layer:
    sys.exit(0)
lines = []
if __import__("os").path.exists(env_path):
    lines = open(env_path, encoding="utf-8").read().splitlines()
have = set()
out = []
for line in lines:
    m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
    if m and m.group(1) in layer:
        out.append(f"{m.group(1)}={layer[m.group(1)]}")
        have.add(m.group(1))
    else:
        out.append(line)
missing = [k for k in layer if k not in have]
if missing:
    if mark not in out:
        out.append(mark)
    for k in missing:
        out.append(f"{k}={layer[k]}")
open(env_path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
}

cmd_pull() {
  need sops
  local enc="$ENV_DIR/secrets.common.enc.env"
  [ -f "$enc" ] || { echo "env-sync: $enc not found; nothing to pull yet" >&2; exit 1; }
  local tmp
  tmp="$(mktemp "$ENV_DIR/.decrypted.XXXXXX")"
  # trap 字串用雙引號立即展開：tmp 是函式 local，EXIT trap 在函式返回後才跑，
  # 到時 local 已出作用域，set -u 下會報 unbound（2026-09-27 實測）。
  # shred 不在 macOS，需 rm 回退。
  trap "shred -u \"$tmp\" 2>/dev/null || rm -f \"$tmp\"" EXIT
  SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}" \
    sops --decrypt --output "$tmp" "$enc"
  chmod 600 "$tmp"
  merge_layer "$tmp"
  merge_layer "$ENV_DIR/common.env"
  chmod 600 "$DOTENV"
  trap - EXIT
  shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  echo "env-sync: pulled shared layers into .env (per-machine keys untouched)"
}

cmd_check() {
  # 不需 sops：只核對鍵名覆蓋率與版控衛生。值一律不讀。
  local fail=0
  [ -f "$DOTENV" ] || { echo "env-sync --check: MISSING .env" >&2; exit 1; }
  python3 - "$DOTENV" "$ENV_DIR/secrets.common.env.example" "$ENV_DIR/common.env" <<'PY'
import re, sys
def keys(p):
    out = set()
    for line in open(p, encoding="utf-8").read().splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
        if m:
            out.add(m.group(1))
    return out
env, sec, com = (keys(a) for a in sys.argv[1:4])
missing = sorted((sec | com) - env)
if missing:
    print("env-sync --check: .env 缺少鍵: " + " ".join(missing))
    sys.exit(1)
print(f"env-sync --check: key coverage ok ({len(env)} keys in .env)")
PY
  fail=$?
  # 以下兩項只在真實 repo 執行（fixture 目錄不在 git 裡，無意義則跳過）。
  if [ "$ENV_DIR" = "$ROOT/settings/env" ]; then
    # hosts/ 下的真值檔不得進版控（只許 .example）。
    if git -C "$ROOT" ls-files settings/env/hosts/ | grep -v '\.example$' | grep -q .; then
      echo "env-sync --check: tracked non-example file under settings/env/hosts/" >&2
      fail=1
    fi
    # 明文暫存檔不得殘留。
    if ls "$ENV_DIR"/.decrypted.* 2>/dev/null | grep -q .; then
      echo "env-sync --check: leftover decrypted tmp in settings/env/" >&2
      fail=1
    fi
  fi
  return $fail
}

cmd_init_secrets() {
  # 從本機 .env 抽出 SHARED_SECRETS 的真值，加密成 secrets.common.enc.env。
  # 只在「第一台」（MSI）跑一次；之後輪換直接 sops 解密改值。
  need sops
  local enc="$ENV_DIR/secrets.common.enc.env" force=0
  [ "${1:-}" = "--force" ] && force=1
  if [ -f "$enc" ] && [ "$force" -ne 1 ]; then
    echo "env-sync: $enc exists; refusing without --force" >&2; exit 1
  fi
  [ -f "$DOTENV" ] || { echo "env-sync: no .env to extract from" >&2; exit 1; }
  local tmp
  tmp="$(mktemp "$ENV_DIR/.init-secrets.XXXXXX")"
  trap "shred -u \"$tmp\" 2>/dev/null || rm -f \"$tmp\"" EXIT
  python3 - "$DOTENV" "$tmp" "$SHARED_SECRETS" <<'PY'
import re, sys
vals = {}
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
    if m:
        vals[m.group(1)] = m.group(2)
want = sys.argv[3].split()
# 缺鍵（整行不存在）是錯誤；空值放行 —— 空＝「未設定」
# （例：MSI 的 ZEN_API_KEY 目前是空的）。合併端本來就會跳過空值，
# 所以空值進加密檔不會清空別台的真值；只報鍵名，不印值。
missing = [k for k in want if k not in vals]
if missing:
    print("env-sync --init-secrets: .env 缺少鍵: " + " ".join(missing), file=sys.stderr)
    sys.exit(1)
empty = [k for k in want if vals[k] == ""]
with open(sys.argv[2], "w", encoding="utf-8") as f:
    for k in want:
        f.write(f"{k}={vals[k]}\n")
if empty:
    print("env-sync --init-secrets: 以下鍵為空（未設定，仍會寫入以保 schema 齊全）: "
          + " ".join(empty))
print(f"env-sync --init-secrets: extracted {len(want)} keys")
PY
  # --filename-override：sops 按「輸入路徑」配 .sops.yaml 的 creation_rules，
  # tmp 路徑配不上任何規則會報 no matching creation rules（2026-09-27 實測），
  # 故宣告這次加密的邏輯檔名。必須是 repo 根相對路徑且與 .sops.yaml 的
  # path_regex 一致，否則換了檔名規則就靜默失效。
  (cd "$ROOT" && sops --encrypt \
    --filename-override "settings/env/secrets.common.enc.env" \
    --output "$enc" "$tmp")
  trap - EXIT
  shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  echo "env-sync: wrote $enc (track it with git)"
}

case "${1:-pull}" in
  pull) cmd_pull ;;
  --check) cmd_check ;;
  --fingerprints) fingerprints "${2:-$DOTENV}" ;;
  --init-secrets) cmd_init_secrets "${2:-}" ;;
  --host) echo "env-sync: fresh-machine scaffold not implemented in this phase; see settings/env/README.md" >&2; exit 2 ;;
  -h|--help|help) usage ;;
  *) echo "env-sync: unknown arg: $1" >&2; usage >&2; exit 2 ;;
esac
