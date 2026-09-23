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
| LLM 預設 | qwen3:14b | qwen3:14b | qwen3:4b |
| 模型 | bge-m3, bge-reranker, qwen3:14b, qwen3-coder | bge-m3, bge-reranker, qwen3:14b, qwen3-coder(+next) | bge-m3, bge-reranker, qwen3:4b, qwen2.5-coder:7b |

模型注意：**qwen3.5:4b 已棄用**（template `{{ .Prompt }}` 壞掉），MSI 一律用 `qwen3:4b`。
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
1. **MSI 8000 外網不通**：x570 直連 `100.65.68.106:8000` 逾時。uvicorn 在 WSL 內綁 0.0.0.0
   只對 WSL VM 內有效，Windows 側需 `netsh interface portproxy add v4tov4 listenport=8000
   connectaddress=<WSL IP>`＋防火牆規則才會對外開（心跳出站不受影響，registry 仍 ok）。
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
  （如 msi：`x570/mbp=qwen3:14b, msi=qwen3:4b`），`/health` 附 `llm_src` 供診斷。
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
LLM_MODEL=qwen3:4b
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
curl http://100.65.68.106:8000/health          # 回 ok+host_id=msi，llm=qwen3:4b
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

### 2.5 WSL 開機自動啟動 api（2026-09-22 已設定）

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
  - `/api/ingest`、`/api/eval` 由 worker 擋 Google 登入（401）；`/api/query` 公網放行。
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

### 4.4 評測上線門檻
- `rag.py rerank()` 接真正 reranker 打分（目前還是 stub）。
- `evals/questions.json` 佔位 3 題 → 擴 50 題含 `expect_case`；`hit_rate ≥ 0.8` 才進 UI。
- 判決注意個資去識別化，回答僅供參考非法律意見。

---

## 5. MB 參考：x570 主要指令

```bash
docker compose up -d --build   # 改碼後重建 api
curl localhost:8000/health
curl localhost:8000/hosts
cd ~/ragdemo/frontend && npm run dev   # 前端 dev（proxy → localhost:8000）
```