#!/usr/bin/env bash
# host-check.sh — mbp／msi 唯讀診斷：判決路徑為何不通
#
# ## 設計約束（constitution XII / R3、R7）
#
# 1. **唯讀**：不建立、不刪除、不修改任何檔案；不 push；不動 .env 憑證值。
# 2. **不印憑證值**：所有金鑰／密碼一律只報「有／無」與長度，永不印內容。
# 3. **不猜**：埠號、路徑、請求格式全部先 grep 原始碼確認（見下方 PREFLIGHT），
#    再組指令。這個腳本裡的每個常數都有 file:line 依據。
#
# ## 為什麼需要它（背景）
#
# /judgments/query 在 x570 實測回 `retrieval_unavailable`（qdrant `judgements`
# collection 不存在）。但在 mbp／msi 上，**症狀可能完全不同** —— 那兩台的
# `laws_flat.jsonl` 與 `judgements` collection 是否存在、api 埠號是否一致，
# 從 x570 無法得知（`backends` 表只有 x570 一筆心跳）。
# 本腳本收集那兩台的事實，不做任何修���。
#
# ## 用法（在 mbp 與 msi 上各跑一次）
#
#     bash scripts/host-check.sh            # 或
#     bash verify/host-check.sh
#
# 輸出直接整份貼回來即可。exit 0 = 診斷完成（不代表健康）。
#
set -uo pipefail   # 刻意不 set -e：診斷腳本要「跑完所有項」，不是第一個錯就停

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" 2>/dev/null || { echo "✗ 無法進入 repo 目錄：$ROOT"; exit 2; }

# ── PREFLIGHT：所有常數的出處 ─────────────────────────────────────────────
# 這些是寫死在腳本裡的唯一幾個「猜不起來」的值，逐個附原始碼依據：
#
#   API 主機埠          compose.yaml:82  ports: ["127.0.0.1:${API_PORT:-920}:920"]
#                       容器內埠固定 920（compose.yaml:76 註明與 Dockerfile 一致）
#   /judgments/query    backend/app/main.py:259
#   請求欄位            backend/app/main.py:253-256
#                       question:str="" / recall:int=50 / top_k:int=5
#   LAWS_FLAT           backend/app/b1_serve.py:42-47
#                       容器內 /app/data/laws/laws_flat.jsonl
#                       否則 <repo>/data/laws/laws_flat.jsonl
#   judgements collection  backend/app/retrieve.py:162  JUDGMENTS_COLLECTION
#   Qdrant 主機埠       由容器網路名 qdrant:6333（backend 程式用 http://qdrant:6333）

API_PORT="$(sed -n 's/^API_PORT=//p' .env 2>/dev/null | tail -1 | tr -d ' \r')"
API_PORT="${API_PORT:-920}"          # 與 compose.yaml:82 的預設一致
API="http://127.0.0.1:${API_PORT}"
QPORT="6333"

line() { printf '%s\n' "------------------------------------------------------------"; }
hdr()  { echo; line; echo "§ $1"; line; }
ok()   { printf '  ✓ %s\n' "$1"; }
bad()  { printf '  ✗ %s\n' "$1"; }
warn() { printf '  ⚠ %s\n' "$1"; }

echo "=============================================================================="
echo "host-check.sh — 唯讀診斷（$(date '+%Y-%m-%d %H:%M:%S %z')）"
echo "host_id=${HOST_ID:-未設}   repo=$ROOT"
echo "本腳本不修改任何檔案、不 push、不印任何憑證值。"
echo "=============================================================================="

# ── 1. 主機身分 ──────────────────────────────────────────────────────────
hdr "1. 主機身分與 repo"
echo "  HOST_ID            : ${HOST_ID:-（未設，compose 需要它；空值會讓 ports 綁錯）}"
echo "  hostname           : $(hostname 2>/dev/null)"
echo "  git HEAD           : $(git rev-parse --short HEAD 2>/dev/null || echo '（非 git repo）')"
echo "  git branch         : $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '—')"
echo "  落後 origin/main   : $(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?') 個 commit"

# ── 2. 容器狀態 ──────────────────────────────────────────────────────────
hdr "2. 容器狀態（qdrant / postgres / api）"
if command -v docker >/dev/null 2>&1; then
  docker compose ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}\t{{.Ports}}' 2>&1 \
    || warn "docker compose ps 失敗（可能沒在 repo 根目錄執行）"
  echo
  echo "  ⚠ qdrant 的主機埠若綁 100.x.x.x（非 127.0.0.1），則"
  echo "    curl http://127.0.0.1:6333/collections 會連不上 —— "
  echo "    那不是服務沒跑，是綁定位置不同（x570 就是這樣）。"
else
  bad "沒有 docker 指令"
fi

# ── 3. laws_flat.jsonl：host 與容器兩邊都要看 ───────────────────────────
hdr "3. laws_flat.jsonl（LAWS_FLAT，b1_serve.py:42）"
echo "  【主機端】repo/data/laws/laws_flat.jsonl"
if [ -f data/laws/laws_flat.jsonl ]; then
  # ⚠ 不用 ${sz:,} —— zsh 的 `:,` 是全域限定符，不是千分位；會報
  #   "arithmetic syntax error: operand expected"。這是實跑才抓到的。
  sz=$(wc -c < data/laws/laws_flat.jsonl 2>/dev/null | tr -d ' ')
  ok "存在（${sz} bytes）"
  echo "       最後修改：$(date -r data/laws/laws_flat.jsonl '+%Y-%m-%d %H:%M' 2>/dev/null || echo '—')"
  echo "       行數    ：$(wc -l < data/laws/laws_flat.jsonl 2>/dev/null | tr -d ' ')"
else
  bad "不存在 ← 這就是「fresh clone 空 mount」的實況"
  echo "       容器端會讀不到 → StatuteCorpus.from_jsonl() 拋 FileNotFoundError"
  echo "       → b1_serve.serve_question() 在 :436 就炸，走不到 :443 檢索"
fi

echo
echo "  【容器端】/app/data/laws/laws_flat.jsonl"
if command -v docker >/dev/null 2>&1; then
  docker compose exec -T api sh -c 'ls -la /app/data/laws/laws_flat.jsonl 2>&1 | head -1' 2>&1 \
    || warn "無法 exec 進 api 容器"
  echo
  echo "  【程式端實際解析到的路徑】"
  # ⚠ 不可 import app.b1_serve —— 它會連帶 import gateway → 需要 httpx。
  #   容器 image 是 `uv sync --frozen --no-dev --no-install-project`（Dockerfile:4），
  #   httpx 應該有，但實測 ModuleNotFoundError。推測是 module 解析路徑問題
  #   （import 單檔而非 package 會走不同的依賴解析），**未確認**。
  #   這裡改用「只讀 LAWS_FLAT 的定義式」—— 直接看 /app/app/b1_serve.py:42-47
  #   那段三元運算，並實測兩個候選路徑誰存在。這與程式邏輯等價且不需 import。
  docker compose exec -T api sh -c '
    if [ -d /app/app ]; then
      echo "  程式走容器分支（/app/app 存在）→ /app/data/laws/laws_flat.jsonl"
    else
      echo "  程式走 host 分支（/app/app 不存在）→ <repo>/data/laws/laws_flat.jsonl"
    fi
    if [ -f /app/data/laws/laws_flat.jsonl ]; then
      echo "  /app/data/laws/laws_flat.jsonl : 存在"
    else
      echo "  /app/data/laws/laws_flat.jsonl : 不存在 ← 程式會 FileNotFoundError"
    fi' 2>&1 || warn "無法 exec 進 api 容器"
fi

# ── 4. qdrant collections（判定 judgements 在不在）──────────────────────
hdr "4. qdrant collections（retrieve.py:162 查的是 'judgements'）"
KEY=""
for f in .env; do
  [ -f "$f" ] && KEY="$(sed -n 's/^QDRANT_API_KEY=//p' "$f" 2>/dev/null | tail -1 | tr -d ' \r')"
done
echo "  【從主機打發布埠】"
echo "  ⚠ 容器 image 內沒有 curl（實測 sh: curl: not found），故從主機打。"
echo "    程式實際連的是容器網域 qdrant:6333（compose 內部 DNS），與主機看到的發布埠同一服務。"
echo
for cand in "http://127.0.0.1:${QPORT}" "http://$(sed -n 's/^TS_IP=//p' .env 2>/dev/null | tail -1 | tr -d ' \r' | sed 's/^$/127.0.0.1/'):${QPORT}"; do
  printf '    試 %-34s ' "$cand"
  if [ -n "$KEY" ]; then
    r=$(curl -sf -m 5 -H "api-key: $KEY" "$cand/collections" 2>/dev/null)
  else
    r=$(curl -sf -m 5 "$cand/collections" 2>/dev/null)
  fi
  if [ -n "$r" ]; then
    echo "OK"
    echo "$r" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print("      （非 JSON 回應）"); raise SystemExit(0)
names = [c["name"] for c in d.get("result", {}).get("collections", [])]
print(f"      collections: {names}")
if "judgements" not in names:
    print("      ✗ 缺 judgements → /judgments/query 必然 retrieval_unavailable")
else:
    print("      ✓ judgements 存在")
if "laws" not in names:
    print("      ⚠ 缺 laws → /query 也會受影響")
' 2>/dev/null || echo "$r" | head -c 300 | sed 's/^/      /'
  else
    echo "失敗（看第 2 段 PORTS 的實際綁定位址）"
  fi
done
echo
echo "  判讀：清單裡應同時有 laws 與 judgements。缺 judgements → 判決路徑必然不通。"
echo
echo "  【若主機兩處都打不開，補打這個】"
echo "    直接問容器（用 python 而非 curl，image 沒有 curl）："
echo "    docker compose exec -T api python -c \"import urllib.request,os;"
echo "      k=os.environ.get('QDRANT_API_KEY','');"
echo "      r=urllib.request.Request('http://qdrant:6333/collections');"
echo "      r.add_header('api-key',k);"
echo "      print(urllib.request.urlopen(r,timeout=10).read().decode())\""

# ── 5. seed 目錄（判決原文來源）─────────────────────────────────────────
hdr "5. data/judgements/seed/（judgement_store.jfull_map() 的來源）"
if [ -d data/judgements/seed ]; then
  n=$(find data/judgements/seed -name '*.json' -type f 2>/dev/null | wc -l | tr -d ' ')
  echo "  seed 檔案數：$n"
  find data/judgements/seed -name '*.json' -type f -exec ls -la {} \; 2>/dev/null | awk '{print "    "$5" bytes  "$NF}'
  [ "$n" -eq 0 ] && warn "seed 空 → jfull_map() 回 {} → 即使檢索到也無原文可引"
else
  bad "data/judgements/seed/ 不存在 → jfull_map() 回 {}（judgement_store.py:60-62 fail closed）"
fi

# ── 6. 實際呼叫兩個端點（唯讀）──────────────────────────────────────────
hdr "6. 端點實測"
echo "  【A】/judgments/query （main.py:259，欄位 question/recall/top_k）"
t0=$(date +%s.%N)
resp=$(curl -s -m 120 -X POST "${API}/judgments/query" \
  -H 'content-type: application/json' \
  -d '{"question":"契約解除後雙方有何回復原狀義務？","recall":50,"top_k":5}' 2>&1)
t1=$(date +%s.%N)
echo "  牆鐘：$(echo "$t1 - $t0" | bc 2>/dev/null || echo '?') s"
echo "$resp" | head -c 600 | sed 's/^/  /'
echo
echo "$resp" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f"  （回應非 JSON：{e}）"); raise SystemExit(0)
st = d.get("status")
ab = d.get("abstention") or {}
print(f"  status   : {st}")
if ab:
    print(f"  reason   : {ab.get('reason')}")
    print(f"  detail   : {ab.get('detail')}")
    print(f"  error    : {ab.get('error')}")
print(f"  evidence : {len(d.get('evidence') or [])} 筆")
' 2>/dev/null
echo
echo "  reason 對照（b1_serve.py:48-56 ABSTAIN_REASONS）："
echo "    retrieval_unavailable ← 檢索層出錯（qdrant judgements 不存在／連不上）"
echo "    no_judgement_hits     ← 檢索成功但無命中"
echo "    no_quotable_evidence  ← 有命中但 seed 無原文"
echo "    no_statute_evidence   ← 有原文但引用的法查不到（laws_flat 問題）"

echo
echo "  【B】/query （main.py:202，欄位 question/recall/top_k/model）— 對照組"
t0=$(date +%s.%N)
resp2=$(curl -s -m 120 -X POST "${API}/query" \
  -H 'content-type: application/json' \
  -d '{"question":"中華民國刑法第184條規定什麼？","recall":50,"top_k":5}' 2>&1)
t1=$(date +%s.%N)
echo "  牆鐘：$(echo "$t1 - $t0" | bc 2>/dev/null || echo '?') s"
echo "$resp2" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f"  （回應非 JSON）"); raise SystemExit(0)
print(f"  ok       : {d.get(\"ok\")}")
print(f"  no_match : {d.get(\"no_match\")}")
print(f"  hits     : {len(d.get(\"hits\") or [])}")
print(f"  trace    : {str(d.get(\"trace\"))[:80]}")
src = d.get("src") or {}
print(f"  qdrant   : {(src.get(\"qdrant\") or {}).get(\"url\")}")
print(f"  llm      : {(src.get(\"llm\") or {}).get(\"model\")}")
' 2>/dev/null
echo
echo "  判讀：/query 正常但 /judgments/query 不通 → 問題在判決側（judgements collection"
echo "        或 seed），不在法規側（laws_flat.jsonl 或 laws collection）。"

# ── 7. 記憶體（corpus 每次請求重載）─────────────────────────────────────
hdr "7. api 容器 RSS（StatuteCorpus 每次請求重載，b1_serve.py:436）"
if command -v docker >/dev/null 2>&1; then
  echo "  判讀基準：b1_serve.py:436 每次請求都 from_jsonl() 重讀整份 laws_flat.jsonl，"
  echo "            無快取。所以連續打 /judgments/query 應該看到 RSS 逐步上升。"
  echo
  echo "  打之前："
  docker stats --no-stream --format '    {{.Name}}  {{.MemUsage}}' 2>&1 | grep api || true
  echo "  連打 3 次 /judgments/query…"
  for i in 1 2 3; do
    curl -s -m 120 -o /dev/null -X POST "${API}/judgments/query" \
      -H 'content-type: application/json' \
      -d '{"question":"契約解除後雙方有何回復原狀義務？","recall":50,"top_k":5}' 2>&1
  done
  echo "  打之後："
  docker stats --no-stream --format '    {{.Name}}  {{.MemUsage}}' 2>&1 | grep api || true
  echo
  echo "  ⚠ 單次 --no-stream 的數值受取樣時機影響。要可靠數字請改用"
  echo "    docker stats --format '{{.MemUsage}}' <container> 連續觀察，"
  echo "    或在容器內 ps -o rss。"
fi

# ── 8. 環境變數存在性（只報有無，不印值）────────────────────────────────
hdr "8. 關鍵環境變數（只報有無與長度，永不印值）"
for kv in API_PORT QDRANT_API_KEY POSTGRES_PASSWORD POSTGRES_DSN HOST_ID OLLAMA LLM_MODEL; do
  v="$(sed -n "s/^${kv}=//p" .env 2>/dev/null | tail -1 | tr -d ' \r')"
  if [ -z "$v" ]; then
    printf '  %-20s : （未設）\n' "$kv"
  else
    printf '  %-20s : 有值，%d chars\n' "$kv" "${#v}"
  fi
done
echo
echo "  ⚠ POSTGRES_DSN 與 POSTGRES_PASSWORD 是不同東西（ingest.md:25-26）。"
echo "    DSN 的密碼不是本機 pg 容器的密碼。"

echo
echo "=============================================================================="
echo "診斷結束。以上全部為唯讀操作。"
echo "請把這份輸出整份貼回來 —— 不要自行修任何東西，等裁決。"
echo "=============================================================================="
exit 0