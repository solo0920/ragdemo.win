# RAGDemo 模組化開發架構表（OpenCode Agent 版）

> ⚠️ **本檔是 2026-09-27 的「交接／外傳副本」，權威版是 `ARCHITECTURE.md`〈架構表〉。**
> 兩份內容重疊但**不是同一個真相**：本檔沒有被 repo 內任何檔案引用，原本放在
> 未追蹤的 `files/`（從 Windows 拖進來，帶 `:Zone.Identifier` ADS 檔）。
> 2026-09-27 收進 `docs/` 納入版控時，逐項比對 repo 現況並修正了 6 處事實錯誤
> （見文末〈與 repo 現況的差異〉）。
>
> **不要在這裡新增規則** —— 新規則寫進 `ARCHITECTURE.md`，否則會產生第二真相。

**文件日期**：2026-09-27  
**用途**：交付 opencode agent 進行模組化開發  
**來源**：基於 `ARCHITECTURE.md` §4 定案版本

---

## 核心原則

- **一次開發只開一個 scope，只動一個模組**
- **三機共用同一份 repo，半成品會被自動分發** → 每次 commit 必須完整、測試通過
- **模組責任明確，邊界清晰** → 避免跨模組改動產生第二真相
- **卡住就問，不猜、不頂替** → 三台機器的代價太高

---

## 模組矩陣（M1–M6）

### M1: 環境變數系統 (env-ops)

| 項目 | 內容 |
|------|------|
| **目標** | 三台環境變數的單一真相：哪個共用、哪個 per-host、哪個 per-host 機密，全部機器驗證 |
| **擁有者 Agent** | `env-ops` |
| **架構書** | `settings/env/README.md` |
| **邊界（不負責）** | 不動 `compose.yaml`、不動 backend 程式碼 |
| **關鍵檔案** | `.env`（根，gitignored）/ `.env.example` / `settings/env/hosts.shared.env` / `settings/env/secrets.host.env.example` |
| **驗收指令** | `python3 scripts/env-audit.py` ✓ / `scripts/env-sync.sh --check` ✓ |

**不變量**（一旦破壞會導致跨機器故障）：
- Per-host 機密永不進 sops（`QDRANT_API_KEY`、`POSTGRES_PASSWORD` 只留本機 `.env` 600 mode）
- `HOST_ID` 絕不能讀錯（改錯會讓 compose 無法啟動）
- Tailscale IP 必須正確（寫 LAN_IP 會被 `registry.py` 過濾掉而無人發現）

**常見陷阱**：
- 兩份 `.env` 副本漂移（已移除 `backend/.env`，只留根 `.env`）
- `POSTGRES_PASSWORD` vs `POSTGRES_DSN` 混淆（前者是本機密碼，後者指 x570）
- Compose 不讀 QDRANT_URLS（只讀 QDRANT_URL），多機降級靠快照同步，不靠環境變數

---

### M2: 後端檢索引擎 (backend)

| 項目 | 內容 |
|------|------|
| **目標** | 檢索本體：路由、模型選擇與降級、registry、rules 儲存 |
| **擁有者 Agent** | `backend` |
| **架構書** | `backend/DESIGN.md` |
| **邊界（不負責）** | 不動 ingest、不動 frontend |
| **核心檔案** | `backend/app/main.py` / `rag.py` / `registry.py` / `rules_store.py` / `usage.py`（另有 `law_struct.py` / `sparse.py`） |
| **端點** | `/health` / `/query` / `/ingest` / `/eval` / `/rules` / `/models` / `/status` |
| **驗收指令** | `curl localhost:8000/health` ✓ / 前端 `/query` 通過 ✓ / `pytest -q`（測試在 repo 根 `tests/`） |

**不變量**：
- 模型路由必須支援全 8 個雲端 provider 群組（openrouter/zen/nv/gemini/groq/cohere/hf/mistral）
- `_pick()` 必須實現 x570→本機 降級邏輯（目前暫時略實，容器化後靠快照取代 live 降級）
- `/query` 回應必含 `confidence` / `trace` 欄位（給前端顯示引註來源）

**常見陷阱**：
- Gemini 2.5 對新 key 回 404（新申請的 key 要用 3.x 系列）
- CF AI Gateway 已下架 HuggingFace provider（要直連官方 `router.huggingface.co`）
- Mistral 上游 rate limit 429（只有 ministral-8b-latest/codestral-latest 可用）
- MSI 為何 qwen3:8b 而非 4b？4b 的 think:false 是 bug，連「1+1」都思考 1000+ token

---

### M3: 資料攝入與清洗 (ingest)

| 項目 | 內容 |
|------|------|
| **目標** | 法規／判決的蒐集、清洗、切分、metadata |
| **擁有者 Agent** | `ingest` |
| **架構書** | `ingest/laws/DESIGN.md`、`ingest/cases/DESIGN.md` |
| **邊界（不負責）** | 不改檢索邏輯與 UI |
| **核心檔案** | `ingest/laws/pg_load.py` / `pg_schema.sql` / `sync_daily.py`（另有 `normalize.py` / `eda.py` / `qdrant_load.py` / `sparse.py`） |
| **資料流** | 官方 zip → `ChLaw.json` → PG 關鍵字表 + Qdrant 向量索引 |
| **驗收指令** | Qdrant `laws` collection 點數檢查 ✓ / `data/laws/.law_version` 更新 ✓ / PG `article` 表行數 |

**不變量**：
- 切分塊大小必須與檢索配合（不能亂切會導致上下文丟失）
- 版本號必須取 `ChLaw.json` 內的 `UpdateDate`（不是 zip 檔名，檔名永遠固定）
- 容器內 `data/laws` 是唯讀掛載，**寫入一律在 host 端由 cron 進行**

**常見陷阱**：
- `SRC_API_URL` 是 per-machine 必要設定，但三台 8000 全綁 127.0.0.1，腳本無法遠端取版本
- Ingest 容器跑不了（Python subprocess 限制），只能在 host 端 `law-update-worker.sh` 跑
- 精簡包還沒完成（離線可用資料量遠低於 500~1000 筆的目標）

---

### M4: 前端與 Worker (frontend)

| 項目 | 內容 |
|------|------|
| **目標** | UI 與 Cloudflare worker；只打後端相對路徑 `/api/*` |
| **擁有者 Agent** | `frontend` |
| **架構書** | `frontend/DESIGN.md` |
| **邊界（不負責）** | 不直連 Ollama / Qdrant / pg |
| **核心檔案** | `src/routes/api/[...path]/+server.ts`（worker）/ `auth/`（OAuth2）/ 各頁面 .svelte |
| **框架** | SvelteKit + Cloudflare Pages |
| **驗收指令** | `pnpm run dev` ✓ 連線轉發正常 / 登入流程通過 / 模型下拉列表完整 |

**不變量**：
- Worker `/api/*` 轉發必須 guard Google 登入（query/ingest/eval/rules 四個端點）
- 不帶 token 給後端（後端已靠 Google 登入 guard + port 收斂）
- 模型下拉必須即時顯示 free 額度、用量統計、限流標記

**常見陷阱**：
- 前端自製下拉（HTML `<select>` 無法對內部子字串著色）
- `SENSITIVE` routes 需登入（`/query` 從 2026-09-24 起必須登入）
- CF Pages 環境變數需在 Dashboard 設定，不是 `.env` 檔案

---

### M5: 評測與驗證 (eval)

| 項目 | 內容 |
|------|------|
| **目標** | 引註命中率；不通過不准進 UI 階段 |
| **擁有者 Agent** | `eval` |
| **架構書** | `evals/README.md` |
| **邊界（不負責）** | 只讀不寫碼 |
| **核心檔案** | `evals/questions.json` / 驗證腳本 |
| **門檻** | `POST /eval` hit_rate ≥ 0.8（否則回傳待修 flag，不進 UI） |
| **驗收指令** | `curl -X POST http://localhost:8000/eval` hit_rate ✓ |

**不變量**：
- 題庫數量足夠（現況 14 題計分／19 題總數，需擴到 50+ 題；且只覆蓋 3 部法）
- JEV（TypeSafe System One）驗證信心閘門需設定（`JEV_VERIFY_MIN`/`JEV_BANK_MIN`）

**常見陷阱**：
- ⚠️ **`RERANK_MODEL` 沒有消費者**（2026-09-27 更正）：原寫「reranker 還是 stub」。
  `rerank()`（`rag.py:1044-1057`）已實作；未做的是接 cross-encoder 模型 ——
  `RERANK_MODEL`（`rag.py:42`）全 backend 只出現在自己那一行定義。
  它卻被 `env-sync.sh` 當 `SHARED_CONFIG` 分發、被 `env-audit.py` 豁免 → M5 見 `evals/README.md` §7 末

---

### M6: 運維與部署 (ops)

| 項目 | 內容 |
|------|------|
| **目標** | 三機部署、runbook、registry 心跳、版本一致性 |
| **擁有者 Agent** | `ops` |
| **架構書** | `scripts/DESIGN.md`、`HOST-UPGRADE.md` |
| **邊界（不負責）** | 不改檢索邏輯、不決定憑證值 |
| **核心檔案** | `compose.yaml` / `scripts/sync-snapshot.sh` / `scripts/env-sync.sh` / `HOST-UPGRADE.md` |
| **主要指令** | `docker compose up -d` / `bash scripts/sync-snapshot.sh` / `scripts/env-sync.sh pull` |
| **驗收指令** | 三台 `curl /health` ✓ / `sync-snapshot.sh` 成功 ✓ / 快照點數一致 |

**不變量**：
- Commit message 必須帶 `msi:`/`mbp:`/`x570:` 前綴（`.githooks/commit-msg` 強制）
- Port 綁定規則嚴格：qdrant/pg 綁 tailscale IP，api 綁 127.0.0.1
- 快照同步是**唯一真相**（x570 離線時靠快照讓 mbp/msi 獨立作業）

**常見陷阱**：
- x570 / mbp 升級時先讀 `HOST-UPGRADE.md`（per-host 待辦清單）
- `compose.yaml` 用 `${TS_IP}` 變數，各機 .env 帶各自 tailscale IP 自動對應
- `QDRANT_URLS` 在容器內實際用不到（compose 只傳 `QDRANT_URL`），降級靠快照而非 live 探測

---

## 模組契約（每份架構書必答 5 題）

每個子模組的架構書都必須包含：

1. **目標** — 一句話，以及「做到什麼算完成」
2. **邊界** — 這個模組**不**負責什麼（避免相鄰模組互相改）
3. **不變量** — 壞了會出事、但測試不一定抓得到的規則
4. **陷阱** — 實測踩過、文件沒寫就會再踩一次的坑
5. **驗收** — 這個模組改完後跑哪條指令算過

---

## 開發工作流

### 第一階段：確認 Scope（同時最多一個進行中）

1. 從下表選一個待辦項目
2. 在 `SCOPE.md` 登錄（格式：`[M#] <模組> <簡述> | <agent> | <預計完成日>`）
3. 推送前檢查不進其他模組的檔案

### 第二階段：編寫架構書

進入 `<模組>/DESIGN.md`（或該模組自己的架構檔），按模組契約 5 點覆蓋。

### 第三階段：實作 → 驗收

按 M#-表的「驗收指令」跑測試，通過後提交。

### 第四階段：三機同步

```bash
git push                           # 觸發 CI：guards + backend + 單檔大小檢查
bash scripts/env-sync.sh pull      # 各機拉最新環境變數
docker compose pull && docker compose up -d   # 各機重啟容器
```

---

## 三機狀況速查（2026-09-26 現況）

| 機器 | Tailscale IP | 角色 | LLM | 容器化？ | 狀態 |
|------|--------------|------|-----|---------|------|
| **x570** | 100.119.83.111 | 主力（資料唯一來源） | qwen3:14b | ✓ docker compose | 完成 |
| **mbp** | 100.64.121.9 | 加速（快照備援） | qwen3:14b | ✓ OrbStack | 完成 |
| **msi** | 100.65.68.106 | demo（快照備援） | qwen3:8b | ✓ Docker Desktop | 完成 |

---

## 待辦清單（優先序）

### 高優先（影響 UI 驗收）

- [ ] **M3** 精簡包完成（目標 500~1000 筆法規）
- [ ] **M5** questions.json 擴 50 題（現況 14 題計分題／19 題總數）
- [ ] **M2** LLM 雲端路由完整驗證（已實作，待實測全 8 provider）

> ⚠️ **原第一條「M3 rerank() 實作（目前 stub）」已於 2026-09-27 刪除 —— 那是過期資訊。**
> `rerank()`（`backend/app/rag.py:1044-1057`）**已實作**且是真正的本地重排
> （條號精準分支領先 ＋ dense 語意相似度降序）。
> 真正沒做的是**接上 cross-encoder 模型**：`RERANK_MODEL`（`rag.py:42`）已宣告，
> 但全 backend 只出現在它自己那一行定義、**沒有任何消費者**。
> 這歸 M2／M3 交界，已記入 `SCOPE.md` 佇列代號 `H`。

### 中優先（運維與資安）

- [ ] **M6** x570 確認 ingest 排程（2026-09-26 未跑過首輪）
- [ ] **M1** x570 補齊環境變數（POSTGRES_PEER_PASSWORD 取值）
- [ ] **M6** 輪換外洩的 qdrant key（x570 已開，其他兩台待 pull）
- [ ] **M6** mbp launchd worker 補身份環境變數（SRC_API_URL）

### 低優先（功能擴展）

- [ ] **M6** mbp/msi 離線 `/hosts` → 另建 pg 副本（目前依賴 x570 registry）
- [ ] **M2** 個資去識別化迴圈
- [ ] **M2** OPENROUTER 真餘額顯示（需 management key）

---

## 升級路徑（不知道怎麼辦時）

1. **先讀該模組的架構書**（上表第 4 欄）
2. **架構書答不了 → 讀本檔對應章 ＋ `SCOPE.md` 當前 scope 驗收條款**
3. **還是答不了 → 停下來問使用者**

⚠️ **為什麼不能猜**：三台共用同一份 repo，猜出來的設定會被另外兩台的 `env-sync pull` 自動分發 → 症狀是「那台機器莫名其妙壞掉」，最貴的時候是幾天後才發現。

---

## 提交準則（三台嚴格執行）

### Commit Message 格式

```
msi: <模組簡稱> <簡述>
mbp: backend rag.py 修復 gemini 2.5 key 判斷
x570: env-ops 補齊 POSTGRES_PEER_PASSWORD
```

**Merge/Revert 自動放行**；`.githooks/commit-msg` 強制每機執行一次：

```bash
git config core.hooksPath .githooks
```

### CI 層（GitHub Actions）

| 檢查項 | 本機 hook | GitHub CI |
|--------|----------|-----------|
| Commit 前綴 | 擋 | 只回報（企業帳號限定） |
| Python 語法 + LAN_IP 檢查 | 擋 | 全 push 都跑 |
| 單檔 ≤ 10 MB | — | 只回報（公開 repo 限制） |
| 禁刪 main | — | **真的擋**（唯一伺服器端擋得住） |

---

## 快速查閱

### 新增模組檔案

```
新模組/
├── DESIGN.md          ← 架構書（模組契約 5 點必填）
└── ...
```

> ⚠️ 原寫「`__init__.py` ← 模組根簽名」。實際 repo 只有 `backend/app/__init__.py`，
> `ingest/laws/` 與 `ingest/cases/` **都沒有**（靠隱式命名空間套件運作）。
> 這條慣例沒有落實，別當成既有規範引用。

### 環境變數檢查

```bash
python3 scripts/env-audit.py                    # 列消費者、找幽靈變數
scripts/env-sync.sh --check                     # 驗 schema 完整性
scripts/env-sync.sh render                      # 預覽本機展開結果
```

### Docker 操作（三機統一）

```bash
# 首次或改 api 碼
docker compose up -d --build

# 只改環境變數或佈置文件
docker compose up -d

# 查看容器狀態
docker compose ps
docker compose logs -f api

# 進容器除錯
docker compose exec api bash
```

### 快照同步

```bash
# 手動同步（用於外出 demo 補最新版本）
bash scripts/sync-snapshot.sh

# 檢查同步狀態
tail -f ~/qdrant/sync.log
cat ~/qdrant/.sync-state
```

---

## 相關檔案索引

- 完整架構：`ARCHITECTURE.md`（2026-09-27，本檔基礎）—— **權威版，規則加在這裡**
- 當前 scope：`SCOPE.md`（開發進度追蹤）
- 升級 runbook：`HOST-UPGRADE.md`（x570/mbp 更新前必讀）
- 交割清單：`X570-HANDOFF.md`（x570 待確認事項）
- 路線圖：`ROADMAP.md`（長期規劃，跨機遷移等）

---

## 與 repo 現況的差異（2026-09-27 逐項查證後修正）

本檔從 `files/` 收進 `docs/` 時，以下 6 處**與 repo 現況不符**，已就地修正。
保留這份清單是為了讓「當初為什麼這樣寫」可追，也避免有人從舊版誤抄。

| # | 原寫 | 實際 | 證據 |
|---|---|---|---|
| 1 | `backend/app/rules.py` | `backend/app/rules_store.py` | `ls backend/app/rules.py` → 不存在 |
| 2 | `pytest backend/tests/` | `pytest -q`（測試在 repo 根 `tests/`） | `backend/tests/` 不存在；`tests/` 12 檔 165 條全過 |
| 3 | PG `laws_keywords` 表 | 只有 `law` / `article` / `law_import` | `grep 'CREATE TABLE' ingest/laws/pg_schema.sql` |
| 4 | `M3 rerank() 實作（目前 stub）` 為高優先待辦 | `rerank()` 已實作；未做的是接 cross-encoder | `rag.py:1044-1057`；`RERANK_MODEL` 只在 `rag.py:42` 出現 |
| 5 | M6 `include` 含 `scripts/env-sync.sh` | 與 M1 重複認領 → 劃給 M1 | 雙重所有權＝第二真相；`SCOPE.md` 的 scope A/B/C 都是 M1 |
| 6 | 模組慣例含 `__init__.py` | 只有 `backend/app/` 有，其餘沒有 | `find ingest -name '__init__.py'` → 空 |

另修正（非錯誤、但會誤導）：M2 漏列 `law_struct.py`／`sparse.py`；
M3 漏列 `normalize.py`／`eda.py`／`qdrant_load.py`／`sparse.py`；
M1 漏列 `common.env`／`secrets.common.*`／`.sops.yaml`；
M4 路徑 `src/routes/…` → `frontend/src/routes/…`；
M5「~14 題」→ 19 題（14 計分 ＋ 5 負面 ＋ 0 案件）。

**模組契約 5 點的落實狀態（2026-09-27）**：6 份架構書全部齊備 ——
M1 `settings/env/README.md`、M2 `backend/DESIGN.md`、
M3 `ingest/laws/DESIGN.md` ＋ `ingest/cases/DESIGN.md`、
M4 `frontend/DESIGN.md`、M5 `evals/README.md`、M6 `scripts/DESIGN.md`。
（`scripts/DESIGN.md` 的〈驗收〉是 §6 不是 §5，因多一節〈現有腳本〉；
五個問題都有答案，只是編號順序略有不同。）

---

**最後編輯**：2026-09-27 | **格式**：可直接複製給 opencode agent 或轉為 JSON/YAML
