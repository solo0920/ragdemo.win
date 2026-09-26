# 三機升級runbook（2026-09-26 容器化後的 per-host 待辦）

本檔是**給 x570 與 mbp 看的行動清單**：pull 之後照著做即可。
MSI 已全部完成，本檔對 MSI 只作為「為什麼要這樣做」的說明。

改動集中在三處：身份識別改走環境變數、法規版本欄位＋更新按鈕、快照同步的
兩個修正。全部可在 `docker compose up -d --build` 後生效。

---

## 0. 先做這件（兩台都要）：輪換 qdrant api key

> 2026-09-27 起有正規流程：`settings/env/README.md`（sops+age 加密分發，
> `env-sync.sh pull` 合併）。下面手動步驟仍有效，是加密檔還沒覆蓋到你之前
> 的 fallback；兩種方式不要混用同一把 key 的兩次輪換。

**為什麼要輪換**：2026-09-26 診斷時，這把 key 兩次被印進 agent 的對話紀錄
（`env | grep` 與 `bash -x` 各一次）。三台共用同一把，所以等同三台外洩。
目前流通中的值是**已外洩**的，請整組換掉。

在任一台產生新值（**不要貼到任何聊天工具**）：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

然後在**根目錄的 `.env`** 把 `QDRANT_API_KEY=` 換成新值，接著重建容器 ——
key 是啟動參數，不重啟不會生效：

```bash
cd ~/projects/ragdemo.win        # mbp 請用你的 checkout 路徑（見 §4）
set -a; . ./.env; set +a
docker compose up -d --force-recreate qdrant
docker compose up -d --force-recreate api
```

驗證（不印值）：

```bash
printf '%s' "$QDRANT_API_KEY" | sha256sum | cut -c1-12   # 三台應一致
curl -s -o /dev/null -w 'HTTP %{http_code}\n' \
  http://127.0.0.1:6333/collections/laws -H "api-key: $QDRANT_API_KEY"   # 應 200
```

> 若三台的 key 不一致（`QDRANT_PEER_API_KEY` 沒設時會退回自己的 key），
> 快照同步會出現「本機 200、遠端 401」。比較指紋（sha256 前 12 碼）即可定位，
> 不需要看到值。

---

## 0b. .env 只有一个（2026-09-26 收斂）

**只維護 repo 根目錄的 `.env`**，`backend/.env` 已於 2026-09-26 在 MSI 移除
（x570/mbp 若還有，可直接刪）。

原本根 `.env` 給 compose、`backend/.env` 給 host 腳本，25 個變數兩份副本 ——
`QDRANT_API_KEY` 曾在兩者間漂移，造成「本機 qdrant 200、遠端 401」的非對稱故障。
現在 `sync-snapshot.sh` 與 `law-update-worker.sh` 會**自己載入根 `.env`**，
crontab 也不必再寫 `set -a; . .../backend/.env; set +a;`。

遷移後請確認：

```bash
# 1) 刪除殘留的 backend/.env（腳本已改讀根 .env，刪了不影響運作）
ls -la backend/.env && rm backend/.env || echo "已無"

# 2) 稽核工具確認沒有幽靈變數與副本漂移
python3 scripts/env-audit.py
# 期望只剩 3 項「幽靈變數」（QDRANT_URLS / EMBED_MODEL / RERANK_MODEL），
# 那是刻意留著的說明性項目，原因會附在 .env.example 註解裡
```

⚠️ **刪除前務必先比對兩份是否一致**（值可能已經漂移）：

```bash
python3 -c "
import pathlib, re
def load(p):
    d = {}
    for line in pathlib.Path(p).read_text(encoding='utf-8').splitlines():
        m = re.match(r'^([A-Z_][A-Z_0-9]*)=(.*)$', line)
        if m: d[m.group(1)] = m.group(2)
    return d
root, back = load('.env'), load('backend/.env')
print('值不一致:', [k for k in set(root) & set(back) if root[k] != back[k]] or '無')
print('只在根:', sorted(set(root) - set(back)) or '無')
print('只在 backend:', sorted(set(back) - set(root)) or '無')
"
```

> 2026-09-26 MSI 實際刪除時就發現 `POSTGRES_PASSWORD` 兩份不同
> （指紋 `e328bd31728a` vs `55cebf3c8276`），必須先對齊才能刪。
> 這正是重複維護的後果 —— 副本漂移會靜默發生。

### 機台專屬變數的前綴

某一台獨有的變數加前綴，避免三台互相覆蓋：

```
msi_<變數名>=<值>      # 只有 MSI 用
mbp_<變數名>=<值>      # 只有 mbp 用
x570_<變數名>=<值>     # 只有 x570 用
```

例如只有 MSI 要放雲端金鑰：`msi_NVIDIA_API_KEY=...`。
**進版控前請跑 `scripts/env-audit.py --template`** 取得最新骨架，
它會列出每個變數的必填/選填、消費者與用途。

> ⚠️ `env-audit.py` 會讀 `.env` 的**變數名**但只印長度與是否存在，
> 不會印值 —— 可以安全貼回來。但不要用 `cat .env`、`env | grep`、
> 對含憑證的腳本跑 `bash -x`（2026-09-26 三次洩漏都是這樣）。

## 1. 補兩個身份環境變數（兩台都要）

`compose.yaml` 已移除 `/etc/hostname`、`/etc/machine-id` 的**單檔 bind mount**
（Docker Desktop 掛單檔會讓容器 init 直接 exit=127；原生 Linux/macOS 雖可用，
但改成環境變數後不再依賴 host 檔案佈局）。所以兩台必須補：

```bash
HN=$(cat /etc/hostname); MID=$(cat /etc/machine-id)
f=.env
grep -v '^HOST_NAME=' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
grep -v '^HOST_MACHINE_ID=' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
printf 'HOST_NAME=%s\nHOST_MACHINE_ID=%s\n' "$HN" "$MID" >> "$f"
chmod 600 "$f"            # 內含憑證，務必 600
```

沒補的話：`/health` 仍會回 ok，但 `machine_id` 會退化成 MAC 位址（弱識別），
並在 api log 留下明確警告 —— 不會靜默，但別忽略。

---

## 2. pull 並重建

```bash
git pull origin main
docker compose config -q          # 先確認 compose 檔可解析
docker compose up -d --build      # --build 只有改到 backend/app/*.py 才需要
curl -s localhost:8000/health     # 應回 host_id=<你的機器>
```

確認身份正確：

```bash
curl -s localhost:8000/health | python3 -m 'import json,sys; d=json.load(sys.stdin); print(d["host_id"], d["hostname"], len(d["machine_id"]))'
```

---

## 3. 排程要補的環境變數

### 3.1 `SRC_API_URL`（兩台的快照同步都要）

`sync-snapshot.sh` 會向**來源機的 `/status`** 取法規版本。但三台的 8000
**全綁 `127.0.0.1`、公網只經 cloudflared tunnel**，所以不能用 `IP:8000`
（實測連不上），必須顯式指定 tunnel 網址：

| 機器 | 值 |
|---|---|
| x570 | 自己的 `/status`，可設 `https://api-x570.ragdemo.win` |
| mbp | `https://api-x570.ragdemo.win` |
| msi（已完成） | `https://api-x570.ragdemo.win` |

**建議放進 `.env` 而非排程指令**（worker 與腳本都會 source 它，較不易漏）：

```bash
grep -q '^SRC_API_URL=' .env || echo 'SRC_API_URL=https://api-x570.ragdemo.win' >> .env
```

沒設的後果**不是錯誤** —— 腳本會記
`law version: 取不到（...）` 並正常跳過，法規版本欄位維持舊值。

### 3.2 `QDRANT_PEER_API_KEY`（選用，但建議）

`QDRANT_API_KEY` 現在有兩個用途（自己的 qdrant ＋ 寫入 x570 的 qdrant），
過去能運作只因三台共用同一把。已拆出獨立的 peer key：

```bash
# 值＝x570 新的 QDRANT_API_KEY（若三台共用同一把，就與本機相同）
echo 'QDRANT_PEER_API_KEY=<x570 的新 key>' >> .env
```

好處：以後任一台輪換自己的 key，都不會再連帶弄斷另外兩台的同步。
不設定則退回 `QDRANT_API_KEY`（單機設定仍可用）。

---

## 4. mbp 專屬：launchd 要加 law-update worker

新的 `scripts/law-update-worker.sh` 負責執行前端「更新」按鈕排入的請求
（容器跑不了 ingest 管線，所以實際動作在 host 端）。MSI 用 crontab 每分鐘：

```
* * * * * /home/solo/projects/ragdemo.win/scripts/law-update-worker.sh >/dev/null 2>&1
```

mbp 的 crontab 被 macOS TCC 擋，請用 launchd。複製既有的
`com.ragdemo.sync-snapshot` 結構（`StartInterval` 60 秒、`RunAtLoad`、
`StandardOutPath`／`StandardErrorPath` 指向 `data/.ops/worker.log`）：

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.ragdemo.law-update.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ragdemo.law-update</string>
  <key>ProgramArguments</key>
  <array><string>SHELL_PATH/scripts/law-update-worker.sh</string></array>
  <key>WorkingDirectory</key><string>REPO_PATH</string>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>REPO_PATH/data/.ops/worker.log</string>
  <key>StandardErrorPath</key><string>REPO_PATH/data/.ops/worker.log</string>
</dict>
</plist>
PLIST
# 把 SHELL_PATH / REPO_PATH 換成你的實際路徑
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ragdemo.law-update.plist
launchctl list | grep law-update        # 應出現 com.ragdemo.law-update
```

不註冊也能用（手動跑 `bash scripts/law-update-worker.sh`），只是按鈕會一直
顯示「已排入更新，等待主機端 worker 執行」。

> 腳本會用 `data/laws/.law_sync.json` 判斷角色：**有**＝來源機（跑完整
> ingest），**沒有**＝備援機（強制重抓 x570 快照）。兩台都該沒有該檔案。

---

## 5. x570 專屬：讓版本欄位有值

三台的「法規版本」欄位現在都顯示 `—`，因為**沒有任何一台跑過每日 ingest**
（`data/laws/.law_sync.json` 不存在）。x570 作為資料來源，需要手動跑一次：

```bash
cd ~/projects/ragdemo.win          # 你的 checkout 路徑
uv sync                            # 工具鏈在根 .venv（duckdb/asyncpg/pandas）
uv run ingest/laws/sync_daily.py   # 先不加 --apply：只檢查並回報，不動資料
uv run ingest/laws/sync_daily.py --apply
```

跑完應該會產生：

```
data/laws/.law_sync.json     ← 版本記錄（UpdateDate）
data/laws/ChLaw.json
data/laws/laws_flat.parquet
```

之後加排程就能每日自動更新：

```
30 2 * * * cd ~/projects/ragdemo.win && uv run ingest/laws/sync_daily.py --apply
```

**注意**：這會**整庫重建** `laws` collection。跑完請確認筆數，並讓 msi/mbp
的快照同步把新版帶下去（`sync-snapshot.sh` 會偵測點數變化）。

---

## 6. 三台都要驗收

```bash
# 1) 容器
docker compose ps                       # 三個都 Up
# 2) 健康
curl -s localhost:8000/health | python3 -m json.tool
# 3) 三機探測 + 法規版本（x570 pull 後才會有版本值）
curl -s localhost:8000/status | python3 -m 'import json,sys; d=json.load(sys.stdin); print(d["log"]); print(d["versions"])'
# 4) 資料完整
curl -s http://${TS_IP}:6333/collections/laws -H "api-key: $QDRANT_API_KEY" \
  | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print(r["status"], r["points_count"])'
# 5) 更新機制（不帶 token 應 401）
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/law-update
```

前端：登入後開「連線與來源」→ 應看到「主機／角色／IP／法規版本／狀態」五欄。
若本機版本落後，本機那一列的版本後方會出現**更新**按鈕（需 ADMIN_TOKEN，
與題庫頁同一組，存在 `localStorage`）。

---

## 7. 已知非問題（別誤判）

| 現象 | 原因 |
|---|---|
| api log 有 `laws_meta 未找到` | 只影響「法規題庫」輔助功能，不參與檢索。可忽略。 |
| 同步 log 有 `law version: 取不到` | `SRC_API_URL` 沒設或來源機還沒 pull。同步本身正常。 |
| 法規版本欄全是 `—` | 還沒有人跑過 ingest（見 §5）。 |
| `/health` 的 `ok:true` 但某台其實離線 | 已知監測缺口：`/health` 不檢查 registry。要看各機狀態請用 `/status` 的 `log`。 |
| `docker compose config` 會印出所有憑證 | 它會把 `.env` 的值展開。**不要把輸出貼到聊天工具。** |

---

## 8. 這次順手修掉的既有 bug（了解即可）

1. **api 在 WSL 上每次 Docker 重啟都死**：`/etc/hostname`、`/etc/machine-id`
   的單檔 bind mount 在 Docker Desktop 不可靠（exit=127）。改走 env（見 §1）。
2. **條號精準比對是死碼**：整段被巢狀在 `if HAS_SPARSE:` 內，而 `laws`
   collection 沒有 sparse vectors → 「民法第184條」只能靠 dense 0.55 < 門檻
   0.58 → `no_match`。已移到兩條召回路徑之外。
3. **2 字法名偵測不到**：`_detect_law()` 的 `len>=3` 門檻讓「民法」（全 corpus
   唯一 2 字法名）永遠認不出。放寬到 `>=2`。
4. **同步成功卻回報失敗**：`ver="$(curl … | python3 …)"` 在 `pipefail` 下，
   curl 連不上會讓賦值回傳非零，`set -e` 在判斷前就中止。已加 `|| true`。
