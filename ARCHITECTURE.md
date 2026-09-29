# RagDemo 架構（維護用）

> 本檔描述**目前實作**（2026-09-26）。規劃中的跨機遷移見 `ROADMAP.md` §4.1.1。
>
> ⚠️ **x570 端另有待確認事項：見 [`X570-HANDOFF.md`](X570-HANDOFF.md)** ——
> qdrant key 輪換後 MSI／mbp 被 401、`POSTGRES_PASSWORD` 三台不一致導致 registry
> 心跳失敗、2026-09-26 關機異常根因未證實、每日 ingest 尚未排程。
>
> ⚠️ **x570 / mbp 升級時請先看 [`HOST-UPGRADE.md`](HOST-UPGRADE.md)** ——
> 2026-09-26 容器化與檢索修正後，兩台各有一份 per-host 待辦：輪換外洩的 qdrant
> key、補身份環境變數、排程補 `SRC_API_URL`、mbp 加 launchd worker、x570 首次跑
> ingest。MSI 已完成。

## 目錄結構

```
compose.yaml       # qdrant + postgres + api（三機共用，x570 已跑 docker compose）
backend/           # FastAPI：/health /ingest /query /eval /rules /models
  app/             #   main.py / rag.py / registry.py / usage.py / rules_store.py
                   #   law_struct.py / __init__.py
    common/        #   backend 與 ingest 共用：sparse.py（法規 sparse tokenizer，
                   #   純 stdlib）/ text.py（空白正規化）/ pg.py（共用連線池）
                   #   / jsonl.py（JSONL 讀取）。ingest 走 sys.path 引用同一份。
  .env.example     #   樣板（真實 .env 在 repo 根，不進版控）
frontend/          # SvelteKit：只打 /api/*（Google 登入守護 SENSITIVE 路徑）
  src/routes/api/[...path]/+server.ts   # worker：登入 guard + 三台備援轉發
  src/routes/auth/                        # OAuth2 PKCE 登入
evals/             # 評測題庫（questions.json + README）
ingest/laws/       # 法規資料擷取/切分/落 PG（pg_load.py / pg_schema.sql）
scripts/
  sync-snapshot.sh #   x570→本機 qdrant 快照自動同步（備援核心；--force 可強制重抓）
  law-update-worker.sh #  執行前端「更新」按鈕排入的請求（容器跑不了 ingest，故在 host 端跑）
HOST-UPGRADE.md    # x570 / mbp 升級 runbook（per-host 待辦，見上方提醒）
.opencode/agents/  # ingest / backend / frontend / eval 四個子代理
.github/           # workflows/ci.yml（三 job）＋ dependabot.yml；見〈三機紀律的 server 端執行〉
```

## 設計目標（2026-09-22 定案）
**x570 不在線時，mbp 與 MSI 都能獨立作業。**
除硬體與 LLM 模型差異外，三台的**資料與檢索能力盡量一致、各自可獨立運作**。

- 依賴關係：各機自備 ollama（LLM/embedding，硬體差異故模型不同）；
  資料層（qdrant）以「快照同步」維持一致；pg 只有 registry 心跳＋用量統計用（非 query 必需）。
- 降級邏輯走 `QDRANT_URLS`/`OLLAMA_URLS` 候選清單：x570 優先，離線自動切本機。
  ⚠️ 2026-09-27 查證：`QDRANT_URLS` 在容器內其實用不到（compose 只傳 `QDRANT_URL`），
  容器化後這條降級路徑並不存在；實際降級靠「快照同步」而非 live 讀 x570。見
  〈備援機制〉與 `settings/env/README.md` 的 QDRANT_URLS 說明。

## 架構表（2026-09-27 定案，模組化的單一入口）

**規則：每次開發只開一個 scope，只動一個模組。** scope 的登錄與佇列見 `SCOPE.md`
（同時最多一個進行中 —— 這是刻意的：三台共用同一份 repo，跨模組的半成品會被
另外兩台的 `env-sync pull` 自動分發出去）。

| # | 模組 | 目標（一句話，驗收的依據） | 擁有者 agent | 架構書 | 邊界（**不**負責） |
|---|---|---|---|---|---|
| M1 | env | 三台環境變數的單一真相：哪個鍵共用、哪個 per-host、哪個是 per-host 機密，全部由機器驗證 | `env-ops` | `settings/env/README.md` | 不動 `compose.yaml`、不動 backend 程式碼 |
| M2 | backend | 檢索本體：路由、模型選擇與降級、registry、rules 儲存 | `backend` | `backend/DESIGN.md` | 不動 ingest、不動 frontend |
| M3 | ingest | 法規／判決的蒐集、清洗、切分、metadata | `ingest` | `ingest/laws/DESIGN.md`、`ingest/cases/DESIGN.md` | 不改檢索邏輯與 UI |
| M4 | frontend | UI 與 Cloudflare worker；只打後端相對路徑 `/api/*` | `frontend` | `frontend/DESIGN.md` | 不直連 Ollama / Qdrant / pg |
| M5 | eval | 引註命中率；不通過不准進 UI 階段 | `eval` | `evals/README.md` | 只讀不寫碼 |
| M6 | ops | 三機部署、runbook、registry 心跳、版本一致性 | `ops` | `scripts/DESIGN.md`、`HOST-UPGRADE.md` | 不改檢索邏輯、不決定憑證值 |

### 模組契約：每份架構書都必須回答這 5 題

1. **目標** —— 一句話，以及「做到什麼算完成」
2. **邊界** —— 這個模組**不**負責什麼（避免相鄰模組互相改，產生第二真相）
3. **不變量** —— 壞了會出事、但測試不一定抓得到的規則
   （例：不可寫死 IP；per-host 機密永不進 sops；追蹤檔不得出現 `LAN_IP=`）
4. **陷阱** —— 實測踩過、文件沒寫就會再踩一次的坑
5. **驗收** —— 這個模組改完後跑哪條指令算過

### 不知道怎麼辦時的升級路徑（三段，不准跳）

1. 先讀**該模組的架構書**（上表第 4 欄）
2. 架構書答不了 → 讀本檔對應章 ＋ `SCOPE.md` 當前 scope 的驗收條款
3. 還是答不了 → **停下來問使用者**。不要猜、不要拿未查證的值頂替。

理由：三台共用同一份 repo，一個 agent 猜出來的設定會被另外兩台的 `env-sync pull`
自動分發 —— 猜等於把謊言版本化，而且症狀是「那台機器莫名其妙壞掉」，
最貴的時候是幾天後才發現。**寧可卡住。**

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

### 三機紀律的 server 端執行（2026-09-26 補齊）
本 repo 在 GitHub 上是 **PUBLIC**，但上述紀律原本只存在於「按 push 的那台機器」——
三台各有一份 hook，任何一台沒設 `core.hooksPath`（或用 `--no-verify`）就形同虛設。
故在 GitHub 端補兩層，且**不影響三台既有的直接 push 流程**：

| 紀律 | 本機（第一道） | GitHub 端（第二道） |
|---|---|---|
| commit 前綴 | `.githooks/commit-msg`（擋） | ci.yml `guards` job（**只回報，擋不了**） |
| 語法／`LAN_IP=`／pytest | `.githooks/pre-push`（擋） | ci.yml `backend` job（全 push 都跑） |
| 單檔 ≤ 10 MB | — | ci.yml `guards` job（**只回報**） |
| 禁刪 main | — | ruleset `deletion`（**真的擋**，唯一擋得住的一條） |

**為什麼 commit 前綴擋不了**：原本打算用 ruleset 的 `commit_message_pattern` 在 server 端擋，
但該 rule **只適用於 enterprise 擁有的 repo**，個人帳號的 repo 拿不到（API 回 422
`Invalid rule`，換 preview media type 也無效；`branch_name_pattern`、
`commit_*_email_pattern` 同樣限制）。`max_file_size` 也退階 —— 它只能用 `push` target，
而 push ruleset 在公開 repo 不允許（422 `Source public repos cannot have push rules`）。
兩者因此改放 CI。**CI 是事後驗證：跑起來時 commit 已在 main**，所以只能是紅字提醒，
真正的第一道仍是各機的 hook。

- **刻意不設** `required_status_checks`：那會要求「先推到別的分支、CI 過了才能進 main」，
  與三機直接推 main 的現況衝突。要保留現況，就讓本機 hook 當第一道、CI 當可外部驗證的第二道。
- **刻意不設** `non_fast_forward` / `required_linear_history`：保留 force-push 當逃生門，
  且歷史含 merge commit。
- `dependabot[bot]` **不需要** bypass actor —— 唯一會擋的 `deletion` 與它無關。
  squash merge 時 **PR 標題要帶前綴**（GitHub 預設拿 PR 標題當 commit message）。
- 規則本身 living 於 GitHub（不在 repo 內，clone 不會帶到）：repo Settings → Rules → Rulesets，
  現有 `main-禁刪分支`（id 24032902）。`.github/` 只有 `workflows/ci.yml` 與 `dependabot.yml`。
- 歷史包袱：169 個 commit 中有 20 個來自 2026-09-22 提交準則「之前」，不符前綴；
  已全在 main，`guards` 只掃本次 push 範圍（新引入的 commit），故不會誤報。

## 三機分工（2026-09-26 現況）

* **x570 100.119.83.111**：主力。api/qdrant/pg **全 docker compose**（三容器），
  `LLM_MODEL=qwen3:14b`，資料唯一來源。cloudflared local tunnel + crontab restart loop。
* **mbp 100.64.121.9**：加速。api/qdrant/pg **全 docker compose（三容器，OrbStack runtime）**，
  `LLM_MODEL=qwen3:14b`（本機 ollama）＋qdrant 本機備援（快照同步來自 x570）；
  容器 api 的 `POSTGRES_DSN` 指 x570 共享 registry（見 ROADMAP §4.1.1）。
* **msi 100.65.68.106（demo）**：api/qdrant/pg **全 docker compose（三容器，Docker Desktop
  4.92 + WSL integration，2026-09-26 容器化）**，`LLM_MODEL=qwen3:8b`（2026-09-23 由 4b 換上：
  4b 的 `think:false` 是已知 bug）＋qdrant 本機備援（快照同步來自 x570）。
  容器 api 的 `POSTGRES_DSN` 指 x570 共享 registry。**注意 WSL 內沒有 tailscale 介面**，
  tailscale 跑在 Windows host 上，`TS_IP` 仍填 host 的 `100.65.68.106`。
  開機自啟＝Docker Desktop `AutoStart` ＋ Startup 的 `wsl-ubuntu-start.vbs`（拉起 distro，
  因為五個 bind mount 來自它；細節見 ROADMAP §4.1.1）。

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
- **repo 根 `.env`＝唯一真相來源**（gitignored，compose auto-read；`sync-snapshot.sh`／
  `law-update-worker.sh` 也自己載入它，2026-09-26 移除 `backend/.env` 這份副本 ——
  兩份並存時 `QDRANT_API_KEY` 曾漂移，造成「本機 200、遠端 401」）。
  樣板／說明見根 `.env.example`（2026-09-26 改）；`python3 scripts/env-audit.py`
  可列出每個變數的消費者、幽靈變數與副本漂移。
- **per-host 值的唯一真相＝`settings/env/hosts.shared.env`（2026-09-27）**：三台的
  `x570_`／`mbp_`／`msi_` 值寫在同一個被追蹤的明文檔裡（不含憑證），
  `scripts/env-sync.sh render` 依本機 `HOST_ID` 挑列、展開 `${VAR}` 後寫進
  **不帶前綴**的 `.env`；`--check` 驗 schema 完整性與漂移。
  前綴刻意不進執行期 `.env`：compose 只認 `${VAR}`，沒有依 `HOST_ID` 動態選
  `msi_`／`x570_` 的能力 —— 前綴若寫在 `.env`，值會讀不到而回退原始碼預設
  （`LLM_MODEL` 掉回 14b、`TS_IP` 讓 `ports:` 綁錯而啟動失敗），屬靜默劣化。
  `HOST_ID` 本身是 render 的選擇器，只能各機手動設一次。
- **`POSTGRES_PASSWORD` ≠ DSN 裡的密碼（2026-09-27 MSI 實測）**：前者是**本機**
  pg 容器的密碼（各機可不同），而 `POSTGRES_DSN` 指向 **x570**，裡面必須是
  x570 的 pg 密碼。兩者 sha256 前 12 碼不同（`55cebf3c8276` vs `e328bd31728a`，
  皆 32 字元）。用 `${POSTGRES_PASSWORD}` 展開 DSN 會製造「設定都有、
  心跳就是 password authentication failed」。連 x570 的 pg 密碼目前沒有對應
  變數（照 `QDRANT_PEER_API_KEY` 慣例應叫 `POSTGRES_PEER_PASSWORD`），
  值待 x570 查證後納管 —— 見 `X570-HANDOFF.md` 事項 2。
- **共用憑證分發（2026-09-27）**：7 把**必須三台一致**的憑證
  （`QDRANT_PEER_API_KEY`／`ADMIN_TOKEN`／`CF_AIG_TOKEN`／`HF_TOKEN`／
  `NVIDIA_API_KEY`／`TYPESAFE_API_KEY`／`ZEN_API_KEY`）改走 `settings/env/` 分層
  ＋`sops`+`age` 加密追蹤，`scripts/env-sync.sh pull` 合併進各機 `.env`。
  流程見 `settings/env/README.md`。
- **憑證分類標準只有一條：有沒有跨機的讀寫關係**（2026-09-27 逐點 grep 查證後改正）。
  `QDRANT_API_KEY` 與 `POSTGRES_PASSWORD` 曾被歸為「三台必須同值」，查證每一個
  消費點後確認**都只指向自己那台**：

  | 鍵 | 消費點 | 為什麼是 per-host |
  |---|---|---|
  | `QDRANT_API_KEY` | `compose.yaml:12`（自己 qdrant 容器的 `QDRANT__SERVICE__API_KEY`）、`compose.yaml:34` ＋ `rag.py:331`（api 打 `compose.yaml:33` 寫死的 `QDRANT_URL: http://qdrant:6333`） | 跨機認證走的是 `QDRANT_PEER_API_KEY` |
  | `POSTGRES_PASSWORD` | `compose.yaml:24`（自己的 pg 容器）、`compose.yaml:36`（DSN 預設值裡的 `@postgres:5432`，也是自己的） | 連 x570 的 pg 密碼應另設 `POSTGRES_PEER_PASSWORD` |

  兩把改為 **per-host 機密**：鍵名宣告在 `settings/env/secrets.host.env.example`
  （值空、追蹤）讓 `--check` 驗「每台都有這兩個鍵」，真值只留該機 `.env`（600），
  **永不進 sops、永不分發**。`env-sync.sh` 在**合併引擎層面**剔除這兩把
  （不只靠「加密檔剛好乾淨」），且 `--check` 會驗「per-host 鍵不得出現在加密檔」
  —— sops 的 dotenv 讓鍵名保持明文，所以這條**不解密就能驗，CI 沒有 age 私鑰也跑得到**。
  **好處：另外兩台完全不需要做任何事**（`pull` 只新增／覆寫，從不刪除既有鍵），
  輪換成本從「三台鎖步＋重啟」降到「一台」。

  舊分類錯在把 2026-09-26「本機 200／遠端 401」的根因誤認為「key 要三台同值」；
  真正的根因是 `backend/.env` 與根 `.env` **兩份副本**（已刪）。根因修掉後舊分類
  被留著當保險，副作用是造出「mbp 的 qdrant key 還是第三把舊的」這個
  **不存在的故障** —— mbp 的 key 只對 mbp 自己的 qdrant 有意義。
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
各機本機 qdrant（容器化後一律綁 `${TS_IP}:6333`，非 loopback）
```

- 快照很小（目前 3 筆≈174KB；500~1000 筆≈10–60MB, zstd），同步成本低。
- **快照還原（2026-09-24 修正）**：刪本機後直接 `upload?priority=snapshot`（含 sparse 雙向量），
  不再預建 dense-only collection。
- **QDRANT_API_KEY 出站（2026-09-26）**：x570 開 key 後，mbp/msi 的 sync-snapshot 排程
  （crontab/launchd）環境需帶 `QDRANT_API_KEY`（見資安收斂）。

### QDRANT_URLS 降級（全 tailscale＋本機）
```
QDRANT_URLS=http://100.119.83.111:6333,http://${TS_IP}:6333
# ⚠️ 容器內其實用不到這行：compose 只傳 QDRANT_URL=http://qdrant:6333（服務名解析），
#    不傳 QDRANT_URLS。此變數只影響「原生執行」的情況（已全數容器化 → 現行無作用）
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

### 實測注意事項（2026-09-26 補）

provider 地雷。這些是實測踩到的，不在任何程式碼裡 —— 刪掉就真的會再踩一次：

- **`gemini-2.5-*` 對新 key 回 404**「no longer available to new users」——
  新申請的 Gemini key 不能用 2.5 系列，選 3.x 系列。
- **CF AI Gateway 的 huggingface provider 已下架**：指向 `api-inference.huggingface.co`
  回 `530 Origin DNS error`。`hf/` 群組要直接接官方 `router.huggingface.co`
  （`HF_BASE_URL` 預設值），並用 Settings→Access Tokens 的 Fine-grained token
  （選 Inference preset，`hf_...` 開頭）。
- **Mistral 上游 rate limit**：`mistral-small`/`medium` 家族回 `429 code 1300`。
  實測可用的是 `ministral-8b-latest` 與 `codestral-latest`（即 `MISTRAL_MODELS` 預設）。
- **msi 為何是 `qwen3:8b` 而非 4b**：4b 的 `think:false` 是已知 bug ——
  連「1+1=?」都會思考 1000+ token。8b 已實測正常（2026-09-23 換上）。

## 題庫（rules）與 JEV 驗證（2026-09-26）

- `/rules`：題庫寫入（`rules_store.py`），關鍵字匹配 → 固定答案。讀取不需 token、寫入要 `ADMIN_TOKEN`
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
* 6333 qdrant：三機一律綁 `${TS_IP}:6333`（容器化後無 loopback 例外）
* 5432 postgres：x570 綁 `${TS_IP}:5432`（mbp/msi 心跳經 tailscale 存取）
* 5173 前端 dev（本機）／11434 ollama（各機 native，綁 localhost）

## 公網接手（Cloudflare tunnel keepalive 規範，2026-09-23 定案）

三台各自一條 local tunnel（`ragdemo-x570`/`ragdemo-mbp`/`ragdemo-msi`），
公網 hostname `api-x570`/`api-mbp`/`api-msi.ragdemo.win` → 各機 `http://localhost:8000`。
**三台統一 `--protocol http2`**（WSL2 的 UDP/QUIC 連 edge 全 timeout，實戰心得）。

### keepalive 兩層（進程層＋edge 長連線）
1. **進程層**：cloudflared tunnel 崩潰——x570/msi 用 crontab `restart loop`（每 5s 重拉），
   mbp 用 launchd `com.ragdemo.tunnel` `KeepAlive=true`。api/qdrant/pg 三機一律 docker compose
   `restart: unless-stopped`（mbp＝OrbStack，隨登入啟動；msi＝Docker Desktop，隨 Docker Desktop
   啟動 —— 2026-09-26 兩台皆完成容器化，msi 的 crontab 冪等檢查與 `~/bin/ragdemo-api.sh` 已移除）。
   cloudflared 一律 `--protocol http2`。
2. **edge 連線層**：`curl http://127.0.0.1:20241/metrics` 應見 4 條連線、errors=0。

### mbp 限制（2026-09-24 確認，2026-09-26 容器化後仍適用）
mbp 的 **OrbStack app**（qdrant/api/pg 容器）與 `~/Library/LaunchAgents/` agent（tunnel/sync）
皆需 **GUI 登入後**才啟動；壓電源停在登入畫面時，公網 `api-mbp` 會 530、本機 8000/6333
無服務——登入後自動恢復。接受此行為（本機 GUI Mac）。

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
- **變數預設值只有一份真值**：`compose.yaml` 的 `${VAR:-default}` 必須與 `rag.py` 的
  `os.getenv("VAR", default)` 一致，否則「走 compose 的機器」與「直接跑 uvicorn 的機器」
  預設行為不同（2026-09-26 統一 `JEV_VERIFY_MIN`：compose 0.5 → 0.4，與 rag.py／`.env.example` 齊平）。
- IP 準則：全部 tailscale 位址；本機服務才允許 127.0.0.1，不用 LAN_IP。

## 依賴與映像版號策略（2026-09-26 定案）
本 repo 公開、且三台自動 pull／自動 `docker compose up`，**沒有版號釘死的東西等於
上游一出新版就三台同步換掉，無人 review**。故分兩類：

**必須釘死（有 lock 不罩的）**
- `compose.yaml` 的 image tag：`qdrant/qdrant:v1.19.1`、`pgvector/pgvector:0.8.6-pg16`。
  原本寫 `latest` 與 `pg16`（浮動），改動僅為釘版，無行為變化。
  升級流程：Dependabot 開 PR → 人工看 → `docker compose pull && up -d` → 驗 `laws` 筆數。
- `.github/workflows/ci.yml` 的 action 以 commit SHA 釘死（註解標版本），Dependabot 追。

**由 lock 罩住，不需額外釘**
- Python：`uv.lock`（根＋backend）。`requires-python` 三處已對齊 **>=3.12**
  （原 backend 寫 `>=3.11`、其餘 3.12，只會讓某台裝 3.11 通過但行為與容器不一致）。
- 前端：`pnpm-lock.yaml` ＋ `packageManager: pnpm@12.6.0`。
- `backend/Dockerfile` 的 `uv` base image **刻意不釘**：uv 沒發布帶版本的組合 tag
  （實測 `0.12.19-python3.12-bookworm-slim` = 404），且 `uv sync --frozen` 下
  真正決定依賴的是 `uv.lock`，uv 二進位版本影響很小 —— 與 image tag 不同類。

- Node 下限 `>=22.12` 寫在 `frontend/package.json` 的 `engines`（取自 vite 8 與
  `@sveltejs/vite-plugin-svelte` 的聯集；`wrangler` 只要求 `>=22.0`）。
  2026-09 現況：24.x 為最新 LTS、20.x 已過期。**注意** pnpm 預設只警告不擋，
  要硬擋需在 `frontend/.npmrc` 加 `engine-strict=true`（尚未加，因 x570 的 Node 版本未確認）。

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
* 三機皆已容器化（2026-09-26：x570／mbp OrbStack／msi Docker Desktop）；
  殘餘的原生相依是 **ollama（Windows/macOS 主機，三機共用不容器化）與 cloudflared tunnel**
* OPENROUTER 真餘額顯示需 management key（外部資源暫無）

## 啟動
**x570（docker compose，2026-09-26 現況）**：
```bash
# .env 在 repo 根（gitignored，唯一真相來源）；.env.example 只作樣板
docker compose up -d --build   # 首次建 api 映像，之後改碼加 --build
curl localhost:8000/health     # 得 host_id=x570
cd frontend && pnpm install && pnpm run build && pnpm run dev   # pnpm（非 npm）
```
**MSI（WSL2 ＋ Docker Desktop，吃 Windows 本機 ollama；2026-09-26 容器化）**：
```bash
# 前置：Docker Desktop 已開且 WSL integration 已勾 Ubuntu（否則 distro 內無 docker）
docker compose up -d --build   # 改 api 碼後加 --build；qdrant/pg 不必動
curl localhost:8000/health     # 回 host_id=msi, llm=qwen3:8b
# 開機自啟：Docker Desktop AutoStart ＋ Startup 的 wsl-ubuntu-start.vbs（拉起 distro）
```
**mbp（docker compose，OrbStack，2026-09-26 容器化）**：
```bash
# OrbStack app 起動即滿載三容器；.env 在 repo 根（gitignored）
docker compose up -d --build     # 改 api 碼後加 --build；qdrant/pg 不必動
curl localhost:8000/health       # 回 host_id=mbp
launchctl list | grep ragdemo    # 只剩 tunnel + sync-snapshot（api/qdrant 已無 launchd）
cd frontend && pnpm run dev      # 前端 dev（proxy → localhost:8000，需 Node≥22）
```

## 備援同步操作
```bash
bash scripts/sync-snapshot.sh [source_url] [dest_url] [collection]
# 例：  bash scripts/sync-snapshot.sh http://100.119.83.111:6333 http://100.65.68.106:6333
# 排程：MSI 用 crontab（*/10）；mbp 用 launchd com.ragdemo.sync-snapshot（每 10 分鐘＋登入）
# x570 已開 qdrant key → 排程環境要帶 QDRANT_API_KEY（見資安收斂/§4.1.1）
```
⚠️ **dest 必須是容器綁的 `${TS_IP}:6333`，不是 `127.0.0.1:6333`** —— compose 只發布到
tailscale IP，綁 loopback 會連不上（容器化後 MSI 踩過）。省略第二參數時腳本會用
`http://${TS_IP:-127.0.0.1}:6333`，但 cron 環境若沒帶 `TS_IP` 就會退回 loopback 而失效。

log `~/qdrant/sync.log`、state `~/qdrant/.sync-state`（點數＋快照名，未變化即 skip）。
`~/qdrant` 在容器化後**只是腳本自建的暫存目錄**（放 log/state/tmp snapshot），
不再是 qdrant 的資料目錄 —— 資料在 `ragdemo_qdrant_data` volume 裡。

### 法規版本欄（2026-09-26）
前端「連線與來源」表多一欄**法規版本**，顯示各機 `laws` collection 對應的官方法規版本。

* **不記 zip 檔名**：實測官方 `Content-Disposition: attachment; filename=ChLaw.json.zip`
  —— 檔名是固定的，永遠不變，拿它當版本等於三台顯示同一串字。版本一律取
  `ChLaw.json` 內的 **`UpdateDate`**（`sync_daily.py` 已在讀）。
* **兩個來源**（`rag._read_law_version()`）：
  | 檔案 | 誰寫 | 意義 |
  |---|---|---|
  | `data/laws/.law_sync.json` | x570 跑 `sync_daily.py` | 該機下載過的版本 |
  | `data/laws/.law_version` | `sync-snapshot.sh` 同步成功後 | **實際服務的版本**（優先） |
  備援機不跑 ingest、快照也不帶 `.law_sync.json`，所以只能靠同步腳本帶過來。
  容器內 `data/laws` 是唯讀掛載，但**讀**不受限；寫入一律在 host 端由 cron 進行。
* ⚠️ **`SRC_API_URL` 是 per-machine 必要設定**：腳本要向來源機的 `/status` 取版本，
  但三台的 8000 **全綁 `127.0.0.1`、公網只經 cloudflared tunnel**，所以不能用
  `IP:8000`（實作時踩過）。排程必須顯式指定，如 MSI：
  ```
  */10 * * * * .../scripts/sync-snapshot.sh http://100.119.83.111:6333 http://100.65.68.106:6333
  # 腳本自己載入 repo 根的 .env，crontab 不必再 set -a / source
  ```
  未設定時腳本退回 `6333→8000` 改寫，在真實部署多半連不上（log 會記 `取不到`）。
* **端點**：`GET /status` 回 `{ok, host, law_version, log, versions}`。
  `law_version`＝本機；`versions`＝三台＋本機。**呼叫別台的 `/status` 必須帶 `?probe=0`**
  —— 預設會再去探測「它的」三台，不帶就是 A→B→C→A 遞迴，請求數指數成長（實作時踩到）。

## Demo 精簡包
x570 全量 → 精選 500~1000 筆 → 靠 `sync-snapshot.sh` 快照機制同步到各機本機 qdrant，
x570 離線時各機照常 `/query`（檢索能力一致，LLM 各機自備）。