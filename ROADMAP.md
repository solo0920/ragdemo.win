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
| api:8000/qdrant:6333/pg:5432 | ✅（docker compose） | ✅（docker compose，OrbStack） | api ✅（WSL2）/qdrant ✅（本機備援）/pg ✗ |
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

關鍵變數（完整三台 profile 見 `ARCHITECTURE.md`「三機分工」，逐項說明見根 `.env.example`）：

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

### 2.3 跑起來並驗證（WSL 內，uv 為主）

```bash
cd ~/ragdemo/backend
uv sync                          # 依 backend/pyproject.toml＋uv.lock（已 commit）建 backend/.venv
# （如裝了 uv：`uv venv .venv` + `uv pip install ...` 二選一；一律以 uv 建環境，勿 python3 -m venv）
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env   # 一定要帶 --env-file，否則沒有 MSI profile
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
cd frontend && pnpm i                      # pnpm 遷移後（2026-09-26）：corepack enable 後用 pnpm
vim ../backend/.env                       # msi profile（見 §2.2）
vim .env                                  # frontend/.env：GOOGLE_CLIENT_ID/SECRET/SESSION_SECRET
                                          #   （值從 x570 frontend/.env 抄，不進 git）
pnpm run dev -- --host                   # msi 瀏覽器開 http://localhost:5173
```

- 手機/其他 tailnet 裝置要看 dev：仍建議只開公網 `https://ragdemo.win`（tailnet http 登入無解，
  Tailscale 免費版不給 TLS cert）。
- 分工不變：dev 畫面在哪台開都行（各自機 opencode 管各自 dev server），
  **資料主源仍 x570**；`ragdemo.win` 仍由 GitHub Pages 自動部署。

### 2.7 WSL 開機自動啟動 api（2026-09-22 已設定）

> **2026-09-26 三機一致性調整**：啟動腳本原在 repo 內（`backend/start-msi.sh`），因屬
> **MSI 單機專屬**啟動機制（與 x570/mbp 的 docker compose 架構不一致），已**移出 repo** 到
> MSI 本機 `~/bin/ragdemo-api.sh` —— repo 只留三機共用的 compose 架構，單機差異留在該機。
> 這是「Docker Desktop 啟用 WSL integration 之前」的過渡手段，見 §4.1.1。

- 啟動腳本：`~/bin/ragdemo-api.sh`（冪等：8000 已有服務就跳過；`setsid nohup` 脫離 session；
  log 寫 `backend/uvicorn.log`、pid 寫 `backend/.uvicorn.pid`）。手動啟動：
  `bash ~/bin/ragdemo-api.sh`
- 1.19.1 起還會一併啟動**本機 qdrant 備援**（`~/qdrant/qdrant`，port 6333，log
  `~/qdrant/qdrant.log`）—— x570 離線時 MSI 的自立資料層，見 §4.1。
- Windows 開機啟動：`solog` 的「啟動」資料夾放 `ragdemo-msi-api.vbs`（隱藏視窗執行
  `wsl.exe -d Ubuntu -u solo -e bash /home/solo/bin/ragdemo-api.sh`），
  比照 Ollama.lnk 的作法；**只在 solog 登入時跑**。
- 注意：WSL distro 名寫死 `Ubuntu`；若新機 clone 到別處，腳本可用 `RAGDEMO_BACKEND=<backend 路徑>`
  覆寫（預設 `/home/solo/projects/ragdemo.win/backend`），並同步改 VBS 與腳本內 `BASE`。
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

## 3.5 LLM 供應商路由架構（2026-09-26 現況）

前端下拉選單的「使用 LLM model」提供 8 個 provider 群組＋地端 ollama。後端 `backend/app/rag.py`
`generate()` 依 `model` 前綴路由（`ollama` 為預設本機路線）：

| provider | 前綴 | 認證 | 通道 | 額度（free，來源 mnfst/awesome-free-llm-apis） |
|---|---|---|---|---|
| OpenRouter 閉源 | `openrouter/` | CF token（gateway 代管 key） | CF AI Gateway | 50 次/天/模型，20 RPM |
| OpenCode Zen | `zen/` | `ZEN_API_KEY` | zen 直連 | 未公布（None） |
| NVIDIA NIM | `nv/`（避開 nvidia/ org 碰撞） | `NVIDIA_API_KEY` | integrate.api.nvidia.com 直連 | 10,000 次/天 |
| Google Gemini | `gemini/` | CF token（gateway 代管 key） | CF gateway google-ai-studio | 1,500 次/天 |
| Groq | `groq/` | CF token（gateway 代管 key） | CF gateway groq | 1,000 次/天 |
| Cohere | `cohere/` | CF token（gateway 代管 key） | CF gateway cohere | 1,000 次/月 |
| Hugging Face | `hf/` | `HF_TOKEN` | router.huggingface.co 直連 | credit（None） |
| Mistral | `mis/` | CF token（gateway 代管 key） | CF gateway mistral | credit（None） |

- **gateway 代管**（openrouter/gemini/groq/cohere/mistral）：key 在 CF AI Gateway 後台的
  Provider Keys 新增，本機請求只用 CF token（`cf-aig-authorization`＋`Authorization` 雙 header），
  不需另填 provider key；gateway URL 由 `OPENROUTER_GATEWAY_URL` 尾段 `/openrouter` 換
  `/google-ai-studio`、`/groq/v1`、`/cohere/v1beta`、`/mistral/v1` 推導。
- **直連需 key**（zen/nvidia/hf）：`ZEN_API_KEY`、`NVIDIA_API_KEY`、`HF_TOKEN` 放各機 `.env`（gitignored）。
- 各 provider 路由細節與實測結果見 `settings/opencode/CF-AIG-TOKEN-ENV.md`、本檔 §3 常見坑；
  模型清單（env 預設字串）集中在 `rag.py` constants。Mistral 的 `mistral-small/medium` 家族實測
  429（code 1300，Mistral 端限額）故未列入；可用 `ministral-8b-latest`、`codestral-latest`。

### 用量統計 / 限流標記 / 額度分數（2026-09-26 commit 65c5cb4/183bfad/664a511）

- **用法：`backend/app/usage.py`**（PG 表 `model_usage(provider, model, day, calls, tokens)`），
  各 `_*_complete` 成功後提取用量記一筆（openai-compatible `usage.total_tokens`、
  gemini `usageMetadata.totalTokenCount`、ollama `prompt_eval_count+eval_count`；
  cohere 不回 usage → tokens 記 0）；寫失敗吞掉不影響 query。
- **限流：`rag._rstatus()`** 取代各 LLM completion 的 `r.raise_for_status()`——
  收到 429 記入 `_LIMITED`（重置時間依 Retry-After／X-RateLimit-Reset／req-minute header 推估，
  皆無 +1h），成功呼叫自動清除；`/models` 附 `limited`。
- **額度分數：`rag.FREE_QUOTA`** 對照表（見上表額度欄，均為「次數型」——該清單對我們用的
  provider 無 token 型額度公布）；`/models` 附 `usage`+`limited`+`quota`。
- **前端（`frontend/src/routes/+page.svelte`）**：原生 `<select>` 已改**自製下拉**
  （`.model-drop`，原生 option 無法對內部子字串著色／右對齊）；每列 flex 左 model 名、
  右側用量；有 quota 的顯示 `calls/limit` 分數，`calls≥limit` 或 429 時轉紅＋重置註記
  （429 用實際 until 的 `HH:MM`；純超額 day 型 `重置 00:00`、month 型 `重置 M/1`）；
  無 quota（hf/mistral/zen/ollama）維持舊「今日 N次/Tk」。
- `/models` 驗證（x570 實測 2026-09-26）：`quota`、`usage`（mistral 2003 tokens、
  cohere calls 記到）、`limited`（觸發 429 後出現 `mistral/mistral-small-latest`）齊備。

---

## 4. 下一步工作（Step 2+，未啟動）

### 4.1 精簡包 snapshot/restore（讓 mbp/msi 無 x570 也能跑）
> **2026-09-22 進度**：MSI、mbp 本機 qdrant 備援＋自動同步皆已完成 ✅。剩餘：擴資料到 500~1000 筆。

**已建立（MSI）**：
- MSI WSL 本機 qdrant 1.19.1（`~/qdrant/qdrant`，port 6333），`~/bin/ragdemo-api.sh` 啟動時一併拉起。
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
- 前端：Node v22.23.2（brew `node@22`，PATH 已寫 `~/.zshrc`），`pnpm install`＋`pnpm run build` 過。

> **2026-09-26 資安收斂後接手須知（x570 已設定，mbp/msi 需補）**：
> x570 qdrant 已啟用原生 API key（`QDRANT__SERVICE__API_KEY`，無 key 回 401）＋只綁 tailscale
> `100.119.83.111:6333`（`${TS_IP}`）；postgres 綁 `${TS_IP}:5432`（mbp/msi 經 tailscale 連共享
> registry，見 §4.1.1）；api 僅綁 `127.0.0.1`。mbp/msi pull 後需同步：
> 1. 三台 `.env` 都要有 **`TS_IP`**（各台 tailscale IP）＋**`QDRANT_API_KEY`**（與 x570 `.env` 同一值），
>    否則 qdrant 回 401、`scripts/sync-snapshot.sh` 拉不到快照（script 已支援 `QDRANT_API_KEY`
>    env，沒設就無 key 向後相容）。
> 2. 自動同步的排程（msi crontab／mbp launchd `com.ragdemo.sync-snapshot`）環境要帶上
>    `QDRANT_API_KEY`（crontab：指令前 `QDRANT_API_KEY=xxx`；launchd 用 `EnvironmentVariables`）。
> 3. x570 compose 的 `POSTGRES_PASSWORD` 已改 `:?` 必填（移除了 `changeme` fallback）——mbp/msi
>    的 `.env` 同時補 `POSTGRES_PASSWORD`（共享 x570 registry，三台同一值，`POSTGRES_DSN` 用）。
> 4. **坑：`POSTGRES_PASSWORD` 只對首次容器初始化生效**。x570 的 postgres volume 是 `changeme`
>    時代建的，改 compose 後要手動 `ALTER USER rag PASSWORD '<新值>'`（用 `docker exec`）──
>    否則容器外（tailscale 來源）連線會 `password authentication failed`（127.0.0.1 trust 免密碼
>    會誤導成「密碼正確」）。mbp/msi 若有沿用舊 volume 同理。
> 5. 本機 dev 直接跑 `rag.py`（不經 compose）時，`.env` 也要設一致 `QDRANT_API_KEY`，否則 401。
> 6. **mbp 已於 2026-09-26 完成 §4.1.1 容器化（OrbStack runtime）**：compose qdrant 綁
>    `100.64.121.9:6333`，sync-snapshot 目標隨之指向 `TS_IP`；api/qdrant/pg 不再用 launchd。

### 4.1.1 二機全容器化對齊 x570（2026-09-26）

**目標**：mbp/msi 捨棄 launchd／`~/bin/ragdemo-api.sh`／native qdrant，改用與 x570 相同的 **docker compose
一鍵三容器**（qdrant＋postgres＋api），密鑰統一由各機 repo 根 `.env` 讀入。

**x570 已實作（本節依據）**：
- `compose.yaml` 三容器；qdrant/postgres 綁 `${TS_IP}:6333/5432`（阻 LAN/公網、tailscale 互通），
  api 綁 `127.0.0.1:8000`（cloudflared 本機連）；compose 自動讀 repo 根 `.env`。
- `rag.py` `_req(kind="qdrant")` 自動帶 `QDRANT_API_KEY` header；`sync-snapshot.sh` 支援
  `QDRANT_API_KEY` env（出站認證）。
- postgres `healthz` 心跳經 tailscale 寫入 x570 backends（msi 曾是唯一在寫的舊筆記，x570 已恢復）。

**mbp 已執行（2026-09-26，OrbStack 當 docker runtime）／msi 待執行**：
1. `docker compose up -d`（mbp＝OrbStack，`~/.orbstack/bin`；msi＝WSL Docker Engine；pgvector/qdrant 皆 multi-arch）。
2. `.env` 補：`TS_IP`（本機 tailscale IP）、`POSTGRES_PASSWORD`＋`QDRANT_API_KEY`＝與 x570 同值。
   自己的 postgres volume 若是舊的，先做一次 `ALTER USER` 同步密碼（見上坑 4）。✅ mbp（fresh volume）
3. 停用舊啟動機制避免搶 port：
   - mbp：✅ `launchctl unload` `com.ragdemo.qdrant`／`com.ragdemo.api`（native `~/qdrant/qdrant`＋uvicorn）。
   - msi：停用 startup `ragdemo-msi-api.vbs`＋`@reboot` crontab；`~/bin/ragdemo-api.sh` 不再使用
     （已於 2026-09-26 移出 repo，見 §2.7）。
4. registry 仍共享 x570：**api 容器的 `POSTGRES_DSN` 指向 x570 tailscale 位址**（覆寫
   `POSTGRES_DSN=postgresql://rag:<pw>@100.119.83.111:5432/ragdemo`），不連本機空庫，三台 backends 合一。
   ✅ mbp：`compose.yaml` 改 `${POSTGRES_DSN:-本機 postgres}`，`.env` 帶 x570 DSN；同文件 x570 不受影響。
5. sync-snapshot 排程保留（備援資料層＝本機 compose 的 qdrant 容器），帶 `QDRANT_API_KEY`；
   `DEST` 指本機 qdrant（compose 綁 `${TS_IP}:6333`，非 127.0.0.1）。✅ mbp：script 預設
   `http://${TS_IP:-127.0.0.1}:6333`＋launchd `EnvironmentVariables` 加 `TS_IP=100.64.121.9`＋已 reload。
6. 驗收：`docker compose ps` 三容器 up；`curl http://<TS_IP>:8000/health`；`/query` 作答；
   x570 離線時本機備援仍可答（沿用 §4.1 驗證法）。✅ mbp：`/health` host_id=mbp、
   `/query` src=mbp（conf=high）、usage 寫回共享 registry。**mbp 已完成，msi 沿用本清單。**

#### ✅ MSI 阻塞點已解除：Docker Desktop 的 WSL integration 已開（2026-09-26）

原先的診斷是「MSI 的 WSL 內沒有 `docker` 指令」。**啟用 integration 後該敘述已不適用**：
WSL integration 會把 Linux 版 CLI 與 socket 一起掛進 distro。

```
$ ls -l /var/run/docker.sock
srw-rw---- 1 root docker 0 /run/docker.sock
$ wsl.exe -l -v
  Ubuntu          Running  2
  docker-desktop  Running  2      ← engine 住在這裡，不是獨立的 WSL daemon
$ docker version
  Client: 29.8.0 / Server: Docker Desktop 4.92.0 (Engine 29.8.0, containerd 2.3.5)
  Storage Driver: overlayfs   Cgroup: cgroupfs v2   32 CPU / 15.21 GiB
```

`docker.sock` 是 `root:docker 0660`，所以 `solo` 必須在 `docker` 群組（已在）。
判斷「能不能用」要看 **socket + Server 段**，不是看 `docker` 指令在不在。

##### 釘死的 image tag 首次在 MSI 實測（此前僅 mbp／x570 驗過）

```
qdrant/qdrant:v1.19.1        ✅ docker.io/qdrant/qdrant@sha256:12364fe851b9f1735…  linux/amd64
pgvector/pgvector:0.8.6-pg16 ✅ docker.io/pgvector/pgvector@sha256:ccc6e83d6e35e931…  linux/amd64
```

`${TS_IP}` 綁定在 MSI 可用 —— Docker Desktop 的 port proxy 接受 `100.65.68.106`，
從 WSL 經該 IP 實測通（`GET /` 回 `version 1.19.1`）；走 `127.0.0.1` **反而不通**，
證明綁定是 IP 專屬的（這是想要的性質：阻斷 loopback 與 LAN）。
⚠️ WSL 內**沒有 tailscale 介面**（只有 `lo` 與 `eth0 172.18.125.39`），tailscale 跑在
Windows host 上；`TS_IP` 仍要填 host 的 `100.65.68.106`。

##### `laws` 資料搬遷：39,879 筆，用 snapshot 無損還原

原生 qdrant 與釘死的 tag **同為 1.19.1**，所以 snapshot 沒有相容性風險。

| 步驟 | 做法 |
|---|---|
| 建 | `POST /collections/laws/snapshots?wait=true`（原生） |
| 取 | `GET /collections/laws/snapshots/<name>` → 301,166,080 B（POSIX tar） |
| 放 | `docker cp` 進容器 `/qdrant/snapshots/import.snapshot` |
| 還 | `PUT /collections/laws/snapshots/recover`，body `{"location":"file:///…","priority":"snapshot"}` |

⚠️ **三個容易踩的坑**：

1. **路由是 `recover` 不是 `upload`**。`upload` 是 **shard 層級**的
   （`/collections/{c}/shards/{s}/snapshots/upload`）；collection 層級在 1.19 是 `recover`。
   打 `upload` 會得到 404，而且 `openapi.json` 在 release build **不提供**（兩台都 404），
   只能從執行檔挖字串或靠 400/404 差異辨別。
2. **`recover` 的 body 是 JSON 不是二進位**，且 JSON 上限 32 MiB
   （`"JSON payload (301166080 bytes) is larger than allowed"`）—— 287 MB 必須先
   `docker cp` 進容器再用 `file://`，不能直接 POST。
3. **目標 collection 要先存在**，因為 router 是按 collection 註冊的
   （不存在時上傳直接 404、`size_upload: 0`）。

**無損驗證**（不只是比筆數）：取原生任一點的向量當查詢，兩邊同查 —— id 順序完全相同、
分數差 < 1e-6、自匹配 `score=1.000000`、payload 欄位一致。還原後 `status=green`、
`optimizer_status=ok`、`indexed_vectors_count=39879`、8 segments、config 與原生相同。
且撐過容器 `--force-recreate`（volume 持久化有效）。

##### ⚠️ 已知非問題：勞動基準法第39條不在 corpus

過往拿「勞動基準法第39條」當 MSI 的驗證題，但 `law_name=勞動基準法 AND article_no=39`
在 corpus 裡是 **0 筆**（勞動基準法本身有，第39條沒有）。所以 `/query` 回 `no_match` 是
**正確行為**，不是檢索壞掉。改用 corpus 內確實存在的條文驗證：問民法第184條 →
`no_match=false`、`confidence=medium`、`relevance=cos@0.62`、**第184條排第一**（191、191-3
條隨後），回答引用真實條文。

##### 🔴 順帶修掉兩個真 bug（都不是容器化造成的）

1. **`rag.py` `/points/search` 的具名向量格式錯誤**：`{"vector": {"dense": vec}}` 會被
   qdrant 400（`did not match any variant of untagged enum NamedVectorStruct`）。
   `/points/search` 要的是 `{"name": "dense", "vector": vec}`；`{"dense": …}` 是
   `/points/upsert` 與 `/points/query`+`using` 的形式。MSI 走得到這條分支是因為
   `HAS_SPARSE=False` 且 `_HAS_NAMED=True`（collection 有 dense 具名向量）。
2. **`main.py` 把所有上游錯誤標成「LLM 上游」**：`except httpx.HTTPStatusError` 會同時
   捕獲 qdrant／ollama／gateway，於是 qdrant 的 400 被顯示成「LLM 故障」。
   這個誤導讓診斷多繞了一圈 —— 改為回報實際請求的 host。

##### MSI 現況：三容器 up，舊原生程序已收掉

```
ragdemo-qdrant-1     Up   100.65.68.106:6333->6333/tcp
ragdemo-postgres-1  Up   100.65.68.106:5432->5432/tcp
ragdemo-api-1       Up   127.0.0.1:8000->8000/tcp
```

已移除：crontab 的 `ragdemo-api.sh` loop、`~/bin/ragdemo-api.sh`、原生 qdrant 行程
（pid 293 已停，資料已驗證等於容器內的副本）。crontab 保留 cloudflared tunnel 與
snapshot 同步，同步的 `DEST` 已由 `http://127.0.0.1:6333` 改為 `http://100.65.68.106:6333`。
MSI 另建了 root `.env`（`chmod 600`，`.gitignore:9` 已忽略，不進版控）。

##### 🔴 x570 的資料服務目前是離線的（與 MSI 容器化無關）

`100.119.83.111` 在線（ollama `11434` 通），但 **`5432` 與 `6333` 都不通** —— postgres 與
qdrant 沒跑。連帶影響：

- MSI `POSTGRES_DSN` 指向 x570，`/hosts` 回空（共享 registry 無法讀）。
- snapshot 同步 cron **每 10 分鐘記一次 skip**（`~/qdrant/sync.log`），
  MSI 的備援資料因此不會更新。
- MSI 的 `/health` 回 `ok: true` **掩蓋了這件事** —— 它只檢查本地 qdrant 與 LLM，
  不檢查 registry。health 檢查項目不足，是後續可補的監測缺口。

**mbp 容器的連線筆記（OrbStack 特有）**：容器內 `host.orbstack.internal` 不解析（2026-09-26
實測 DNS 失敗）；mbp ollama 綁 `*:11434`，容器直接連本機 **tailscale `100.64.121.9:11434`**
即可（`OLLAMA_URLS` 首項設此）。

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
> 前置：該機已有 git repo、tailscale 在線、8000 後端可跑（mbp launchd／msi ragdemo-api.sh）。
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
> - mbp 已實作並驗證（commit `mbp:`）；x570 / msi pull 後 `launchctl kickstart -k gui/$(id -u)/com.ragdemo.api`／`~/bin/ragdemo-api.sh` 重啟即可。
>   **mbp 自 2026-09-26 改用 docker compose（OrbStack），api 無 launchd，改用 `docker compose up -d --build api` 重啟。**

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
    > ⚠️ **已被 2026-09-26 的 §4.1.1 容器化取代**（此段保留為 2026-09-23 的決策紀錄）：
    > msi 的 `~/bin/ragdemo-api.sh` 與原生 uvicorn/qdrant 已移除，現為三容器 compose。
    > 換機後請改用 `docker compose up -d --build`（見 ARCHITECTURE「啟動」節的 MSI 段）。
  - `git pull origin main`；msi 先 `pkill -f 'uvicorn.*8000'` 再 `bash ~/bin/ragdemo-api.sh`
    （ragdemo-api.sh 偵到 8000 在跑會跳過，不重拉新碼）；mbp 用 docker compose 重啟
    （`docker compose up -d --build api`）。
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

**特殊狀況：關機／重開機 —— 不要「先 docker stop 再關機」**
- 現象（2026-09-26）：x570 關機後開機，主機在線（ollama 11434 還通）但
  **postgres(5432) 與 qdrant(6333) 不通**，連帶讓 msi 的 `/hosts` 回空、
  快照同步每 10 分鐘記一次 skip。
- ❌ **不要用這種「安全關機」別名**：
  ```bash
  # 危險：會讓開機自動恢復失效
  alias sdown='sudo docker stop $(sudo docker ps -q); sync; sync; sudo shutdown now'
  ```
  官方文件對 `unless-stopped` 的定義是：「Similar to `always`, except that when the
  container is stopped (**manually or otherwise**), it isn't restarted **even after Docker
  daemon restarts**」。三機的 api/qdrant/pg **全部**是 `unless-stopped`，
  所以手動 `docker stop` 過的容器，**開機後不會自啟**，必須手動 `docker compose up -d`
  —— 等於把 §4.1.1 驗過的開機自動恢復作廢。
  （旁證：2026-09-26 MSI 重開機後 `ragdemo-api-1` 是 `exited` 而 qdrant/pg 是 `running`，
  正是「曾被手動停過」的樣子。）
- ✅ **正確做法：`sudo systemctl poweroff`，不必做任何前置動作**
  | 別名前置動作 | 為何不必要 |
  |---|---|
  | `docker stop` | dockerd 收到 SIGTERM 自己會依序停容器（預設每個 10s 逾時）。手動停反而**關掉自動恢復**。 |
  | `sync && sync` | 三台都是 ext4（journaling fs），`sync` 早已是 no-op。 |
  | 換成 `poweroff` | systemd 上 `/usr/sbin/shutdown` **就是** `systemctl` 的 symlink（實測 `-> ../bin/systemctl`），`shutdown now`／`poweroff`／`halt` 行為完全一致。 |
- 順帶把 `docker update --restart unless-stopped $(docker ps -q)` 記下來：若真的手動停過容器，
  這行可讓它們重新納入自動恢復管轄。
- **若已發生「開機後容器沒起來」**：
  ```bash
  docker compose up -d            # 三容器（會保留 named volume，不會清資料）
  bash ~/bin/ragdemo-boot-check   # 逐項驗證
  ```
- ⚠️ **2026-09-26 x570 事件的根因尚未證實**。曾懷疑是關機前處於 suspend（S3）、
  喚醒時從記憶體映像恢復，但 `systemd-analyze cat-config systemd/logind.conf` 顯示
  `IdleAction` 未設定（預設 `ignore`，不會自動睡眠）、`HandleLidSwitch` 預設 `suspend`
  只在合上上蓋時觸發（x570 是桌機）—— **該假設不成立，已撤回**。
  要查真相請在 **x570 上**跑：
  ```bash
  systemctl is-system-running; systemctl status docker --no-pager
  docker ps -a --format '{{.Names}}\t{{.Status}}'   # 看是否「曾被手動停止」
  journalctl -b -u docker --no-pager | tail -40      # 開機時 daemon 狀況
  journalctl --list-boots | head -5                  # 確認上次是否真的完整關機
  ```

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
- 修復（2026-09-23 已做）：msi crontab 新增 `restart loop` 包 `~/bin/ragdemo-api.sh`
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

**特殊狀況：裝 zsh 後終端字變暗變細（2026-09-25）**
- 現象：切到 zsh 後，輸入時自動補建的文字呈**淡灰暗色**，整行看起來「暗、細」。
- 原因：`zsh-autosuggestions` 預設樣式為 `ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=8'`
  （16 色板中的「亮黑」＝淡灰色），補建文字就以淡灰浮現。
- 判定：`echo $ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE` 回 `fg=8` 即為此因。
- 修復：在 `~/.zshrc` 載入 autosuggestions **之前**加上：
  ```bash
  export ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=cyan'   # 或 yellow/magenta/white,bold
  [ -f /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh ] && \
    source /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh
  ```
  放在 source **前**才能讓該檔的 guard `(( ! ${+ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE} ))` 跳過其預設值。
- 驗證：開新終端，補建文字改為設定色即完成；喜歡其他色直接換 `cyan`→`yellow`/`magenta`/`white,bold`。
- 教訓：**zsh 自動補建「變暗」不是字型問題，是預設 `fg=8` 淡灰樣式**；優先查 `ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE`。

**特殊狀況：opencode /models 看不到 openrouter free 模型／呼叫 401／429（2026-09-25，三台適用）**
- 現象：全球設定走 CF gateway（`~/.config/opencode/opencode.json`）後，`/models` 找不到想用的
  openrouter free 模型；叫用報 `OpenRouter API key is missing`、`Missing Authentication header`，
  或認證通過後的 `Rate limit exceeded: free-models-per-day`（429）。
- 原因（依序排除三層）：
  1. **config 寫法**：頂層必須是 `provider`（單數）＋選項在 `options` 內（`baseURL`/`apiKey`/`headers`）；
     寫 `providers`（複數）或 `settings`/provider 下直接 `headers` 會被 opencode **靜默忽略**，
     以為有設定其實打上游 api.openrouter.ai（未認證→401）。
  2. **apiKey 必須是真 gateway token**：內建 `openrouter` 的 SDK 強制要有 `options.apiKey`
     （缺→ `OpenRouter API key is missing`；給 dummy `sk-or-...`→ gateway 視為不合法值回
     `Missing Authentication header`；給真 OpenRouter `sk-or-...`→ `401/2009 Unauthorized`；
     給遮蔽/截斷值→ 401 code 2009）。值＝**完整 CF gateway token**
     （`{file:~/.config/opencode/cf-aig-token}`），gateway 收標準 `Authorization` 即可
     （`cf-aig-authorization` header 非必要、但照 msi 風格同時帶亦無害）。
  3. **429 = 額度非故障**：free 額度是 OpenRouter 帳號共享池 **50 次/天（三台合用）**，用罄即 429；
     每日 00:00Z 重置；加 $10 credits → 1000/天，或 CF AI Gateway → Provider Keys 加自己的 OpenRouter key（BYOK）。
- 修復（global config 範例，三台一致）：
  ```json
  { "provider": { "openrouter": {
      "options": {
        "baseURL": "https://gateway.ai.cloudflare.com/v1/<ACCOUNT_ID>/<GATEWAY_ID>/openrouter",
        "apiKey": "{file:~/.config/opencode/cf-aig-token}",
        "headers": { "cf-aig-authorization": "Bearer {file:~/.config/opencode/cf-aig-token}" }
      } } } }
  ```
- 排查：`opencode models --provider openrouter` 看模型清單；`opencode run "回覆:OK" --model openrouter/<model>:free`
  看錯誤碼（401=設定/token，429=額度）。
- 教訓：**「模型沒顯示／呼叫失敗」先分三層（config 合法寫法→真 token→額度）**；401 屬設定、429 屬資源，
  勿把額度打擊誤判成設定錯誤。設定檔本體：`settings/opencode/global/<機名>/opencode.json`、專案 `opencode.json`。

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
- duckdb 筆記：一律用根 `pyproject.toml`（uv 為主）——`cd ~/ragdemo && uv sync` 即得 `.venv`
  （含 duckdb/asyncpg/httpx/numpy/pandas）；清華鏡像加速僅備援（`uv pip install -i https://pypi.tuna.tsinghua.edu.cn/simple ...`）。
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
  前端 `pnpm run build`＋svelte-check 0 error、`/eval` 14/14=1.0。
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

### 4.10 單元測試（pytest，2026-09-24 上線）
- 策略＝「**重點 TDD**」：純函式核心（law_struct 項/款、sparse tokenizer＋u32 索引、rag `_ref/_hit_view`
  渲染、normalize 清洗、chunk_text 切塊、sync_daily 版本判定/縮水防呆/state 持久化/zip 解析）全部可測，
  不碰網路/docker/LLM；RAG 整合回歸仍以 `/eval`（hit_rate=1.0）＋ pre-push HTTP smoke 把守。
- 環境：根 `pyproject.toml` 新增 `[dependency-groups] dev=["pytest>=8"]`，`uv sync --dev` 裝進 `.venv`；
  pre-push hook 新增強制步驟 `3)`——`tests/` 37 個測試全過才放行，機器未裝 pytest 則跳過（不擋 push）。
- 依測試補上的 robust/一致性修正：① `normalize.law_articles` 的 `pcode` 若未預置 `_pcode`
  改自己算（原先靜默 None）；② `rag._ref` 引用標示的 `article_no` 先 strip（消除雙空白）；
  ③ `sync_daily` 把版本比對與縮水判斷抽成純函式 `version_changed`/`shrink_guard` 供測試。
- 回歸實測：`uv run --frozen pytest -q` → **37 passed in 0.08s**（加上 §4.11 引擎閘門測試現共 44）。

### 4.11 檢索→生成閘門（低相關/不明語意不問 LLM，2026-09-24 上線）
- 目標：使用者搜尋後「快速有效解析語意找到最適合的法案」；**不明語意直接說找不到，低相關直接回
  「沒有符合比對的法條」，不作過多猜測**。
- 校準量測（本機 x570 qdrant，qwen/bge 現行資料）：正題 top dense 餘弦 **0.64–0.76**、
  無關語意題（天氣/煮咖哩/訂機票/改作文）**0.43–0.57** → 切得開。門檻收 env：
  `RAG_MIN_DENSE=0.58`、`RAG_MID_DENSE=0.62`、`RAG_HIGH_DENSE=0.70`。
- **召回改「兩腿分開查＋本端 DBSF 融合」（決定性）**：偵測到 Qdrant 伺服端 `fusion:dbsf` 對同 query
  回不同 id/分數尺度的非決定性，改 dense(bge-m3)+sparse(TF) 各 prefetch 500 分開查、min-max 正規化
  加總融合（`_fusion_sort`）；順帶保證每個 hit 都拿得到真實 dense 餘弦（原「伺服端融合」拿不到）。
- **本地 rerank**（`rerank`）：條號精準分支（`_exact_rank`，scroll＋sparse dot＋法名 bigram）保持領先，
  其餘依「真實 dense 語意相似度」降序（pool 每筆都有 dense，稀疏僅主導的噪音沉底）。
- **引用顯示「語意相似度 %」**：`/query` hits 新增 `rel`（dense 餘弦×100，整數%）與 `exact` 旗標；
  前端引用列顯示 `73%`（精準分支顯示「精準」）——原 `score.toFixed(2)` 是融合分數(0–2)/精準分數（不同
  尺度），直接加 `%` 會誤導，故改以 0–100 的語意相似度呈現。
- **信心分級 `_decide`（不問 LLM 的閘門）**：
  - `dense_max < 0.58` → **no_match** 直回「沒有符合比對的法條」（~1.3–1.6s，不問 LLM）；
    但「條號精準命中」例外放行 medium（破熱門條號被擁擠的正確答案不誤殺）。
  - 無法律語意訊號（`_legal_signal`：條號/法律語彙）且 `dense_max < 0.62` → no_match（不明語意不猜）。
  - `>=0.70` → high；其餘 → medium（生成時附「不確定就明說」guard）。
- SYSTEM 補強：「若資料與問題無關或僅模糊相關，直接回「沒有符合比對的法條」，不要編造/臆測」。
- `/query` 新增回傳欄位：`no_match`(bool)、`confidence`(high/medium/no_match)、`relevance`(原因)；前端
  no_match 時顯示專屬狀態（不渲染低相關引用）。
- 評測題庫 `evals/questions.json` 補 **5 道負面題**（`expect_none`），`/eval` 回報 `neg_rate`：
  實測 **hit_rate=14/14=1.000、neg_rate=5/5=1.000**（負面全 no_match，正面全正確）。
- 測試：`tests/test_rag_engine.py`（rerank/_decide/_legal_signal/answer 閘門，44 tests 全過）。
- 修補（msi，2026-09-24）：法名＋條號（含簡稱「勞基法第38條」）改**先滾「該法該條」當精準 top1**。
  原「跨法同條號」競爭下實測兩個缺口：① sparse tokenizer 的 CJK run 先吃掉「第」（法名 bigram
  吸收），數字被 latin 拆出 → query 根本缺「第N條」條號 token；② doc 端 `min(tf,4)` 飽和讓罰責類
  條文的「規定/條規」拿 3~4 權重，與 query「規定什麼」假重疊灌分（「勞基法第38條」top1 曾命中
  事業用爆炸物管理條例第38條，正確條文掉到 top5）；簡稱對法名 bigram 重疊=0 使加分失效。
  僅條號查詢（無法名）維持跨法競爭不變。`/eval` 19 題 hit_rate=1.0 維持。

### 4.12 法名/簡稱查詢分支（2026-09-24 上線）
- 問題：裸法名（例:「證券交易法」）dense 前段被「提及該法名」的其他法條文佔據，本法條文進不
  了 top（原本回 medium＋cautious guard 誤導 LLM 回「沒有符合比對的法條」）。
- 法名分支：啟動時 scroll 全量 payload（只取 law_name，1025 部法 0.3s）建清單＋各法條文數；
  無條號查詢滾出該法條文（條號升序）prepend 當來源。修了 u64 接近上限的點使 scroll 不前進的
  死迴圈。
- 簡稱對照 `_LAW_ALIASES`（證交法/證交稅/勞基法/消保法/個資法/民訴/刑訴/行訴/道交條例/
  遺贈稅法/強執法）；`_detect_law` 解析順序：簡稱→法名精準→法名開頭→包含→嵌入問句。
- 信心：法名命中**永不 no_match**（`law_name@` → high，即使 dense 偏低）。
- 答案＝**基本敘述**（`_law_brief`：《法名》（共N條）＋ LLM 一到三句概括，禁止引用第1條）；
  LLM 掛了 fallback 基本敘述，保證有回應。jud 對法名命中顯示「法名｜《…》」，前端標「簡介」徽章。
- pytest：`LAW_QUERIES` 29 組法名/簡稱表，驗證 detect、永不 no_match、基本敘述不含第1條
  （56 tests 全過）；`/eval` hit_rate 14/14、neg 5/5 維持。

### 4.13 GitHub 自動化：三項待辦（2026-09-26 記錄，尚未做）

2026-09-26 補上 `.github/`（CI＋Dependabot）、釘版、ruleset 後，剩下三件要人或要決策的事。
背景見 `ARCHITECTURE.md`〈三機紀律的 server 端執行〉與〈依賴與映像版號策略〉。

#### 待辦 1：GitHub MCP Server 授權 ✅ **已完成（2026-09-26，MSI）**

⚠️ **原以為走 OAuth，實測不可行**：`/mcps` 按 sign in 會得到
`Incompatible auth server: does not support dynamic client registration`。
opencode 的 OAuth 需要 dynamic client registration（RFC 7591，讓 client 自己註冊取得
`client_id`），**GitHub 遠端 MCP 不支援**，它要求事先建好 GitHub App / OAuth App。

**已改用 PAT 認證並實測通過**：
- `~/.config/opencode/gh-token`（`chmod 600`）存 token，config 只寫
  `"Authorization": "Bearer {file:/home/solo/.config/opencode/gh-token}"` —— 與既有的
  `cf-aig-token` 同一套做法，備份不含明碼密鑰（repo 是公開的，這點必須如此）。
- **另一個關鍵修正**：不設 `X-MCP-Toolsets` 時 server 只給預設 5 個 toolset
  （`context,repos,issues,pull_requests,users`）＝ 45 個工具，**Dependabot alerts、
  secret scanning alerts、code scanning、Actions 執行結果、ruleset 全部沒有** ——
  等於接了白接。加上 `X-MCP-Toolsets` 後 45 → **57 個工具**，缺的都補回來。
  無效 toolset 名稱會被**靜默忽略不報錯**，改完要實際驗工具數。
- 實測結果：`get_me` → `solo0920`；Dependabot alerts 0、secret scanning alerts 0；
  4 個 Dependabot PR 可讀；ruleset `main-禁刪分支` 可讀。
  （`list_code_scanning_alerts` 回 404 `no analysis found` —— 這個 repo 沒啟用 code scanning，正常。）

**⚠️ 憑證治理：原本沿用 `gh` 的 token 是錯的決策，已改正**

一開始圖省事直接用 `gh auth token` 填進 `gh-token`（實測 GitHub 遠端 MCP 確實接受）。
但那是**過度授權 ＋ 憑證共用**：`gh` 的 scope 是 `repo`（涵蓋帳號下所有 repo）、
`workflow`、`gist`，而且兩個工具共用同一份命脈。已改為：

| | opencode | `gh` CLI |
|---|---|---|
| 類型 | classic PAT | OAuth token |
| scope | **只有 `public_repo`** | `gist, read:org, repo, workflow` |
| 理由 | repo 是 public、帳號 0 個 private repo，已足夠 | `workflow` 必要：沒有它 push 改 `ci.yml` 會被拒 |

`public_repo` 夠用是**逐端點查過官方文件**的，不是推測：Dependabot alerts 端點明寫
「若只用於 public repository 可用 `public_repo`」；Actions runs「anyone with read access
即可」；rulesets「僅請求 public 資源時可無認證使用」。實測七個端點全 `200`，
`x-oauth-scopes` 回應標頭確認 PAT 只拿到 `public_repo`。

**沒選 fine-grained 的理由**：Dependabot alerts 端點的文件完全沒提 fine-grained PAT，
建立前無法確認相容性；`public_repo` 有明文依據。日後要換必須重跑能力檢查。

**已知取捨**：PAT 沒有 `workflow` scope，所以改寫 workflow 檔的 PR 不能用它的 token 合併 ——
但 `gh` 那枚有，所以 action 類 Dependabot PR 用 `gh pr merge` 合即可。

**新增 per-machine 工具 `~/bin/gh-token-check`**（故意不放進公開 repo）：`--set` 隱藏輸入
token 並立刻列出七個端點狀態碼與「解耦狀態」判定。踩過的坑是 curl 全回 `000` ——
那不是權限問題而是 URL 沒組出來（變數賦值被 `read` 吃掉），所以腳本會擋下明顯不是
token 的輸入。

**撤銷流程的四個事實**（都實測過，細節見 README）：`gh auth logout` **不撤銷** token；
列 grant 的兩個 API 端點已被 GitHub 移除（實測 404）；正確路徑是
Settings → Integrations → Applications → **Authorized OAuth Apps**（不是 Developer settings）；
`Revoke` 會一次撤掉該 app 所有 token。順帶查出一個先前沒在意的授權：
**`Visual Studio Code` 也持有本帳號的 OAuth 授權**。

**mbp / x570 若要加**：同一組 scope 的 PAT 三台機器可共用一枚，但每台機器的
`gh-token` 檔要各自建立、權限各自 `600`，並且該機的 `gh` 必須是**另一枚** token
（`gh-token-check` 的「解耦狀態」就是查這個）。完整寫法、撤銷流程與輪換檢查清單見
`settings/opencode/README.md`〈MCP〉。

#### 待辦 2：合併 Dependabot PR 時，commit 訊息要帶機器前綴 ✅ **已完成（2026-09-26）**

Dependabot 開了四個 PR（#1 asyncpg／#2 fastapi／#3 adapter-auto／#4 pydantic）。
GitHub squash merge **預設拿 PR 標題當 commit message**，而標題形如
`build(deps): update fastapi ...` 沒有 `msi:` 前綴 → ci.yml 的 `護欄` job 會報紅。

```bash
gh pr merge <編號> --squash --subject "msi: bump fastapi floor 0.115 → 0.141.1（對齊 lock 實際解析值）"
```

**實際合併結果**：

| PR | 處置 | 理由 |
|---|---|---|
| #1 asyncpg `>=0.30` → `>=0.31.0` | 合併 | floor 收緊到 lock 早已解析的版本 |
| #2 fastapi `>=0.115` → `>=0.141.1` | 合併 | 同上 |
| #4 pydantic `>=2.0` → `>=2.13.5` | 合併 | 同上 |
| #3 adapter-auto `3.3.1` → `7.0.1` | **關閉並移除依賴** | 見下 |

三個 backend PR 都不是升版，而是**把 floor 收緊到 `uv.lock` 早已解析的版本**
（lock 當時就鎖在 0.31.0／0.141.1／2.13.5）。實測 `uv sync --frozen` 在
pyproject 與 lock 不一致時**仍然 exit 0** —— `--frozen` 明確跳過 lock 新鮮度檢查，
lock 為權威（這正是本專案的設計）。所以這些 PR 合併後安裝的套件集完全不變。
但 `uv lock --check` 會判定過期，因此合併後補了一次 `uv lock`：只改 3 行
`requires-dist` metadata，解析出的版本一個都沒動。

**#3 為什麼關掉而不是合併**：`svelte.config.js` 明確寫死
`adapter: adapter()`（`@sveltejs/adapter-cloudflare`），而 SvelteKit 只在
`kit.adapter` **未設定**時才啟用 `@sveltejs/adapter-auto` —— 該套件結構上不可能生效。
佐證：全 repo 無任何 import、`pnpm run build` 實際輸出 `Using @sveltejs/adapter-cloudflare`。
移除後 lock 淨減 18 行，且 Dependabot 不再會為它開 PR。

順帶一提：commit 前綴**無法**在 server 端用 ruleset 擋（`commit_message_pattern` 只適用
enterprise 擁有的 repo，個人帳號拿不到，實測 422）。所以 `護欄` job 是「事後回報」，
真正擋得住的仍只有各機的 `.githooks/commit-msg`（見 ARCHITECTURE 同節）。

**合併衝突怎麼解**：#1／#2／#4 都改 `backend/pyproject.toml` 的同一個 hunk，先合的
會讓後面的分支 CONFLICTING。`PUT /pulls/{n}/update-branch` **不解衝突**（實測回
`422 merge conflict between base and head`，它只做 fast-forward），要自己 rebase：

```bash
git fetch origin 'refs/pull/4/head:refs/heads/pr4' && git checkout pr4
git -c core.hooksPath=/dev/null rebase origin/main   # replay 的是 Dependabot 的 commit，不是自己的
# 解衝突：整檔取 origin/main 版本再改自己那一行，不要用 regex 砍衝突標記（會留下兩側重複行）
git push --force-with-lease origin pr4:dependabot/pip/backend/pydantic-gte-2.13.5
gh pr merge 4 --squash --subject "msi: ..."
```

`core.hooksPath=/dev/null` 只用在這一次 rebase：`.githooks/commit-msg` 會擋下
Dependabot 沒有機器前綴的訊息，但最終 squash 出來的 commit 仍由 `護欄` job 驗證前綴。

#### 待辦 3：`secret_scanning_non_provider_patterns` 開不了 → 改用 CI 掃 ✅ **已完成（2026-09-26）**

個人帳號的公開 repo **無法**開啟 `secret_scanning_non_provider_patterns`
（API 接受請求但狀態維持 disabled，不報錯）。這個功能正是用來掃
**非 provider 標準格式**的 key，而本 repo 正好有在用。2026-09-26 實測
`security_and_analysis` 現況：

```
secret_scanning_push_protection        = enabled   ← 只認 GitHub 合作廠商 pattern 集
secret_scanning_non_provider_patterns  = disabled  ← 自訂 pattern，計畫限制開不了
secret_scanning_validity_checks        = disabled
```

**已實作**：`ci.yml` 的 `護欄` job 新增 step〈追蹤檔案不得含憑證〉，掃**全部被追蹤的檔案**
（不是 `before..after` 範圍 —— 憑證進來會留在樹裡好幾個 commit，後續任何一次 push 都該
重新確認它還沒被清掉），涵蓋四種格式：

| 來源 | 格式 | push protection 涵蓋 |
|---|---|---|
| Cloudflare AI Gateway | `cfut_[A-Za-z0-9_-]{20,}` | ❌ |
| NVIDIA NIM | `nvapi-[A-Za-z0-9_-]{20,}` | ❌ |
| OpenRouter | `sk-or-v1-[A-Za-z0-9_-]{20,}` | ❌ |
| HuggingFace | `hf_[A-Za-z0-9]{30,}` | ❌ |

**驗證過的行為**（用 YAML 還原後的 script 實跑，不只是寫完就推）：

- 乾淨的樹 → `✓ 無憑證格式殘留`、exit 0
- 四種格式各塞一個假 key → 全部命中並報出行號、exit 1
- 誘餌（`cfut_underscore_name`、`hf_short`、`nvapi-x`）→ **不誤報**，exit 0
- 這些字面 pattern **不會匹配到 `ci.yml` 自己**（`[` 不在 `[A-Za-z0-9_-]` 類別裡）
- 長度門檻 20：實測 `{8,}` 也沒誤報，但 20 能擋掉像變數名那樣的巧合命中

命中時的處理建議已寫進 step 的錯誤訊息：**立刻輪換該 key，再清除歷史**（順序重要 ——
先輪換才有意義，key 都還有效的情況下清歷史是白做）。

覆蓋的是 `.env.example`、文件、腳本、workflow 檔這些**常被誤放 key 的地方**。
`.env` 本身 gitignored，所以真正要防的是「有人把 key 抄進範例檔或文件」。

---

## 5. MB 參考：x570 主要指令

```bash
docker compose up -d --build   # 改碼後重建 api
curl localhost:8000/health
curl localhost:8000/hosts
cd ~/ragdemo/frontend && pnpm run dev   # 前端 dev（proxy → localhost:8000）
```
