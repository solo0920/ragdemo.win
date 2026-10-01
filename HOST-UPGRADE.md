# 三機升級 runbook — **腳本管不到的 per-host 手工步驟**

> **2026-09-27 瘦身**：本檔原本 351 行、含 73 行「本節作廢不要照做」與整段被
> `scripts/host-sync.sh` 取代的 pull/重建步驟，都已刪除（351 → 244 行）。
> 現在這份是**「剩下來的、只能手工做的」**。已刪部分的結論全部存活在
> `ARCHITECTURE.md`〈密鑰管理〉、§2 指向的 `host-sync.sh`、`settings/env/README.md`。

## 先跑腳本，再回來看這裡

```bash
bash scripts/host-sync.sh        # 升級＋重建＋驗收（取代原本的 pull/重建/驗收三節）
bash scripts/host-doctor.sh      # 診斷（取代原本的驗收節）
```

## ⭐ §0 git hooks（**新機／重灌必做，最容易漏**）

```bash
git config core.hooksPath .githooks
```

**為什麼列在最前面**：這是全份 runbook 裡唯一「漏了不會立刻發現」的一項。
`core.hooksPath` 是 **per-clone 的 git 設定**，不在版控裡、也不是環境變數 ——
所以 `host-sync.sh` 管不到（它只碰檔案與容器），新機 clone 完預設就是**沒設**。

沒設的後果不是報錯，是**檢查靜默消失**：commit message 少了 `msi:` 前綴也過、
`LAN_IP=` 違規也過、pytest 掛掉也照 push。症狀要等到「某台機器 push 出違規
commit、或壞掉的 commit 上線」才會浮現，而那時已經難回溯是誰按的。

驗證有沒有生效（會印 pre-push 的三行檢查結果）：

```bash
git push --dry-run origin main   # 應看到「✓ pytest N passed」
```

`git config --get core.hooksPath` 沒有輸出 = 沒設。

（2026-09-29 msi 重灌後實測：這個值不見了，連續 11 個 commit 是在沒有任何
pre-push 檢查的情況下產生的。內容碰巧都安全 —— 因為每次都另外手動跑了 pytest ——
但機制本身是失效的。`ARCHITECTURE.md`〈提交準則〉有寫這條，顯然不夠顯眼。）

## 剩下的手工步驟只有三類

都是排程或「需要人決定」的事，寫成腳本反而更難讀：

- **§1 身份環境變數** —— 新機/重灌必做（`HOST_NAME` / `HOST_MACHINE_ID`）
- **§3–§5 排程與角色** —— crontab / launchd 設定、x570 的每日 ingest
- **§7 已知非問題** —— 別誤判成故障的現象

**本檔對三台都適用**（2026-09-27 修正）：原文寫「MSI 已全部完成」是當時的狀況，
不是永久屬性。msi 重灌後，§1 與 §3.1 對它又重新變成待辦。

## 重灌後 30 秒自我檢查

```bash
git config --get core.hooksPath || echo "✗ 缺 §0 的 hooksPath"
command -v docker >/dev/null && id -nG | grep -qx docker || echo "✗ 不在 docker 群組"
systemctl is-active docker cloudflared 2>/dev/null
curl -sf localhost:8000/health >/dev/null && echo "✓ api 在線" || echo "✗ api 沒起來"
```

四行涵蓋 2026-09-29 msi 重灌後**實際踩到的全部問題**（hooksPath 遺失、
docker 群組沒加、cloudflared 沒裝、api 沒跑）。

---

## 0. .env 只有一個（2026-09-26 收斂，已完成）

**只維護 repo 根目錄的 `.env`**。`backend/.env` 已於 2026-09-26 刪除，程式與腳本
一律讀根目錄那份 —— 25 個變數兩份副本的時代，`QDRANT_API_KEY` 曾在兩者間漂移，
造成「本機 qdrant 200、遠端 401」的非對稱故障。實際刪除時就發現 `POSTGRES_PASSWORD`
兩份不同（指紋 `e328bd31728a` vs `55cebf3c8276`）—— **副本漂移會靜默發生**。
新機直接 `cp .env.example .env`，不存在遷移問題。

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

## 2. ~~pull 並重建~~ → 已由 `scripts/host-sync.sh` 取代

原本是手動四步（`git pull` → `docker compose config -q` → `up -d --build` → `curl /health`）。
2026-09-27 起這條路徑是一個指令：

```bash
bash scripts/host-sync.sh              # 升級到目前分支
bash scripts/host-sync.sh --dry-run    # 只看會做什麼，不寫檔不動容器
```

它比手動版多做的事：工作樹不乾淨就**拒絕**（不覆蓋未提交改動）、xtrace 下拒絕執行、
驗收失敗**不回滾**已完成的步驟、exit code 契約區分「驗收未通過(5)」與「驗收被略過(6)」。
細節見 `scripts/DESIGN.md`〈host-sync.sh〉。

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

### 3.2 `QDRANT_PEER_API_KEY` —— **不要手動設，`env-sync.sh pull` 會給你**

它是 6 把共用憑證之一，值在 `settings/env/secrets.common.enc.env`（sops+age 加密），
`env-sync.sh pull` 會解密合併進 `.env`。手動抄反而會被 `pull` 覆蓋。
`pull` 完確認（不印值）：

```bash
scripts/env-sync.sh --fingerprints | grep QDRANT_PEER_API_KEY   # 與另外兩台的 sha12 應一致
```

**容易搞反的一件事**（這裡也曾是 73 行的「不要照做」，2026-09-27 瘦身時
只留下這張表，完整查證見 `ARCHITECTURE.md`〈密鑰管理〉）：

| 鍵 | 給誰用 | 三台要一致嗎 |
|---|---|---|
| `QDRANT_API_KEY` | **自己**那台的 qdrant 容器 ＋ 自己那台的 api | **不要**（per-host 機密，各機不同沒問題） |
| `QDRANT_PEER_API_KEY` | 連**別台**（x570）的 qdrant 做快照同步 | **要**（走加密檔自動分發） |

所以「某台的 `QDRANT_API_KEY` 和別台不一樣」不是故障；只有 `QDRANT_PEER_API_KEY`
不一致才是。

---

## 4. mbp 專屬：launchd 要加 law-update worker

新的 `scripts/law-update-worker.sh` 負責執行前端「更新」按鈕排入的請求
（容器跑不了 ingest 管線，所以實際動作在 host 端）。MSI 用 crontab 每分鐘：

```
* * * * * /home/solo/projects/ragdemo/scripts/law-update-worker.sh >/dev/null 2>&1
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

**日常驗收用 `scripts/host-doctor.sh`**（7 段：git／工具／容器／`env-sync --check`／
憑證指紋／registry 心跳／法規版本，且永不印值）。部署後的當次驗收由
`scripts/host-sync.sh` 第 6 步自動做（`/health` 的 `host_id` 必須等於 `.env` 的
`HOST_ID`，加上 `/status?probe=0` 的 `law_version`）。

⚠️ 呼叫別台的 `/status` **必須帶 `?probe=0`**，否則 A→B→C→A 互相探測，
請求數指數成長（2026-09-26 實作時踩過）。

腳本**沒有**覆蓋、仍需人工確認的兩項：

```bash
# 1) qdrant 點數（腳本只查容器在不在，不查資料量）
curl -s http://${TS_IP}:6333/collections/laws -H "api-key: $QDRANT_API_KEY" \
  | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print(r["status"], r["points_count"])'
# 2) 更新機制的 auth（不帶 token 應 401）
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

### 三台的 pytest 環境不一致（2026-09-30 實測）

`.venv/bin/python -m pytest -q` 在三台上的 skip 數不同，**不是測試壞掉，是環境長得不一樣**。
全 repo 測試總數一致（374），差別全在誰能跑：

| | msi | x570 | 原因 |
|---|---|---|---|
| 全部 | 367 passed / 7 skipped | 344 passed / **30 skipped** | — |
| 前端 26 個 | ✅ 跑 | ❌ **skip** | **x570 沒裝 node**（`test_frontend_hosts` 10 + `test_frontend_probe` 5 + `test_frontend_cf_access` 11）。它們用 node 真的跑一次 `+server.ts` 的函式。 |
| qdrant 認證 3 個 | ✅ 跑 | ❌ **skip** | `test_sync_snapshot_auth.py:103` 硬寫 `curl 127.0.0.1:6333/healthz`，而 **x570 的 `TS_IP` 有值** → qdrant 綁在 `100.119.83.111`、不在迴圈。⚠️ 也就是**唯一真正把 qdrant 暴露到 tailnet 的那台，正好是唯一不再測它的那台**。 |
| WSL 專用路徑 1 個 | ✅ 跑 | ❌ skip | `test_detect_host_endpoint.py` 的函式內判斷「本機不是 WSL」。x570 是原生 Linux，本來就該 skip。 |

**要看某台能驗證什麼，不要看 skip 數，要看缺的是哪一類。** CI（Ubuntu runner、node
已裝、TS_IP 空）是唯一三類都跑得到的地方 —— 所以 CI 全綠不等於「三台都能驗證」。

### 各機的 venv 是手動長出來的，不一定等於 `pyproject.toml`

2026-09-30 在 x570 實測到兩件事：

1. **缺 `pytest-asyncio`** → 所有 `@pytest.mark.asyncio` 的測試** failed（不是
   skipped）**。用 `-p no:asyncio` 模擬確認過：`test_host_probe.py` 7 個全紅。
   修法是 `uv sync --dev`（`pyproject.toml` 的 dev group 早就宣告了它）。
2. **plugin 集不合約**：x570 有 `typeguard`、msi 有 `anyio`，**兩邊都有不在
   `pyproject.toml` 裡的 plugin**。那是手動 `pip install` 的痕跡。

`uv sync --dev` 會把 venv 對齊到宣告的依賴。新機／重灌後**先跑它**，再跑 pytest。

### x570 跑的是 Python 3.14，而 pin 是 3.12

`.python-version` 與 CI 都是 3.12；msi 是 3.12.14；**只有 x570 是 3.14.4**。
`requires-python = ">=3.12"` 不禁止 3.14，所以不是違規 —— 但等於 x570 上驗證的
是一個**沒有任何 CI 覆蓋的 Python 版本**。

**刻意不追。** 3.14 目前跑得動（2026-09-30：x570 上 374 個測試全過或按環境
skip，0 failed），收緊 pin 只會製造摩擦。記在這裡是為了讓下次寫到只在某個
Python 版本成立的語法時，知道**有兩台測得到、一台測不到**。

---

## 8. 已修的 bug —— 這些是**陷阱**，別改回去

> 原本這節是「順手修掉的 bug」changelog。2026-09-27 瘦身時改成陷阱清單：
> 修掉的事實本身不值得記錄（git 歷史有），但**描述現行行為**的那兩條值得記，
> 否則下次有人會「順手整理」回去。

1. **條號精準比對不可重新巢狀進 `if HAS_SPARSE:`**。它原本被那樣包住，而
   `laws` collection 沒有 sparse vectors，於是整段是死碼 → 「民法第184條」
   只能靠 dense 0.55 < 門檻 0.58 → `no_match`。已移到兩條召回路徑之外。
2. **`_detect_law()` 的法名長度門檻是 `>=2`，不可收回 `>=3`**。「民法」是全
   corpus 唯一的 2 字法名，`>=3` 會讓它永遠認不出。
3. **`SHELL_PATH`／`REPO_PATH` 之類排程用變數不要刪**（§4）。它們是
   launchd/crontab 唯一能把 repo 路徑帶進去的地方。
