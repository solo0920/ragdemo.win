# RagDemo 架構（維護用）

> 本檔描述**目前實作**（2026-09-26）。規劃中的跨機遷移見 `ROADMAP.md` §4.1.1。

## 目錄結構

```
compose.yaml       # qdrant + postgres + api（三機共用，x570 已跑 docker compose）
backend/           # FastAPI：/health /ingest /query /eval /rules /models
  app/             #   main.py / rag.py / registry.py / usage.py / rules.py
  start-msi.sh     #   MSI WSL 開機自動啟動（api＋本機 qdrant 備援，未遷移時用）
  .env.example     #   樣板（真實 .env 在 repo 根，不進版控）
frontend/          # SvelteKit：只打 /api/*（Google 登入守護 SENSITIVE 路徑）
  src/routes/api/[...path]/+server.ts   # worker：登入 guard + 三台備援轉發
  src/routes/auth/                        # OAuth2 PKCE 登入
evals/             # 評測題庫（questions.json + README）
ingest/laws/       # 法規資料擷取/切分/落 PG（pg_load.py / pg_schema.sql）
scripts/
  sync-snapshot.sh #   x570→本機 qdrant 快照自動同步（備援核心，支援 QDRANT_API_KEY）
.opencode/agents/  # ingest / backend / frontend / eval 四個子代理
```

## 設計目標（2026-09-22 定案）
**x570 不在線時，mbp 與 MSI 都能獨立作業。**
除硬體與 LLM 模型差異外，三台的**資料與檢索能力盡量一致、各自可獨立運作**。

- 依賴關係：各機自備 ollama（LLM/embedding，硬體差異故模型不同）；
  資料層（qdrant）以「快照同步」維持一致；pg 只有 registry 心跳＋用量統計用（非 query 必需）。
- 降級邏輯走 `QDRANT_URLS`/`OLLAMA_URLS` 候選清單：x570 優先，離線自動切本機。

## 主機命名（2026-09-22 定案，三台嚴格執行）
**三台一律用硬體代號 `x570` / `mbp` / `msi`；`linux` 是作業系統名，禁止拿來當主機名。**

| 代號 | 機器 | OS | tailscale IP |
|---|---|---|---|
| `x570` | solo-X570-I-AORUS-PRO-WIFI | Linux | 100.119.83.111 |
| `mbp` | Lees-MacBook-Pro | macOS | 100.64.121.9 |
| `msi` | MSI（WSL2 Ubuntu） | Windows | 100.65.68.106 |

- 適用範圍：`.env` 的 `HOST_ID`、前端切換按鈕 id、commit 前綴（`msi:`/`mbp:`/`x570:`）、
  `sync-snapshot.sh` 註解、文件（本檔／ROADMAP）——一律寫 `x570`，不寫 `linux`。
- 沿革：早期以 OS 名 `linux` 代稱主力機；2026-09-22 定案改用硬體代號。

## IP 準則（2026-09-22 定案，三台嚴格執行）
**連線與登錄一律只留 tailscale IP（100.64.0.0/10），杜絕位址污染。**

- `TS_IP` 必填；**不使用 LAN_IP**（192.168.x 一律不寫入 .env、不進 registry）。
- `OLLAMA_URLS`/`QDRANT_URLS`/`POSTGRES_DSN` 全部用 tailscale 位址；
  唯一例外是**本機服務**可寫 `127.0.0.1`。
- 強制執行點：`registry.py _ips()` 過濾非 100.64.0.0/10 → `/hosts` 只會看到 tailscale。

## 提交準則（2026-09-22 定案，三台嚴格執行）
**commit message 首行必須以 tailscale 機器名前綴開頭：`msi:` / `mbp:` / `x570:`。**

- `git log --oneline` 一眼可讀；`.githooks/commit-msg` 強制（Merge/Revert 自動放行）。
- 各機啟用一次：`git config core.hooksPath .githooks`。
- `pre-push` hook：強制 backend Python 語法＋追蹤檔無 `LAN_IP=`；本機 api 在線時報 `/health`；
  `RAGDEMO_SMOKE=1` 加跑 `/query`；離線只警告不擋。

## 三機分工（2026-09-26 現況）

* **x570 100.119.83.111**：主力。api/qdrant/pg **全 docker compose**（三容器），
  `LLM_MODEL=qwen3:14b`，資料唯一來源。cloudflared local tunnel + crontab restart loop。
* **mbp 100.64.121.9**：加速。api（uvicorn@8000，launchd）+ 本機 qdrant 備援已完成，
  `LLM_MODEL=qwen3:14b`；`QDRANT_URLS`/`POSTGRES_DSN` 指 x570，離線降級本機。
  （規劃遷移 docker compose——見 ROADMAP §4.1.1）
* **msi 100.65.68.106（demo）**：api 在 WSL2（`start-msi.sh`，開機自動啟動）＋本機 qdrant 備援，
  `LLM_MODEL=qwen3:8b`（2026-09-23 由 4b 換上：4b 的 `think:false` 是已知 bug）。
  （規劃遷移 docker compose——見 ROADMAP §4.1.1）

## 資安收斂（2026-09-26 定案）

回應「port 暴露 0.0.0.0 / 弱密碼 / 無認證」稽核，本輪完成：

### Port 綁定（folding 到最小曝露面）
| 服務 | 綁定 | 理由 |
|---|---|---|
| qdrant 6333 | `${TS_IP}:6333`（x570=100.119.83.111） | mbp/msi 備援 snapshot 靠 tailscale 拉取；阻 LAN(192.168)/公網 |
| postgres 5432 | `${TS_IP}:5432` | mbp/msi 心跳共用 registry（`sync-snapshot`不需 pg）；阻 LAN/公網 |
| api 8000 | `127.0.0.1:8000` | cloudflared / dev proxy 都本機連；公網只經 tunnel |

- `compose.yaml` 用 `${TS_IP}` 變數→各機 .env 帶各自 TS_IP 即自動對應，無需改 yaml。
- 保留 mbp/msi 用 `http://<TS_IP>:6333/5432` 經 tailscale 存取（registry 心跳、快照備援）。

### 認證
- **qdrant**：`QDRANT__SERVICE__API_KEY=${QDRANT_API_KEY}`（`.env`）。無 key 回 401。
  `rag.py` `_req(kind="qdrant")` 自動帶 `api-key` header；`sync-snapshot.sh` 支援
  `QDRANT_API_KEY` env（出站認證，mbp/msi 的 crontab/launchd 要帶）。
- **postgres**：`POSTGRES_PASSWORD` 採 `:?` 必填語法，**移除了 `changeme` fallback**。
  **坑**：`POSTGRES_PASSWORD` 只對首次容器初始化生效——既有 volume 需 `ALTER USER rag PASSWORD` 手動同步
  （x570 已做，2026-09-26）。127.0.0.1 來源在 pg_hba 是 `trust`，會誤導成「密碼對」，要以 tailscale
  來源測試真正的 credentials。
- **api `/query`**：**不加後端 token 認證**。前端 worker（`+server.ts`）對 query/ingest/eval/rules
  做 Google 登入 guard，且 port 收斂後 `/query` 只經 `127.0.0.1`＋cloudflared＋worker 三層到達
  （worker 轉發不帶 token，加 `_require_admin` 會弄壞登入流程）。
- `/rules` 寫入另用 `ADMIN_TOKEN`（`Authorization: Bearer`）double-check。

### 密鑰管理
- repo 根 `.env`（gitignored，compose auto-read）。`backend/.env.example` 為樣板＋說明。
- 掃描確認 git 無真實 token（git ls-files、.env 追蹤數 0、歷史/前端建置產物皆無）。

## 備援機制（x570 離線時各機獨立作業）

```
x570 qdrant（資料唯一來源）
   │  資料變更（points_count 變化）
   ▼
scripts/sync-snapshot.sh（crontab 每 10 分鐘，MSI 已掛；mbp 用 launchd 同頻率）
   │  POST /collections/laws/snapshots → 建新快照
   │  下載 → 本機刪舊 collection → 重建 → upload?priority=snapshot 還原
   │  驗證點數與 x570 一致 → 更新 state；順手清 x570 舊快照
   ▼
各機本機 qdrant（MSI: 127.0.0.1:6333 ✅ / mbp: 127.0.0.1:6333 ✅）
```

- 快照很小（目前 3 筆≈174KB；500~1000 筆≈10–60MB, zstd），同步成本低。
- **快照還原（2026-09-24 修正）**：刪本機後直接 `upload?priority=snapshot`（含 sparse 雙向量），
  不再預建 dense-only collection。
- **QDRANT_API_KEY 出站（2026-09-26）**：x570 開 key 後，mbp/msi 的 sync-snapshot 排程
  （crontab/launchd）環境需帶 `QDRANT_API_KEY`（見資安收斂）。

### QDRANT_URLS 降級（全 tailscale＋本機）
```
QDRANT_URLS=http://100.119.83.111:6333,http://127.0.0.1:6333
```
`rag.py _pick()`：依序試候選，首個通連者快取；連線錯誤自動降級下一個。
→ x570 在線用 x570（最新）；離線自動切本機（快照資料），query 不中斷。

### 外出 demo 模式（2026-09-23 定案：零改造）
「x570 斷線」當常態：帶出門 x570 在家離線，**mbp 自動扮演主力**，msi 當備援。
worker 自動模式依 x570→mbp→msi 探測；出發前 mbp 跑 `bash scripts/sync-snapshot.sh` 補最新快照。
詳見 ROADMAP §4.6。

### pg / registry 的定位
- `POSTGRES_DSN` 指 x570（tailscale）：心跳寫 `backends` 表、`GET /hosts` 讀表。
- 心跳自動清 `REGISTRY_STALE_MIN`（3 分鐘）未報到的 host row；PG 離線時此清理不跑、無害。
- x570 離線時：心跳失敗只是 warning，**不影響 /query**；`/hosts` 回空清單。
- **用量統計**：`usage.py` 把 provider/model 逐日寫入 `model_usage` 表（best-effort，
  寫入失敗吞掉）——`/models` 前端顯示「今日 N 次／約 T tokens」。見「LLM 雲端路由」。

## LLM 雲端路由（2026-09-26）
前端下拉除地端 ollama 外有 8 個雲端 provider 群組（模型清單以 `model="<pfx>/<id>"` 註記；
`rag.py` `generate()` 依前綴路由，完整路由表＋額度＋驗證見 `ROADMAP.md` §3.5）。

`openrouter/`、`zen/`、`nv/`（NVIDIA NIM）、`gemini/`、`groq/`、`cohere/`、`hf/`、`mis/`（Mistral）。
認證分兩類：**CF AI Gateway 代管**（openrouter/gemini/groq/cohere/mistral，本機只需 CF token；
gateway URL 由 `OPENROUTER_GATEWAY_URL` 尾段 `/openrouter` 換 `/google-ai-studio`、`/groq/v1`、
`/cohere/v1beta`、`/mistral/v1` 推導）vs **直連需自有 key**（zen 需 `ZEN_API_KEY`、nv 需
`NVIDIA_API_KEY`、hf 需 `HF_TOKEN`）。

- 用量統計（`usage.py`，PG `model_usage`）、429 限流標記（`rag._LIMITED`，重置時間由 header 推估）、
  free 額度分數（`rag.FREE_QUOTA`，來源 mnfst/awesome-free-llm-apis，全「次數型」）由 `/models` 回傳。
- 前端自製下拉（原生 `<select>` 無法對內部子字串著色／右對齊）。
- 完整路由表＋額度＋驗證見 `ROADMAP.md` §3.5；排障見 `settings/opencode/CF-AIG-TOKEN-ENV.md`。

## 題庫（rules）與 JEV 驗證（2026-09-26）

- `/rules`：題庫寫入（`rules.py`），關鍵字匹配 → 固定答案。讀取不需 token、寫入要 `ADMIN_TOKEN`
  （`Authorization: Bearer`）。前端 worker 已把 `/rules` 列入 SENSITIVE（需 Google 登入）。
- JEV（TypeSafe System One）驗證：LLM 產出「防編故事」驗證器，題庫採用時以驗證信心閘門
  （`JEV_VERIFY_MIN` 驗證、`JEV_BANK_MIN` 題庫採用的影響）把關；`TYPESAFE_API_KEY` 提供金鑰。
  未設／`JEV_DISABLED` 時不啟用。前端在回答區顯示信心/來源（confidence/trace）。

## 模型清單（歷史參考，2026-09-21）
* x570：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3:14b`、`qwen3-coder:latest`
* mbp：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3-coder:latest`、`qwen3-coder-next:latest`、`qwen3:14b`
* MSI：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3:8b`、`qwen2.5-coder:7b`

## 服務埠（2026-09-26 收斂後）
* 8000 api：x570 docker compose／mbp／msi，全綁 `127.0.0.1`（公網只經 cloudflared tunnel）
* 6333 qdrant：x570 綁 `${TS_IP}:6333`；MSI/mbp 本機備援綁 `127.0.0.1:6333`
* 5432 postgres：x570 綁 `${TS_IP}:5432`（mbp/msi 心跳經 tailscale 存取）
* 5173 前端 dev（本機）／11434 ollama（各機 native，綁 localhost）

## 公網接手（Cloudflare tunnel keepalive 規範，2026-09-23 定案）

三台各自一條 local tunnel（`ragdemo-x570`/`ragdemo-mbp`/`ragdemo-msi`），
公網 hostname `api-x570`/`api-mbp`/`api-msi.ragdemo.win` → 各機 `http://localhost:8000`。
**三台統一 `--protocol http2`**（WSL2 的 UDP/QUIC 連 edge 全 timeout，實戰心得）。

### keepalive 兩層（進程層＋edge 長連線）
1. **進程層**：x570/msi 用 crontab `restart loop`（tunnel 崩潰每 5s 重拉；api/qdrant 每 30s 冪等檢查），
   mbp 用 launchd `KeepAlive=true`。（x570 api 在 docker compose `restart: unless-stopped`；三機
   cloudflared 一律 `--protocol http2`。）
2. **edge 連線層**：`curl http://127.0.0.1:20241/metrics` 應見 4 條連線、errors=0。

### mbp 限制（2026-09-24 確認）
mbp 的 `~/Library/LaunchAgents/` agent 需 **GUI 登入後**才載入；壓電源停在登入畫面時
公網 `api-mbp` 會 530、本機 8000/6333 無服務——登入後自動恢復。接受此行為（本機 GUI Mac）。

## 模型 keepalive（ollama 常駐，2026-09-23 定案）
各主機預設 LLM 與 embedding 啟動後即常駐（`keep_alive: KEEP_ALIVE`，request 層指定）。
`KEEP_ALIVE` env（`.env`）：`-1`=永久常駐（預設）、`0`=即時卸載、`"30m"`=30 分。
api lifespan 跑 `rag.warmup()` 預載（best-effort，失敗只 log）。驗證：`ollama ps` UNLOAD 空白。

## 環境變數（repo 根 `.env`，不進版控；compose 自動讀取）
- 必填：`HOST_ID`、`TS_IP`（各機 tailscale IP）、`POSTGRES_PASSWORD`（強制，無 fallback）、
  `QDRANT_API_KEY`（qdrant 認證，三台同值）。
- 資料：`OLLAMA_URLS`、`LLM_MODEL`、`EMBED_MODEL=bge-m3:latest`、`RERANK_MODEL`、`COLLECTION=laws`、
  `QDRANT_URLS`、`POSTGRES_DSN`。
- 雲端路由：`OPENROUTER_GATEWAY_URL`、`CF_AIG_TOKEN`（或 `CF_AIG_TOKEN_FILE`）、`ZEN_API_KEY`、
  `NVIDIA_API_KEY`、`HF_TOKEN`、各 provider `*_MODELS` 清單。
- 驗證/權限：`TYPESAFE_API_KEY`、`JEV_VERIFY_MIN`、`JEV_BANK_MIN`、`ADMIN_TOKEN`。
- IP 準則：全部 tailscale 位址；本機服務才允許 127.0.0.1，不用 LAN_IP。

## 評測門檻
`POST /eval` hit_rate 未達 0.8 不進 UI，先修切分/召回。`evals/` 見 README。

## Google 登入（frontend，OAuth2 PKCE）
- Server routes：`/auth/login`（PKCE＋state cookie → 302 Google）、`/auth/callback`（code 換 token、
  JWKS RS256 驗證、HMAC session cookie 12h）、`/auth/logout`、`/auth/me`。
- env：`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `SESSION_SECRET`
  （dev 填 `frontend/.env`，上線填 Pages 變數）。
- Google Cloud 重導 URI 需登記：`http://localhost:5173/auth/callback`、`https://ragdemo.win/auth/callback`。
- **2026-09-24 起 `/query` 也需登入**：`+server.ts` 的 `SENSITIVE`（query/ingest/eval/rules）一律
  Google 登入守護（401 未登入）——前端「登入才可查詢」；`/health`、`/models` 不需登入（探測用）。

## TODO
* `rag.py rerank()` 還是 stub，待接真正 reranker 打分
* `evals/questions.json` 佔位，待擴 50 題（目前 ~14 題，`/eval` 14/14）
* 判決注意個資去識別化，回答僅供參考非法律意見
* 精簡包擴到 500~1000 筆（目前 3 筆，同步機制已就位）
* 需離線 `/hosts` → mbp/msi 另建 pg 副本（低優先）
* 二機遷移 docker compose（ROADMAP §4.1.1）
* OPENROUTER 真餘額顯示需 management key（外部資源暫無）

## 啟動
**x570（docker compose，2026-09-26 現況）**：
```bash
# .env 在 repo 根（gitignored）；backend/.env.example 只作樣板
docker compose up -d --build   # 首次建 api 映像，之後改碼加 --build
curl localhost:8000/health     # 得 host_id=x570
cd frontend && pnpm install && pnpm run build && pnpm run dev   # pnpm（非 npm）
```
**MSI（WSL2，吃 Windows 本機 ollama；未遷移）**：
```bash
bash backend/start-msi.sh      # 冪等：api＋本機 qdrant 一起拉起（開機自動啟動見 ROADMAP §2.5/2.7）
curl localhost:8000/health     # 回 host_id=msi, llm=qwen3:8b
```
**mbp（launchd，登入自動跑；未遷移）**：
```bash
launchctl load ~/Library/LaunchAgents/com.ragdemo.qdrant.plist    # qdrant @127.0.0.1:6333
launchctl load ~/Library/LaunchAgents/com.ragdemo.api.plist       # uvicorn @8000（--env-file .env）
launchctl load ~/Library/LaunchAgents/com.ragdemo.sync-snapshot.plist  # 每10分鐘快照同步
curl localhost:8000/health     # 回 host_id=mbp
cd frontend && pnpm run dev    # 前端 dev（proxy → localhost:8000，需 Node≥22）
```

## 備援同步操作
```bash
bash scripts/sync-snapshot.sh [source_url] [dest_url] [collection]
# 例：  bash scripts/sync-snapshot.sh http://100.119.83.111:6333 http://127.0.0.1:6333
# 排程：MSI 用 crontab（*/10）；mbp 用 launchd com.ragdemo.sync-snapshot（每 10 分鐘＋登入）
# x570 已開 qdrant key → 排程環境要帶 QDRANT_API_KEY（見資安收斂/§4.1.1）
```
log `~/qdrant/sync.log`、state `~/qdrant/.sync-state`（點數＋快照名，未變化即 skip）。

## Demo 精簡包
x570 全量 → 精選 500~1000 筆 → 靠 `sync-snapshot.sh` 快照機制同步到各機本機 qdrant，
x570 離線時各機照常 `/query`（檢索能力一致，LLM 各機自備）。