# RagDemo ROADMAP（接手畀 opencode，各主機共用）

> 這份文件是「三台主機的 opencode 能各自接手工作」的交接媒介：
> 對話記錄不會跨機器，但 repo 內的檔案會。每台新機器的 opencode
> 讀完本檔＋`ARCHITECTURE.md`＋`.env.example` 即可接手設定與開發。
>
> 接手原則：**每台機器在自己的 WSL/Linux 跑 opencode，ollama 視主機而定。**
>
> 接手 checklist（新機必做）：① clone 後 `git config core.hooksPath .githooks`
> （啟用 commit 前綴＋push smoke，見 ARCHITECTURE「Git hooks」）；② 依 §2.2/§0 寫
> 本機 `backend/.env`（只留 tailscale IP）；③ 跑 `bash scripts/sync-snapshot.sh` 建本機
> qdrant 備援；④ `/health`＋`/query` 驗證。

---

## 0. 三台主機總覽（2026-09-22 現況）

| | x570 .99（主力） | mbp .93.85（加速） | MSI .0.2（demo/接手） |
|---|---|---|---|
| 硬體 | X570 + 3950X + RTX 5070 Ti + 64G RAM | Mac M1 Max + 64G unified | RTX 5060 + 64G RAM |
| tailscale | 100.119.83.111 | 100.64.121.9 | 100.65.68.106 |
| LAN | 192.168.0.99 | 192.168.93.85 | 192.168.0.2 |
| ollama | v0.34.0（native） | v0.34.2（native, 0.0.0.0） | v0.34.2（native, 0.0.0.0） |
| api:8000/qdrant:6333/pg:5432 | ✅（docker compose） | api ✅（launchd）/qdrant ✅（本機備援）/pg ✗ | api ✅（WSL2）/qdrant ✅（本機備援）/pg ✗ |
| LLM 預設 | qwen3:14b | qwen3:14b | qwen3:8b |
| 模型 | bge-m3, bge-reranker, qwen3:14b, qwen3-coder | bge-m3, bge-reranker, qwen3:14b, qwen3-coder(+next) | bge-m3, bge-reranker, qwen3:8b, qwen2.5-coder:7b |

模型注意：**qwen3.5:4b 已棄用**（template `{{ .Prompt }}` 壞掉）＋ **qwen3:4b 已棄用**（
`think:false` 無效是已知 bug，見 ollama#12917：連「1+1=?」都思考 1000+ token 吃光輸出）。
MSI 2026-09-23 起改用 **qwen3:8b**（實測 `think:false` 正常、回應簡短）。
qwen3 家族請保留 `"think": false`（root level），否則思考 token 吃光輸出、回空字串。

### 0.1 x570 最新狀態（2026-09-22 x570 側 smoke）
- api/qdrant/pg 三容器 up（`docker compose ps`），`/health` host_id=x570（
  x570 機 `.env` 的 `HOST_ID` 已一併改為 x570），
  `hostname=solo-X570-I-AORUS-PRO-WIFI`、`machine_id=af8eaa67…`、`ips=[100.119.83.111, 192.168.0.99, 172.18.0.3]`
- `/hosts` 三台都在線（x570/mbp/msi `ok:true`，心跳 30s 正常寫入 x570 PG）
- `/query`「契約解除後雙方有何回復原狀義務？」hits [0.736, 0.638, 0.369]、答案引 `民法第259條`，
  全程約 1.5s（think:false 生效）
- `/eval`（POST）`{tested:0, hit_rate:0.0}`（3 題皆佔位無 expect_case，屬預期）
- x570 模型 4 個：bge-m3 / bge-reranker / qwen3:14b / qwen3-coder

**本輪觀察（留給各機 opencode 處理）**：
1. ~~MSI 8000 外網不通~~ **已處置（2026-09-23）**：棄用 portproxy 方案。uvicorn 在 WSL 內綁 0.0.0.0
   只對 WSL VM 內有效，改由 **cloudflared（WSL 內）tunnel 直連 localhost:8000** 對外，
   `netsh portproxy`＋防火牆規則已刪（公網 api-msi.ragdemo.win 實測直達）。
   （心跳出站不受影響，registry 仍 ok。）
2. ~~msi 的 models 清單與 mbp 相同~~＋~~mbp 的 ips 內混入 192.168.0.2~~ **已處置**
   （2026-09-22）：ip 污染根因＝IP 準則沿革，已定「只留 tailscale IP」準則（見
   ARCHITECTURE「IP 準則」），`registry.py _ips()` 只收 100.64.0.0/10。各台 pull 後
   **核對 .env 移除 LAN_IP**，心跳 30s 自動清乾淨 registry。

---

## 1. Step 1 已完成（commit aef1138）

主機身份 registry + 位址降級，全走環境變數、不需改碼：

- `backend/app/registry.py`：`backends` 表記錄每台硬體身分（hostname/machine_id/mac/ips）＋
  ollama 模型清單（心跳每 30s 自報），`GET /hosts` 可查全員。
- `rag.py`：`OLLAMA_URLS`/`QDRANT_URLS` 候選清單（逗號分隔，前者優先），
  `_pick()` 選「優先權最高且可用（TCP＋模型齊備）」者快取，連線錯誤 / model 404 自動降級，
  過 `PICK_TTL`（30s）重掃，高位主機回復自動切回；新增 `local_models()`。
- LLM model 依選中主機而定：`OLLAMA_MODELS` 與 `OLLAMA_URLS` 同順序對應
  （如 msi：`x570/mbp=qwen3:14b, msi=qwen3:8b`），`/health` 附 `llm_src` 供診斷。
- `main.py`：`/health` 附 host 欄位、`GET /hosts`、lifespan 內起心跳 loop。
- compose 掛載宿主 `/etc/machine-id`、`/etc/hostname`，避免容器身分混入。
- x570 驗證過：`/health` 回真實主機名 `solo-X570-I-AORUS-PRO-WIFI`、`/hosts` 含 4 個模型、
  fallback 死點自動跳過、`/query` 0.736 分帶法條引註。

關鍵變數（`backend/.env.example` 有完整三台 profile）：

```
HOST_ID        # registry 主鍵＋前端切換鍵：x570 / mbp / msi
TS_IP          # 唯一身分 IP（tailscale，100.64.0.0/10）；LAN_IP 已停用，勿再寫
OLLAMA_URLS    # 逗號分隔候選，例 http://127.0.0.1:11434,http://100.119.83.111:11434
QDRANT_URLS    # 同上，例 http://qdrant:6333（容器內）/ http://localhost:6333（native）
POSTGRES_DSN   # 心跳寫哪台 PG；mbp/msi 可暫時指 x570 那台
REGISTRY_HEARTBEAT  # 心跳間隔秒數（預設 30）
REGISTRY_STALE_MIN  # 心跳時清掉超過 N 分鐘未報到的 host（預設 3）
RERANK_MODEL   # 仍是 stub，注意
GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET  # frontend/.env（dev）；上線放 Cloudflare Pages 變數
SESSION_SECRET # frontend 登入 session cookie 簽章金鑰（>=32 字元亂數）
# Pages 專案 ragdemo-win 的 production secret：GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / SESSION_SECRET 已設（2026-09-22）
```

**檔案結構**：`compose.yaml`（qdrant+postgres+api）、`backend/`（FastAPI）、
`frontend/`（SvelteKit+Cloudflare adapter）、`evals/`（題庫）、`.opencode/agents/`（4 子代理）。

---

## 2. MSI 接手設定（目標：WSL2 裡跑 api，吃 Windows 本機 ollama）

> 決定：**opencode 裝在 WSL2，後端也在 WSL2，ollama 維持 Windows native 不動。**
> Windows 最多再跑一個 `qdrant-x86_64-pc-windows-msvc.exe`（離線接手才需要，見 §4）。

### 2.1 WSL 內一次性的環境（對 MSI 的 machinist 指示）

```bash
# WSL2 → Ubuntu（已在 Windows 端 wsl --install 後）
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl python3 python3-venv python3-pip gh

# gh 登入（repo 是 solo0920 的，需要權限）
gh auth login
git config --global user.name  "Solomon Lee"
git config --global user.email "solo4study@gmail.com"

# opencode + provider key（每台機器獨立設定）
curl -fsSL https://opencode.ai/install | bash
opencode   # 首次 /connect 貼 provider API key

# clone 專案（二擇一）
gh repo clone solo0920/ragdemo.win ~/ragdemo
# 或 git clone https://github.com/solo0920/ragdemo.win ~/ragdemo
cd ~/ragdemo

# 啟用 repo 內 hooks（提交前綴 + push smoke，新機必做一次；見 ARCHITECTURE「Git hooks」）
git config core.hooksPath .githooks
git config core.hooksPath   # 應回 .githooks
```

### 2.2 後端 .env（MSI WSL 用）— 寫到 backend/.env（不進版控）
> IP 準則（2026-09-22 定案）：一律只寫 tailscale IP，不用 LAN_IP（見 ARCHITECTURE「IP 準則」）。

```bash
HOST_ID=msi
TS_IP=100.65.68.106
POSTGRES_PASSWORD=changeme
# WSL 的 127.0.0.1 是 WSL 自己，不是 Windows！
# ollama 已綁 0.0.0.0，用 MSI 的 tailscale IP 直連 Windows 本機 ollama：
OLLAMA_URLS=http://100.65.68.106:11434,http://100.119.83.111:11434
LLM_MODEL=qwen3:8b
# 輕量接手：資料層/Qdrant 優先 x570（tailscale），本機 127.0.0.1:6333 當備援（離線降級）
QDRANT_URLS=http://100.119.83.111:6333,http://127.0.0.1:6333
POSTGRES_DSN=postgresql://rag:changeme@100.119.83.111:5432/ragdemo
EMBED_MODEL=bge-m3:latest
RERANK_MODEL=qllama/bge-reranker-v2-m3:latest
COLLECTION=laws
```

### 2.3 跑起來並驗證（WSL 內，建議 venv）

```bash
cd ~/ragdemo/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r <(printf 'fastapi\nuvicorn[standard]\nhttpx\npydantic\nasyncpg\n')   # 或 pip install fastapi "uvicorn[standard]" httpx pydantic asyncpg
uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env   # 一定要帶 --env-file，否則沒有 MSI profile
```

```bash
# 驗證（另開終端，可在 Mac/別台測）：
curl http://100.65.68.106:8000/health          # 回 ok+host_id=msi，llm=qwen3:8b
curl http://100.65.68.106:8000/query -H 'content-type: application/json' \
     -d '{"question":"契約解除後雙方有何回復原狀義務？"}'   # LLM 吃本機 ollama，資料吃 x570
# 重點：等 30s 心跳後，回 x570 查：
curl http://100.119.83.111:8000/hosts          # 應看到 x570 + msi 兩筆
```

Windows 防火牆（若 8000 不通）：PowerShell（管理員）
```powershell
New-NetFirewallRule -DisplayName "ragdemo-api-8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

### 2.4 讓前端能連 MSI

- 本機 tailnet 內：瀏覽器直連 `http://100.65.68.106:8000`（前端 msi 按鈕已是直連 tailscale URL）。
- 公網/外出：需 Cloudflare Tunnel 路由 `api-msi.ragdemo.win` → `http://100.65.68.106:8000`（見 §4.3）。

### 2.6 在 msi 本機跑前端 dev（2026-09-24 定案，勿在 x570 SSH 上開 dev）

> 背景：之前習慣 SSH 到 x570 開 dev，但用 tailscale IP（如 `100.119.83.111:5173`）開畫面時
> Google 登入會被擋（redirect_uri 必須 HTTPS 白名單，免費 Tailscale 沒 TLS cert，見 §4.8），
> 查詢又被 `disabled={!user}` 卡住。改成在**各機自己 WSL** 跑 dev server 才是正解：
>
> - `localhost:5173` 的 Google redirect 是「瀏覽器所在那台」的 localhost（已在白名單），
>   所以 **msi 自己的瀏覽器開 `http://localhost:5173` 就能登入**（WSL2 自動 bridge 5173↔Windows）。
> - dev 的 vite proxy 把 `/api/*` 打到本機 `localhost:8000`（msi 自己的 api，資料吃 x570 或本機備援），
>   **即時 HMR、不用等 Pages 重部署**；只有正式上線才 push 等 Pages。
> - 提醒：dev 下按「**自動**」＝**本機 backend（msi）回答**，「連線詳細」的檢索後端格顯示 msi 屬預期
>   （資料/模型仍依 `QDRANT_URLS`/`OLLAMA_URLS` 優先序走 x570，見 §4.5「dev 自動」條目）；
>   真正的 failover 順序選擇只在 Pages worker（prod）。

msi 一次性準備：

```bash
cd ~/ragdemo && git config core.hooksPath .githooks
cd frontend && npm ci                     # msi 已有 Node v22
vim ../backend/.env                       # msi profile（見 §2.2）
vim .env                                  # frontend/.env：GOOGLE_CLIENT_ID/SECRET/SESSION_SECRET
                                          #   （值從 x570 frontend/.env 抄，不進 git）
npm run dev -- --host                     # msi 瀏覽器開 http://localhost:5173
```

- 手機/其他 tailnet 裝置要看 dev：仍建議只開公網 `https://ragdemo.win`（tailnet http 登入無解，
  Tailscale 免費版不給 TLS cert）。
- 分工不變：dev 畫面在哪台開都行（各自機 opencode 管各自 dev server），
  **資料主源仍 x570**；`ragdemo.win` 仍由 GitHub Pages 自動部署。

### 2.7 WSL 開機自動啟動 api（2026-09-22 已設定）

- 啟動腳本：`backend/start-msi.sh`（冪等：8000 已有服務就跳過；`setsid nohup` 脫離 session；
  log 寫 `backend/uvicorn.log`、pid 寫 `backend/.uvicorn.pid`）。手動啟動：
  `bash backend/start-msi.sh`
- 1.19.1 起還會一併啟動**本機 qdrant 備援**（`~/qdrant/qdrant`，port 6333，log
  `~/qdrant/qdrant.log`）—— x570 離線時 MSI 的自立資料層，見 §4.1。
- Windows 開機啟動：`solog` 的「啟動」資料夾放 `ragdemo-msi-api.vbs`（隱藏視窗執行
  `wsl.exe -d Ubuntu -u solo -e bash /home/solo/projects/ragdemo.win/backend/start-msi.sh`），
  比照 Ollama.lnk 的作法；**只在 solog 登入時跑**。
- 注意：WSL distro 名寫死 `Ubuntu`、路徑寫死 `/home/solo/projects/ragdemo.win`；
  若新機 clone 到別處需同步改 VBS 與腳本內 `BASE`。
- 系統 python 沒 pip/python3-venv，MSI 用 `uv venv .venv` 建環境（Ubuntu 24.04 無須 sudo）。

---

## 3. 常見坑筆記（跨機 opencode 都要知道）

- 埠被佔：x570 上曾撞 CUPS/legacy-printer-app 搶 8000，已停用；新機若 8000 被佔先查。
- `pkill -f` 會自匹配自己的 cmdline 而卡死 → 用括號技巧：`pkill -f 'wor[k]erd'`。
- wrangler 需 Node ≥22：x570 用 nvm Node v22.23.2（系統 v20 太舊）。
- Cloudflare Worker proxy 未設 `API_ORIGIN` 時回 503：Pages 變數＋`nodejs_compat` flag。
- qdrant scroll 是 POST 不是 GET（`.points/scroll`）。
- **commit 前綴準則（2026-09-22）**：commit message 首行必須 `msi:`/`mbp:`/`x570:`
  開頭（tailscale 機器名前綴）。各機啟用一次 `git config core.hooksPath .githooks`
  （repo 內 `.githooks/commit-msg` 會強制，違反直接拒絕）。詳見 ARCHITECTURE「提交準則」。

---

## 4. 下一步工作（Step 2+，未啟動）

### 4.1 精簡包 snapshot/restore（讓 mbp/msi 無 x570 也能跑）
> **2026-09-22 進度**：MSI、mbp 本機 qdrant 備援＋自動同步皆已完成 ✅。剩餘：擴資料到 500~1000 筆。

**已建立（MSI）**：
- MSI WSL 本機 qdrant 1.19.1（`~/qdrant/qdrant`，port 6333），`start-msi.sh` 啟動時一併拉起。
- 備援資料：從 x570 qdrant 快照還原到本機（目前 3 筆，與 x570 同步；有測試資料驗證增減偵測）。
- **自動同步**：`scripts/sync-snapshot.sh`（x570 建快照→下載→本機刪舊重建還原→驗證點數一致；
  用點數變化偵測新資料，沒變化就 skip；同步後清理 x570 舊快照只留最新）。
  crontab 每 10 分鐘跑一次＋開機後跑；log `~/qdrant/sync.log`、state `~/qdrant/.sync-state`。
- MSI `.env`：`QDRANT_URLS=100.119.83.111:6333,127.0.0.1:6333`
  （x570(tailscale) 優先最新資料，x570 離線自動降級本機）。`POSTGRES_DSN` 仍指 x570 —— 離線時心跳只是
  warning、不影響 query（`/hosts` 會暫時沒資料，可接受）。
- 驗證過：模擬 x570 全離線（QDRANT/OLLAMA 首位換死 IP）→ `/query` 純本機（127.0.0.1:6333＋
  Windows ollama 100.65.68.106:11434）完整作答，答案內容正確。

**已建立（mbp，2026-09-22）**：
- mbp 本機 qdrant 1.19.1（`~/qdrant/qdrant`，aarch64-apple-darwin binary，port 6333，log `~/qdrant/qdrant.log`）。
- 快照同步：`scripts/sync-snapshot.sh` 從 x570 還原（目前 3 筆）；`~/qdrant/.sync-state` 記點數，
  未變化自動 skip；第二次跑已驗證 unchanged。
- 自動化：macOS 的 crontab 被 TCC 擋 → 改用 **launchd agent**（`~/Library/LaunchAgents/`）：
  - `com.ragdemo.qdrant`（登入啟動＋KeepAlive）、`com.ragdemo.api`（uvicorn，登入啟動＋KeepAlive）、
  - `com.ragdemo.sync-snapshot`（每 10 分鐘＋RunAtLoad，log `~/qdrant/sync.log`）。
- mbp `.env`：`QDRANT_URLS=100.119.83.111:6333,127.0.0.1:6333`（x570 優先，離線切本機）。
- 前端：Node v22.23.2（brew `node@22`，PATH 已寫 `~/.zshrc`），`npm install`＋`npm run build` 過。

**待做**：
- 目標：500~1000 筆精選資料即可（ARCHITECTURE 的 Demo 精簡包）。
- 驗收：MSI 上拔掉 x570 連線後 `/query` 仍能答（目前 3 筆已通過）。

### 4.2 前端吃 `/hosts`
- `+page.svelte` 改由 `GET /hosts` 動態產生切換按鈕（label=hostname + 模型清單），
  不再寫死 x570/mbp/msi；localStorage key `ragdemo-backend` 保留。
- **自動模式後端 failover（2026-09-23）**：`自動` 走 Pages worker（`api/[...path]/+server.ts`），
  worker 依 `API_ORIGINS`（或內建 `api-x570→api-mbp→api-msi`）依序嘗試，
  x570 離線（502/503/504/530/1033 或網路失敗）自動切下一台；成功回應帶
  `x-ragdemo-origin` header，health 顯示實際 `host_id`。API_ORIGIN 若命中國內三台
  也展開成完整三台（x570 優先）。前端手動按鈕（x570/mbp/msi）仍直連各台。

### 4.3 公網接手（Cloudflare）
- 一台 tunnel（放 x570）指三個 hostname：api-x570/api-mbp/api-msi.ragdemo.win → 各機 8000。
- Worker `+server.ts` 加 `?backend=` 白名單路由，Pages 設 `API_ORIGIN`＋`nodejs_compat`。
- **已完成（2026-09-22~23，x570）**：
  - 本地型 tunnel `ragdemo-x570`（id 6539736a-...）＋DNS `api-x570.ragdemo.win` → `http://localhost:8000`；
    config 在 `~/.cloudflared/config.yml`，binary 在 `~/.local/bin/cloudflared`。
  - Pages production 已設：`API_ORIGIN=https://api-x570.ragdemo.win`、`GOOGLE_CLIENT_ID`、
    `GOOGLE_CLIENT_SECRET`、`SESSION_SECRET`（wrangler `pages secret put`）。
  - `/api/ingest`、`/api/eval` 由 worker 擋 Google 登入（401）；`/api/query` **2026-09-24 起也需登入**（見 §4.8）。
  - **開機自啟（2026-09-23）**：crontab `@reboot` 已掛（免 sudo）：
    `@reboot setsid ~/.local/bin/cloudflared tunnel --config ~/.cloudflared/config.yml run ragdemo-x570`
    （原手動 setsid nohup 已停用；若要改 systemd，unit 範本見交接清單註解，sudo 執行後關掉 crontab 即可）
  - 憑證（credentials json）勿外洩。

#### x570 tunnel 修復＋改名 checklist（x570 opencode 照此執行）

```bash
# 0) 前置驗證（你必須在 x570 上，且有 git 權限）
cd ~/ragdemo && git pull origin main
# 若你看到的 config 仍是 api-linux → 本 checklist 適用；若已是 api-x570 → 跳過改名步驟

# 1) 確認 cloudflared binary 與 tunnel id
~/.local/bin/cloudflared tunnel list | grep ragdemo-x570   # 應列 id 6539736a-…
ls ~/.cloudflared/                                        # 應有 config.yml + <id>.json

# 2) 改 config ingress hostname：api-linux → api-x570（勿動 service/tunnel 欄位）
sed -i 's/api-linux\.ragdemo\.win/api-x570.ragdemo.win/g' ~/.cloudflared/config.yml
grep hostname ~/.cloudflared/config.yml   # 應只剩 api-x570.ragdemo.win

# 3) DNS 改名（在 x570 已認證 cloudflared 的情況下；否則請使用者在 dashboard 改）
~/.local/bin/cloudflared tunnel route dns ragdemo-x570 api-x570.ragdemo.win
#   驗證： dig +short CNAME api-x570.ragdemo.win → 應回 <tunnel-id>.cfargotunnel.com
#   舊記錄 api-linux 可留（指到同一 tunnel）或刪除——避免誤連先不管

# 4) 更新 Pages secret API_ORIGIN（x570 已用 wrangler 認證過)
npx wrangler pages secret put API_ORIGIN --project-name ragdemo-win
#   輸入 https://api-x570.ragdemo.win

# 5) 設開機自動啟動（systemd，取代手動 setsid nohup → 根治 Down）
sudo ~/.local/bin/cloudflared service install      # 用憑證建 systemd unit（會 prompt 路徑）
#   或手動 unit（若 service install 不支援本地型）：
#   sudo tee /etc/systemd/system/cloudflared-ragdemo.service >/dev/null <<'EOF'
#   [Unit]
#   Description=Cloudflare Tunnel ragdemo-x570
#   After=network-online.target
#   Wants=network-online.target
#   [Service]
#   ExecStart=/home/<user>/.local/bin/cloudflared tunnel --config /home/<user>/.cloudflared/config.yml run ragdemo-x570
#   Restart=always
#   RestartSec=5
#   [Install]
#   WantedBy=multi-user.target
#   EOF
#   sudo systemctl daemon-reload && sudo systemctl enable --now cloudflared-ragdemo
#   （principal 記得把 /home/<user> 換成實際路徑）

# 6) 驗證
systemctl status cloudflared-ragdemo --no-pager | head -8     # active (running)
curl -s https://api-x570.ragdemo.win/health                    # 回 host_id=x570 即完成
curl -s https://ragdemo.win/api/health                         # 回 x570 health = Pages 鏈路 OK

# 7) 更新本段 ROADMAP：把「待辦 1-2」標記完成，commit 前綴用 x570:
```
> **分工註記（2026-09-23）**：tunnel 各機管各機——x570 管 `api-x570`、mbp 管 `api-mbp`、
> msi 管 `api-msi`（名稱已定，實體待建）。Dashboard 層（建 tunnel/DNS）只有使用者能操作；
> opencode 負責各自機器上的 cloudflared 安裝、config、自動啟動與驗證。

#### mbp / msi 各自建 tunnel checklist（在該機執行，host=mbp|msi 替換）
> 前置：該機已有 git repo、tailscale 在線、8000 後端可跑（mbp launchd／msi start-msi.sh）。
> credentials json 必須存在該機自己（本地型 create 就是在該機產生），所以由各機執行，勿搬移。

```bash
# 1) 裝 cloudflared（免 sudo，~/.local/bin）
mkdir -p ~/.local/bin
curl -fsSL -o /tmp/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64   # MSI 用; mbp 用 darwin: cloudflared-darwin-amd64
chmod +x /tmp/cloudflared && mv /tmp/cloudflared ~/.local/bin/cloudflared

# 2) 登入（裝置碼，選 zone ragdemo.win）
~/.local/bin/cloudflared tunnel login

# 3) 建本地型 tunnel（名字 = ragdemo-<host>；同時生 credentials json）
~/.local/bin/cloudflared tunnel create ragdemo-<host>
#   記下輸出的 id（形如 xxxxxxxx-xxxx-...）

# 4) DNS
~/.local/bin/cloudflared tunnel route dns ragdemo-<host> api-<host>.ragdemo.win

# 5) config：service 指本機後端。
#    mbp：hostname api-mbp.ragdemo.win → http://localhost:8000
#    msi：hostname api-msi.ragdemo.win  → http://localhost:8000（WSL 內，cloudflared 也放 WSL 內）
cat > ~/.cloudflared/config.yml <<EOF
tunnel: <建立的 id>
credentials-file: $HOME/.cloudflared/<id>.json
ingress:
  - hostname: api-<host>.ragdemo.win
    service: http://localhost:8000
  - service: http_status:404
EOF

# 6) 開機自啟（免 sudo 版：crontab @reboot）
( crontab -l 2>/dev/null | grep -v 'cloudflared tunnel.*run ragdemo-<host>'; \
  echo '@reboot setsid ~/.local/bin/cloudflared tunnel --config ~/.cloudflared/config.yml run ragdemo-<host> >> /tmp/cfd.log 2>&1' ) | crontab -

# 7) 全起 + 驗證
~/.local/bin/cloudflared tunnel run ragdemo-<host> &
sleep 3
curl -s https://api-<host>.ragdemo.win/health   # 應回 host_id=<host>

# 8) 不 push API_ORIGIN（Pages 仍指 x570）；接管時才改 Pages API_ORIGIN 或前端 ?backend=
```

**已完成（msi，2026-09-23，commit 前綴 msi:）**：
- cloudflared 2026.9.1 放 WSL 內 `~/.local/bin/cloudflared`；tunnel `ragdemo-msi`
  （id `c2280387-c2b7-484f-ab8e-691534516db4`）＋ DNS `api-msi.ragdemo.win` → `http://localhost:8000`。
- `~/.cloudflared/config.yml`：ingress `api-msi.ragdemo.win` → `http://localhost:8000` + 404 fallback。
- 開機自啟：crontab `@reboot` 已掛（**用絕對路徑＋`--protocol http2`**，見下方心得）。
- 驗證：`curl https://api-msi.ragdemo.win/health` → 回 `host_id=msi`；dashboard Connections 2x。

**實戰心得（2026-09-23，msi）**：
- WSL2 的 UDP/QUIC 連 edge 會 timeout（所有 edge 失敗、1033）→ **必須加 `--protocol http2`**
  （precheck 也是降級建議 http2）。已寫進 @reboot。
- crontab 環境**不展開 `~`** → @reboot 用 `/bin/bash -lc "... 絕對路徑 ..."` 包起來。
- mbp 是 macOS（可 QUIC），但仍建議同樣加 `--protocol http2` 以穩定。

> **已完成（2026-09-23，mbp）**：
> - local tunnel `ragdemo-mbp`（id 2ae52a95-ed8c-4b58-84aa-20f1284d73c5）＋ DNS `api-mbp.ragdemo.win` → `http://localhost:8000`；
>   config 在 `~/.cloudflared/config.yml`，binary 在 `~/.local/bin/cloudflared`（Apple Silicon 用 **darwin-arm64** 而非 checklist 的 amd64）。
> - 開機自啟：mbp 的 crontab 被 macOS TCC 擋，改用 **launchd** `com.ragdemo.tunnel`（log `/tmp/cfd.log`），
>   `--protocol http2`（採 msi 心得，穩定性優先）＋ KeepAlive，效果等同 @reboot。
> - 驗證：`curl -s https://api-mbp.ragdemo.win/health` 回 `host_id=mbp` ✅；Pages `API_ORIGIN` 未動（仍指 x570）。

#### keepalive 補齊 checklist（2026-09-23 定案，規範見 ARCHITECTURE「公網接手」節）

> 三台統一 keepalive，缺一不可。msi 已完成（crontab restart loop＋`--protocol http2`）；
> **x570 / mbp pull 後照此補齊**：

- [ ] **進程自動重啟**：mbp 已有 launchd `KeepAlive` ✅；x570 把 crontab `@reboot` 單次啟動
      改成 restart loop 範本（ARCHITECTURE 該節，替換 `ragdemo-<host>`；或改 systemd 需 sudo）
- [ ] **`--protocol http2`**：mbp ✅；x570 在 @reboot / systemd 的 cloudflared 命令加 `--protocol http2`
- [ ] **驗證**：`ps aux | grep [c]loudflared` 在跑；
      `curl http://127.0.0.1:20241/metrics | grep ha_connections`（應 4 條）；
      `curl -s https://api-<host>.ragdemo.win/health` 回 `host_id`
- [ ] **崩潰復活測試**：`kill` 掉 cloudflared，5 秒後 restart loop / launchd 自動拉回

#### 模型 keepalive（2026-09-23 定案，各機預設 model 啟動即常駐）

> 規範見 ARCHITECTURE「模型 keepalive」。**三台 pull＋重啟 api 後即自動生效**：
> - `rag.py` embed/generate 帶 `keep_alive=KEEP_ALIVE`（預設 `-1` 常駐，可在 `.env` 覆寫）；
>   api 啟動時 `warmup()` 預載該機預設 LLM＋`bge-m3`，首個 query 不再冷載入。
> - 驗證：`ollama ps` 看到預設模型在列且 UNLOAD 為空白（常駐）；重啟 api 後首個 `/query` 不慢。
> - mbp 已實作並驗證（commit `mbp:`）；x570 / msi pull 後 `launchctl kickstart -k`／`start-msi.sh` 重啟即可。

#### 接管整備與來源可追溯（2026-09-23 定案）

> **Q：x570 斷聯時 msi/mbp 能否自己回應？**
> A：RAG 資料主源在 x570（Qdrant 向量、Postgres registry、ollama）。msi/mbp 要能獨自回答，
> 必須「自給三條件」全過：本機 ollama 有 embed(bge-m3)＋LLM、本機 qdrant 有 `laws` 資料、
> tunnel keepalive 正常。**x570 不在，任何一台要回答 = 那台的 127.0.0.1 qdrant 要真有資料**，
> 資料單點是 x570 的向量庫（§4.1 snapshot/restore 就是為了解這題，尚未做）。

**三機自給檢查清單（各自在某機執行）**
```bash
# 1) 本機 ollama：embed + LLM 都要有
curl -s 127.0.0.1:11434/api/tags | python3 -c "import sys,json;print([m['name'] for m in json.load(sys.stdin)['models']])"
#    需要包含：bge-m3:latest、qwen3:14b（或 qwen3:8b/4b）；缺 embedding 就是答不了
# 2) 本機 qdrant：laws 是否真的有資料
curl -s 127.0.0.1:6333/collections/laws | python3 -c "import sys,json;d=json.load(sys.stdin);print('points:',d['result']['points_count'])"
#    >0 = x570 斷聯時這台可完整回答；0/404 = 只剩「找不到」或 502
# 3) tunnel keepalive（見上節 checklist）
```

**本次回應來源表（已上線並驗證，2026-09-23 x570，mbp/msi 比照辦理）**
- 前端在回答最前面顯示**來源表**：三台連線狀態＋**本次 Qdrant 由哪台提供**＋**LLM 由哪台提供**；
  目的是做「前後對照」的除錯利器（可立刻看出資料/模型被誰供給）。
- 資料流：worker `?backend=` 先探三台 → 挑台 → 該後端 `rag.py::answer()` 回傳 `src`
  （`{qdrant:{host,url}, llm:{host,url,model}}`，URL 依 `_KNOWN_IPS`（100.119.83.111=x570、
  100.64.121.9=mbp、100.65.68.106=msi）映射成主機 id；**裸主機名/127.0.0.1/localhost 一律標本機**）
  → worker 原封轉傳 → 前端 `+page.svelte` 的 `statusRows()` 組 **HTML `<table>`** 顯示
  （不用純文字 md，瀏覽器才不會整排錯位）。
- **mbp / msi 交接：比照 x570 設計**，pull＋重啟後端後即自動生效：
  - `git pull origin main`；msi 先 `pkill -f 'uvicorn.*8000'` 再 `bash backend/start-msi.sh`
    （start-msi.sh 偵到 8000 在跑會跳過，不重拉新碼）；mbp 用 launchd 重啟
    （`launchctl kickstart -k`）。
  - 前端由 Pages 自動部署，不需動手；worker 已有 `?backend=` 與轉傳邏輯。
  - 驗收：`curl -s -X POST 'https://ragdemo.win/api/query?backend=<host>' ...` 回應須含 `src`；
    未含 = 那台還在跑舊 rag.py。前端來源表該格會顯示 `-`。

### 4.4 評測上線門檻
- ~~`rag.py rerank()` 接真正 reranker 打分（目前還是 stub）~~ rerank 仍走向量分數；
  但法規語料下 hybrid(DBSF 分數融合) 已達 **hit_rate=1.0（14/14，2026-09-23）**，門檻 0.8 已過（法規）。
  註：原本用 RRF 只看排名，query「證券交易法第11條」這類熱門條號會漏真身（證交法11 在 sparse
  腿排到 193 名）；改 DBSF 後該題 top1 正確命中。
- `evals/questions.json` 已換 14 題真實法條題（expect_law，內容見檔案）。
- 判決語料尚未進 corpus（`laws_demo_backup` 暫存 demo 判決），/eval 目前以法規為主；
  判決 ingest 屬 ingest 分工，列後續。
- 判決注意個資去識別化，回答僅供參考非法律意見。

### 4.5 Troubleshooting（2026-09-23 實測紀錄）

**特殊狀況：拔線但主機仍可達（x570 自動改走 Wi-Fi）**
- 現象：人工拔 x570 網路線後，前端「自動」仍顯示 `⦿ x570｜qwen3:14b`，或查詢結果「明顯不對」。
- 原因：x570 除 LAN 外另有 **Wi-Fi / tailscale direct** 路徑——拔 LAN 線後自動改用 Wi-Fi，
  cloudflared 連 edge 沒斷，`api-x570` 實測連續 5 次 HTTP 200；tailscale ping `direct 2ms`。
  自動模式依 x570→mbp→msi 探測看到「x570 活著」就選它，**符合設計，不是 bug**。
- 判定離線的方法（在 msi）：
  ```bash
  # 1) tailscale 連線方式：direct=真通；relay=殘存
  /c/Program\ Files/Tailscale/tailscale.exe ping -c 2 x570
  # 2) 公網連續探測（HTTP 000/530 才算斷）
  for i in 1 2 3; do curl -s -m 12 -o /dev/null -w "try$i: HTTP %{http_code}\n" https://api-x570.ragdemo.win/health; done
  # 3) worker 自動模式實際選中誰
  curl -s https://ragdemo.win/api/health
  ```
- 驗證（2026-09-23）：關閉分享器後 x570 才真正離線 → 自動模式正確跳 `mbp`（`⦿ mbp｜qwen3:14b｜715ms`）。
- 教訓：**「拔線」≠「離線」**；排查時先確認目標機的實際網路路徑（LAN/Wi-Fi/tailscale），
  勿以單次 HTTP 000 或直覺判定主機死亡。

**特殊狀況：資料庫只有 3 筆 demo → /query 回「找不到，不要編造」**
- 現象：問「欠薪/勞基法」，回答「找不到，不要編造」，但引用列出刑法271/民法259/判決123。
- 原因：`laws` collection 目前僅 3 筆 demo（刑法271、民法259、判決123，x570/本機一致），
  無勞基法內容 → 召回只撈得出這 3 筆（score 0.28~0.34 低分）→ LLM 誠實拒答。
  是**資料量問題，非系統故障**；擴 500~1000 筆精選後自然改善（見 §4.4）。
- 排查：`/collections/laws/points/scroll` 看實際筆數與摘要，勿直接歸咎模型選擇。

**特殊狀況：msi 公網 502 → 前端「✗ Failed to fetch／所有後端皆無法連線」**
- 現象（2026-09-23）：前端「連線與來源」顯示 x570 ✅／mbp ✅／**msi ❌**，選 msi 即「所有後端皆
  無法連線」；但 x570/mbp 都正常，看似無理。
- 原因：**msi 的 uvicorn（WSL 內）已死**——log 末行 `INFO: Shutting down`（WSL 重啟時被收掉）。
  WSL 重啟後 crontab `@reboot` 只拉起 cloudflared（tunnel 有在跑），**api 沒有 keepalive**，
  靠「Windows 登入 startup」才啟動 → WSL 重啟不觸發 → 本機 8000 無 listener →
  tunnel 轉發得到 502 → worker 探測失敗。是**進程層缺口，非連線/模型問題**。
- 排查指令（在該機）：
  ```bash
  ps aux | grep [u]vicorn                      # 空 = uvicorn 掛了
  curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health   # 000 = 沒在聽
  curl -s -o /dev/null -w '%{http_code}' https://api-msi.ragdemo.win/health  # 502 = tunnel 通但 origin 死
  tail -5 backend/uvicorn.log                  # 末行 Shutting down = WSL 重啟收掉，非崩潰
  ```
- 修復（2026-09-23 已做）：msi crontab 新增 `restart loop` 包 `backend/start-msi.sh`
  （每 30s 冪等檢查，WSL 重啟/崩潰自動拉起 uvicorn＋qdrant，見 ARCHITECTURE keepalive 規範）。
- 教訓：**「隧道有在跑」≠「api 在跑」**；公網 502 先查 origin（localhost:8000）而非 tunnel。

**特殊狀況：dev（localhost:5173）「連線與來源」彈窗主機狀態全 ❌＋檢索後端「-」**
- 現象（2026-09-24）：msi 本機跑 dev server，登入後 query 正常、Qdrant/LLM 來源也正常
  （x570），但同一彈窗的**主機狀態三台全 ❌**、檢索後端顯示「-」。
- 原因：**dev 的 vite proxy 把 `/api/*` 直送本機 backend（localhost:8000），繞過 Pages worker**；
  而「主機狀態」的 `log` 是 worker 每支 query 並行探測三台公網 api 後注入的。backend `/query`
  原本沒回 `host`/`log` 欄位 → 前端 `result.log[id]==='連線成功'` 永不成立、`result.host` undefined。
  是 **dev/prod 資料來源落差，非連線故障**（點的「連線詳細」吃的是 `result`，不是 health）。
- 修復（2026-09-24，commit `d3fd386` + 後續補 ok）：backend `answer()` 補 `host: HOST_ID`＋
  `log`＋`ok: True`；新增 `_host_probe_log()` 並行探測三台公網 `api-*.ragdemo.win/health`
  （與 worker 同語意、同字串；`HOST_API`/`PROBE_TIMEOUT` 環境變數可覆寫）。**prod 的 worker
  會自行覆蓋這三個欄位**，不衝突。實測三台連線成功、probe 約 1.3s（tunnel 往返）。
  另坑：光補 host/log 後 dev query 仍「查詢失敗」——前端 `ask()` 有 `result.ok` 斷言，
  backend 原本沒回 `ok`；補上後 dev/prod 皆通。
  （2026-09-24 mbp 實例）**pull 進樹 ≠ 重啟生效**：`ok:True`（c32eb47）隨 rebase 進本地，但
  uvicorn 仍跑舊碼 → dev query 一樣「查詢失敗」；`launchctl kickstart -k gui/$(id -u)/com.ragdemo.api`
  後即通。改後端碼後，先打 `localhost:8000/query` 確認新欄位真的在新起的進程上。
- 教訓：**dev 與 prod 的資訊來源不同（backend vs worker）**；排查「資訊型 UI」問題時，
  先確認該請求實際打到誰（`vite proxy` → backend，還是 `worker`）。
- 操作備註：重啟服務忌用 `pkill -f 'uvicorn app.main'` 這類未加 bracket 的 pattern——執行中的
  shell 自身 cmdline 含有同字串，**會把自己一起殺掉**（輸出截斷、後續命令沒跑）。請用
  `pkill -f '[u]vicorn app.main'` 的 `[x]` 寫法（re 匹配 x 但不匹配字面 `[x]`）。

**特殊狀況：dev 按「自動」但檢索後端顯示 msi（2026-09-24）**
- 現象：dev（localhost:5173）按「自動」送出 query，「連線詳細」主機狀態 x570 ✅、
  但檢索後端顯示 msi，看似「x570 通卻不選 x570」。
- 原因：**dev 的 vite proxy 把 `/api/*` 直送本機 backend（msi），不做 failover**（§2.6 設計）；
  「自動」挑選順序（x570→mbp→msi）只存在於 Pages worker（prod）。dev 下實際為：
  - 主機狀態（log）＝本機 backend 即時探測三台公網（真實，故 x570 ✅）
  - 檢索後端（host）＝「回答的 API 實例」→ msi（dev 就是 msi 本機在答）
  - 資料/模型仍依 `QDRANT_URLS`/`OLLAMA_URLS` 優先序走 x570 → src 的 Qdrant/LLM 兩格顯示 x570
- 實測證據：`curl -s -X POST 'http://localhost:5173/api/query?backend=auto' -d '{"question":...}'`
  → host=msi、log 三台全「連線成功」、src.qdrant/src.llm=x570；prod `ragdemo.win/api/health`
  自動路由回 api-x570。**不是選錯**：資料與模型都照 x570 優先，msi 只是 dev 的 API 外殼。
- 教訓：dev 驗證「檢索後端」格＝本機 backend；要驗證真正的 failover 順序請看 prod（ragdemo.win）。

**特殊狀況：x570/mbp 開機但公網 530（2026-09-24，msi 記錄，交 x570/mbp 接手）**
- 現象：x570/mbp 已開機、tailscale 可達（x570 22 通但無 key），但 `api-x570`/`api-mbp.ragdemo.win`
  回 **530**（tunnel 離線）；msi 200。worker 自動模式落到 msi，「連線詳細」x570/mbp ❌。
- root cause（mbp，2026-09-24）：**開機未登入 → 使用者 LaunchAgent 不載入**。mbp 的四個
  `com.ragdemo.*` 放 `~/Library/LaunchAgents/`，屬 `gui/<uid>` domain，帳號 GUI 登入（指紋）前不會跑
  → tunnel/api/qdrant 全無 → 公網 530。指紋登入後 `RunAtLoad` 自動全起、KeepAlive 照常。
  2026-09-24 上午 msi 量測到 530 即落在 mbp 開機後未登入期間；登入後三台皆 200、無須其他修復。
  （Ollama.app 亦登入後才起；規範與 LaunchDaemons 升級選項見 ARCHITECTURE「公網接手」。）
- 交接：詳細診斷＋各機修復 checklist（systemd/launchd/crontab keepalive、`--protocol http2`）見
  **`TUNNEL-530-2026-09-24.md`**（repo 根）；修完回報並在本節補 root cause。
- （**x570 接手 2026-09-24 已修/自癒**）現況：三台公網皆回 200
  （`for h in x570 mbp msi; do curl -s -m 12 -o /dev/null -w "$h %{http_code}\n" https://api-$h.ragdemo.win/health; done`），
  backend `/status`＝三台全「連線成功」。root cause：x570 的 cloudflared 曾有段無人執行
  （bash loop PID 起於 13:15、cloudflared 新 PID 8571 起於 13:40，兩者之間即 msi 觀測到的 530 窗口），
  **由 crontab `@reboot` restart loop（`--protocol http2`）如設計自動拉回，非 keepalive 缺口**。
  x570 無 systemd unit（`cloudflared-ragdemo.service` 不存在）、keepalive 僅 crontab 一套、單實例無互搶，
  log 無 `[tunnel] exit` 標記即代表 loop 從未斷裂。
  教訓：**530＝cloudflared 進程短暫死亡，restart loop 會自癒；數小時未癒才需查 loop 本身是否也死了**
  （`ps aux | grep [c]loudflared` 看 bash loop＋cloudflared 兩 PID 是否同在）。
  另：接手時 running api 容器缺最新碼（`GET /status` 404）→ `docker compose build api && up -d api` 後才通，同 §4.5 前一條「pull 進樹≠重啟生效」，改後端碼一律重建容器。

**特殊狀況：開機照常登入後 tunnel 才起來（2026-09-24 x570 實測，root cause 定案）**
- 現象：按電源開機後公網 530，**直到使用者登入並開 opencode 才變 200**；看起來像「服務等登入」。
- timeline：boot 13:15:42 → cronie loop（PID 1876）13:15:49 就已啟動 → cloudflared 卻拖到 **13:40:32**，與登入 session（13:40:16）差 16 秒。
- root cause：**crontab `@reboot` loop 的 log 導向 `>> /tmp/opencode/cfdrun.log`，但 `/tmp` 開機是空的、
  `/tmp/opencode` 只有 opencode 工具會建** → loop 每 5s 重跑一次 `>> ...` 時 bash 開 redirect 失敗
  （目錄不存在）→ **cloudflared 根本沒被執行**，且失敗無任何 log（連 `[tunnel] exit` 標記都沒寫）。
  登入＋開 opencode 建出 `/tmp/opencode` 後，下一次 loop 才真正跑起 cloudflared。docker 容器不受影響
  （`unless-stopped`，postgres/qdrant 13:15:55 隨開機起）。
- 判定：`stat -c%y /tmp/opencode` 的 ctime 與 cloudflared 新 PID／log 首行**同一秒** → 實錘。
- 修復（`x570:`）：crontab 內兩處導向改為 `/home/solo/.cloudflared/cfd.log`（home 在 `/`、開機即存在、
  不依賴工具；與 TUNNEL-530 文件的 log 位置一致），已重拉 loop 即刻生效。
- 教訓：**keepalive 的 log 路徑不能依賴工具專用臨時目錄（`/tmp/opencode`）**；shell redirect 失敗會讓
  被包的程式根本不執行且「無聲失敗」。排查「登入才起服務」先對開機時間軸＋`ps -o lstart`。

### 4.6 外出 demo 模式（2026-09-23 定案）

- 原則：**出門＝當 x570 斷線**，現有自動 failover 已涵蓋、零設定：
  開 ragdemo.win → worker 探到 x570 離線 → 自動用 **mbp**（本機 qwen3:14b＋qdrant）；
  僅 mbp 也掛 → msi 本機 qwen3:8b 頂上（品質略降仍可 demo）。
  不用 Docker：角色仍是單一備援主機，launchd/crontab 原生棧已達標（見 ARCHITECTURE keepalive）。
- 出發前檢查（在 mbp）：
  ```bash
  bash scripts/sync-snapshot.sh                          # 同步最新 laws 到 mbp 本機
  curl -s http://127.0.0.1:6333/collections/laws | python3 -c "import sys,json;print(json.load(sys.stdin)['result']['points_count'])"
  curl -s http://127.0.0.1:8000/health                   # 本機 api 活著
  ```
- demo 驗收：前端「自動」顯示 `⦿ mbp｜qwen3:14b｜laws｜~xxxms`、來源表 mbp ✅；
  若落 msi 表示 mbp 離線（正常降級）。

### 4.7 法規全量數據管線（law.moj.gov.tw，2026-09-23 定案）
- 資料源：`https://law.moj.gov.tw/api/ch/law/json`（帶 UA；回傳其實是 ZIP）。2026-09-11 版
  ＝**1347 部**（憲法9＋法律1338）、條文 **47,281**（另 4,062 章節標題）；已廢止 322、刪除條文 1,019。
- 落點：`data/laws/`（`laws.json`=原始 ZIP、`ChLaw.json`、`manifest.csv`、`schema.csv`；
  `laws_flat.jsonl`、`laws_flat.parquet`＝條目扁平＋備份；`laws_meta.jsonl/.parquet`＝法規層）。
  原始與 parquet/json 進 .gitignore，腳本進版控。
- 清洗規則與 Qdrant/PG 策略：**`ingest/laws/DESIGN.md`**（已寫）＋腳本：
  `ingest/laws/normalize.py`（stdlib 清洗→JSONL）、`ingest/laws/eda.py`（duckdb 分析→parquet）。
- duckdb 筆記：本機 pypi/github 下大檔極慢，建 venv 後用清華鏡像秒裝：
  `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple duckdb pandas`。
- 重點分析結論：條文中位數 79 字、p99 638、僅 4 則 >3000 字→**一條一向量**為主；
  bge-m3 是 hybrid 模型 → Qdrant `dense+sparse` 雙向量＋**DBSF 分數融合**，條號/專有名詞精準；
  payload 帶 `pcode/law_name/article_seq/article_no/chapter/is_repealed/is_abandoned/category`。
- **已實作上線（x570）**：`pg_load.py`→PG law/article/law_import（含 source_sha256/UpdateDate 審計，
  2.4s 落庫 1347/47281 條）；`qdrant_load.py`→laws collection dense+sparse 重灌 **39,879 條**
  （條號/章節前綴入向量、點 ID u64、sparse u32、超長文本逐步縮短容錯）；backend `rag.py`
  查詢改 **DBSF hybrid**（每腿 prefetch 500，避免熱門條號漏真身）＋**條號精準分支**
  （scroll 同條號候選＋本地 sparse-dot＋法名大gram 重疊計分 prepend top3，破擁擠/同分/排序外）
  ＋payload 渲染；點 ID 用 md5 整數（Qdrant 只收 u64/uuid）。
- 條文回答與引用顯示：`backend/app/law_struct.py` 解析「項=非款行、款=一、」結構，
  `_ref()` 附「1項6款」讓回答列舉各款要旨；`/query` hits 附 `art/item/para_count/item_count`，
  前端引用改為「**機率｜法名+條號｜款位｜內容**」。
- **評測：`/eval` hit_rate=1.0（14/14 real questions，expect_law 比對）**，公網 ragdemo.win 全通。
- 舊 demo 3 點備份 `laws_demo_backup`；後續：判決語料、msi/mbp 同步（見 DESIGN §5）。
- **msi/mbp 同步修復（2026-09-24，三台 pull 即得新版）**：`laws` 改 named dense+sparse
  後，`sync-snapshot.sh` 用舊單向量 config 預建 collection → snapshot 相容性失敗
  （`restore upload failed` → 本機 0 點，備援失效）。**定案做法（mbp 109b894）＝刪本機後
  直接 `priority=snapshot` 上傳還原**（快照內建設定重建含 sparse，不預建）；msi 已還原
  39,879 點並以「x570 離線」模式實測本機 hybrid 檢索正常（條號題 top1 民法259）。

### 4.8 登入管制＋前端 UI（2026-09-24 定案，x570）
- **`/api/query` 需 Google 登入（commit 669966b）**：Pages worker `+server.ts` 的 `SENSITIVE`
  從 `{ingest, eval}` 加入 `query`——未登入（無有效 `ragdemo_session` HMAC cookie）回 **401**
  「請先登入 Google 後再操作」；`SESSION_SECRET` 未設則 503。前端未登入時禁用
  textarea/送出＋顯示登入引導，`ask()` 先檢查 `user`。
  限制：guard 只在 **Pages 代理層**，三台後端直達 URL（`api-<host>.ragdemo.win/query`）本身仍
  無驗證（若要連後端一起鎖，需後端以同 `SESSION_SECRET` 驗同一 cookie，列後續）。
- **UI 調整（commit bd85cc0、05ebc0c、126ee11）**：
  - Google 登入按鈕併進標題列（`.head` flex `space-between`），不再獨立成列突起。
  - 「連線與來源」表格改為後端列最右側「**連線詳細 ▸**」按鈕：點擊才浮出小彈窗
    （含三角形箭頭、`position:absolute` 貼在按鈕下）顯示三台探測 log＋Qdrant 檢索／
    LLM 生成來源；僅查詢過（有 `result`）時才出現。原永久表格移除。
- **連線詳細按鈕常駐（2026-09-24，commit msi）**：按鈕不再包在 `{#if result}`（原本要送出後才浮現），
  改為常駐 `.sw-right`。新增 backend `GET /status`（直接回 `rag._host_probe_log()` 的三台探測 log）；
  未查詢時點開彈窗＝**即時探測**「主機狀態」（頁面載入＋每次開彈窗＋切換主機時刷新），
  檢索方式區塊顯示「尚未送出查詢」；查詢過仍以該次 query 的 log/src 為準（語意不變）。
  路徑：dev proxy `/api/status`→本機 8000、prod worker 轉發、手動選主機直連，三路皆通。
- 今日檢查：工作樹乾淨、無密鑰外洩（僅 `.env.example`/compose fallback 的佔位 `changeme`）、
  前端 `npm run build`＋svelte-check 0 error、`/eval` 14/14=1.0。
- **dev 登入注意**：`auth/login` 的 `redirect_uri`＝「目前 origin + `/auth/callback`」，所以用
  tailscale IP（如 `100.119.83.111:5173`）開 dev 登入前，要先到 Google Console 的該 OAuth
  Client 把該 origin 加入授權重導 URI（無萬用字元），否則 `redirect_uri_mismatch`；本機
  `http://localhost:5173` 已在白名單可直接用（`+server.ts:16` 逐 origin 組 redirect）。

### 4.9 判決案例數據管線（規劃，2026-09-24 定案方向，尚未實作）

> 完整設計：**`ingest/cases/DESIGN.md`**。動機：目前 qdrant 只有法條（39,879 點），
> 之後要加判決案例，資料量會暴增——先定「三層架構」讓量級可控（50G 內極寬裕）。

- **三層架構（定案）**：**parquet＝全文長期歸檔**（清洗後原文＋全 metadata，按年 partition）；
  **Postgres＝metadata 主庫**（x570，source of truth：去重／增量／審計／faceting，比照 laws）；
  **Qdrant `cases`＝檢索瘦身視圖**（payload 只放案號/法院/日期/案由/主文＋要旨節錄，**不放全文**）。
- 備援離線不需要 PG：qdrant payload 自帶檢索＋回答所需 metadata，msi/mbp 離線照樣答。
- 判決專屬設計：800~1200 字 chunk＋150 重疊（點 ID md5(case_no:idx)）；dense+sparse hybrid(同 laws)＋
  **int8 quantization**（laws 沒有）；`sync-snapshot.sh` 改 **per-collection state**（`.sync-state-laws`/`-cases`）。
- 量級：1,000 件 ≈ 150~450MB；5,000 件 ≈ 0.7~2GB（詳見 DESIGN §3 對照表）。
- 里程碑：**M1** ingest 管線（normalize→PG→parquet→qdrant_load，500~1,000 件選樣）；**M2** backend
  `rag.py` 雙 collection 檢索＋真正 reranker；**M3** eval 題庫 3→50（expect_case）＋hit_rate≥0.8。
- **開放問題（待使用者定）**：① 判決資料源格式（`judgment.judicial.gov.tw` JSON/HTML/txt？）
  ② 選樣範圍（年份＋法院層級＋案由子集）③ 要旨是否第一階段就上 LLM 抽取。

---

## 5. MB 參考：x570 主要指令

```bash
docker compose up -d --build   # 改碼後重建 api
curl localhost:8000/health
curl localhost:8000/hosts
cd ~/ragdemo/frontend && npm run dev   # 前端 dev（proxy → localhost:8000）
```