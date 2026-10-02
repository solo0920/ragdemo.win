# backend 模組架構書（M2）

> **擁有者：`backend` agent。** 本檔骨架由 `ARCHITECTURE.md`〈架構表〉建立，
> 內容由 owner 逐題補齊 —— 補不完的題目代表該處有沒被記錄下來的知識，
> 應該在下次動到時補上，而不是先留空。

## 1. 目標

檢索本體：路由、模型選擇與降級鏈、registry、rules 儲存。
做到什麼算完成：`pytest -q` 全過 ＋ `GET /health` 回 200。

## 2. 邊界（不負責）

- 不動 `ingest/`（資料層）
- 不動 `frontend/src/`
- 環境變數的**分層與分發**歸 M1 `env-ops`；本模組只宣告「哪個變數必須傳進容器」
- 部署、runbook、心跳歸 M6 `ops`

## 3. 不變量

- 不可寫死 IP／主機名／憑證，一律讀環境變數
- `os.getenv("VAR", 預設)` 與 `compose.yaml` 的 `${VAR:-預設}` 必須**逐字一致**
- 追蹤檔不得出現 `^LAN_IP=`（一律 tailscale 100.64.0.0/10）
- `docker compose config` 會展開憑證，絕不可貼其輸出

## 4. 陷阱（實測踩過）

- **「程式讀得到」≠「容器拿得到」**：compose 沒列 `environment:` 的變數在容器裡
  fallback 到原始碼預設且**不報錯**。判斷：`python3 scripts/env-audit.py`
  看「沒傳入容器」區段。
  - 2026-09-27 實測 19 個（`gateway.py`／`registry.py` 有讀、compose 沒列）。
  - **已於 scope B 修掉 15 個**（補進 `compose.yaml` 的 api `environment:`，
    預設值逐字抄原始碼 `os.getenv` 第二個參數）。
  - **剩下 4 個是有理由的**：刻意不傳的那 3 個（見本節末）＋ 政策性停用的
    `LAN_IP`（ARCHITECTURE.md〈IP 準則〉，追蹤檔不得出現 `LAN_IP=`）。
  - 所以 `env-audit` 現在報出的「沒傳入容器」**只應該**是那 3 個刻意的
    （實際只列 `.env` 裡真的有設值的，所以常常只看得到 `QDRANT_URLS`）。
    出現第 4 個就是 scope B 被回退、或有人加了新旋鈕忘了補 compose。
- **`_ollama_probe`（`gateway.py`）檢查模型是否真的存在**（`need <= have`），
  所以 `OLLAMA_MODELS` 進不了容器時症狀是「安靜地永遠挑最差的候選」，不是報錯。
- `OLLAMA_MODELS` 與 `OLLAMA_URLS` 位置對應（`gateway.py _llm_model_for()` 用 index 取值）。
  這也是為什麼 compose 裡 `OLLAMA_MODELS` 的預設值是**巢狀**的
  `${OLLAMA_MODELS:-${LLM_MODEL:-qwen3:14b}}`：`gateway.py` 的
  `os.getenv("OLLAMA_MODELS", LLM_MODEL)` 第二個參數是 `LLM_MODEL` 這個
  **變數本身**、不是字面值。抄成 `qwen3:14b` 會是靜默劣化 ——
  MSI（`LLM_MODEL=qwen3:8b`）三台 ollama 會全被要求 14b 而 probe 全滅，
  比修之前更糟。巢狀寫法的好處是「沒設 `OLLAMA_MODELS` 的機器行為完全不變」
  （2026-09-27 實測：`LLM_MODEL=qwen3:8b` → `OLLAMA_MODELS=qwen3:8b`）。
- 抓遠端 `/status` 必須帶 `probe=0`，否則請求數指數成長。
- Docker Desktop（WSL）掛「單一檔案」型 bind mount 會讓容器 init exit=127；
  `/etc/hostname`、`/etc/machine-id` 尤其。改走環境變數（`HOST_NAME`／`HOST_MACHINE_ID`）。

### 刻意不傳入容器的 3 個變數（scope B，2026-09-27 定案）

`env-audit` 會一直把下面這 3 個列進「沒傳入容器」。**那是設計，不是漏掉。**
下次有人「好心」補回去之前，先讀這裡。

| 變數 | 為什麼刻意不傳 |
|---|---|
| `QDRANT_URLS` | 容器化的降級模型是**快照同步**（`scripts/sync-snapshot.sh` 每 10 分鐘把 x570 的快照推到 mbp/msi），不是讓 mbp/msi 的容器 live 去讀 x570。傳進去會讓 mbp/msi 的容器**依賴 x570 在線**，與快照模型的初衷相反（x570 掛了要能繼續服務）。而且 `QDRANT_URLS` 的 peer 就是**別台的 qdrant**，那會把 `QDRANT_API_KEY` 逼成三台同值 —— 正是 scope `A` 正在拆掉的鎖步輪換。容器一律走 `QDRANT_URL=http://qdrant:6333`。 |
| `HOST_MACHINE_ID_FILE` | `registry.py:20` 把它當「主機檔案」的 fallback 路徑（預設 `/run/secrets/host-machine-id`）。Docker Desktop 掛單一檔案不可靠（見上），所以設計上就走 `HOST_MACHINE_ID` 環境變數（`registry.py:27`，值優先於檔案）。這個 fallback 只在**原生執行**（host 端直接 `uvicorn`）時才有意義；傳進容器等於宣告「容器裡有那個檔案」而實際沒有。 |
| `HOST_HOSTNAME_FILE` | 同上（`registry.py:21` ＋ `registry.py:28`）。 |

已補上的 15 個（補在 `compose.yaml` 的 api `environment:`，每個旁邊都有
「原本讀得到但容器拿不到，2026-09-27 補上」的註解）：
`CF_AIG_GATEWAY_ID`、`HOST_API_URLS`、
`JEV_DISABLED`、`JEV_MODEL`、`OLLAMA_MODELS`、`PICK_TTL`、`PROBE_TIMEOUT`、
`RAG_HIGH_DENSE`、`RAG_MID_DENSE`、`RAG_MIN_DENSE`、`REGISTRY_HEARTBEAT`、
`REGISTRY_STALE_MIN`、`TYPESAFE_URL`。
`tests/test_env_audit.py` 有斷言把「15 個必須有、3 個必須沒有」鎖住。

> 2026-09-27：`HOST_API_MBP`／`HOST_API_MSI`／`HOST_API_X570` 三個已合併成
> `HOST_API_URLS=x570=網址,msi=網址`。舊的三變數是「機台名寫進變數名」，
> 第 4 台要改 compose 並重新 build；而且**不保留**舊鍵的相容 fallback ——
> 留著就等於要求 env-audit 永遠維持一個沒有使用者的機制。刪除不是靜默的：
> `env-audit.py` 的 `REMOVED_KEYS` 會把殘留的舊鍵報成幽靈並附遷移指引。

## 5. pg 裡的兩張表：peer 自我回報 vs 本機設定

### `backends`（registry.py）—— 由心跳**完全接管**

| 欄位 | 意義 |
|---|---|
| `host_id` | PK。每列＝一個活著的 peer，**由心跳 INSERT 建立** |
| `hostname`／`machine_id`／`mac`／`ips`／`ts_ip`／`lan_ip` | 自我回報的身分（`ips` 只留 tailscale IP） |
| `models` | JSONB，`rag.local_models()` 回的 ollama 模型清單 |
| `llm` | **心跳當下的 `LLM_MODEL`**，不是使用者的設定 —— 每 `REGISTRY_HEARTBEAT` 秒被覆寫 |
| `last_seen`／`ok` | 心跳時間；`DELETE … last_seen < now() - STALE_MIN 分` 讓離線的自動消失 |

`heartbeat()` 是 `INSERT … ON CONFLICT (host_id) DO UPDATE`，**除 `host_id` 外每一列都被
EXCLUDED 覆寫**。所以這張表裡沒有任何欄位能存放「本機設定」。

### `host_settings`（host_settings.py，2026-10-02）—— 只由使用者寫入

`GET`／`PUT /settings/default-model` —— 網頁上設定「**這台**用哪個 model 當預設」，
存本機 pg，**取代該機的 `LLM_MODEL`** 成為查詢時的實際預設。

```sql
host_settings(host_id TEXT PK, default_model TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ)
```

回應形狀固定四欄（前端契約，`envelope()` 產生，GET 與 PUT 共用）：
`{ok, host, model, effective}`。`model`＝存的設定（`null`＝未設定）、
`effective`＝`model or gateway.LLM_MODEL`。

### 為什麼不存進 `backends.llm`

`backends` 已有 `llm TEXT`，但它**每 `REGISTRY_HEARTBEAT` 秒被心跳覆寫**
（`registry.heartbeat()` 的 `llm=EXCLUDED.llm`）。症狀是「存進去、GET 看得到、
下一次心跳洗掉、沒有任何錯誤」。獨立表的理由是語意：`backends` 的每列＝
**一個活著的 peer**，由心跳建立、也只由心跳更新；`default_model` 是本機的使用者
設定，不該要求那台先心跳過才有地方放。獨立表讓「心跳絕不會碰到它」**結構上成立**，
`tests/test_default_model.py` 從 SQL 層與原始碼層各釘一條。

### 查詢時的優先權

    /query 的 model 欄位 → 存的 default_model → 未指定（= `gateway._llm_model_for(選中的 ollama)`）

第三段刻意是「未指定」而不是 `LLM_MODEL`：空字串讓 `rag.generate()` 走
`_llm_model_for(base)`（該台 ollama 對應的模型）。多台 ollama 配 `OLLAMA_MODELS` 時
`LLM_MODEL` **不等於**它，填進去是靜默劣化。

### 快取與降級

- `TTL=10s` 快取；寫入成功後**立刻**更新快取（不必等 TTL）。
- 讀不到 DB **不拋**：有上次已知的值就用它，沒有才回 `""`（→ `LLM_MODEL`）。
  `invalidate()` 刻意「讓快取過期但保留值」，降級才不會讓使用者剛設的東西消失。
- 降級結果只快取 `TTL_FAIL=5s`（比 TTL 短）：否則 pg 掛掉時每個查詢都要等一次
  connect timeout。
- **寫入端不降級**（PUT 失敗回 503），只有讀取端降級 —— 寫入失敗還回 200 是說謊。

### 三個刻意的選擇

- **不新增環境變數**（TTL 是模組常數）。多一個 `os.getenv` 就多一份
  「程式讀得到、容器拿不到」的風險，還得同時補 `compose.yaml` 與 `.env.example`。
  `tests/test_default_model.py` 有一條測試擋住「順手加個 knob」。
- **`host` 只出現在回應、不在 request body**。三台各有自己的 pg，跨機寫入要一套
  「遠端寫入授權」，而需求不需要 —— 前端用 `HOST_API_URLS` 直接問／寫每一台。
  測試從 AST 釘住 PUT 的 body 只有 `model`。
- **不掛 `ADMIN_TOKEN`**（與 `/rules` 的寫入不同）：這是「自己那台的預設」，
  對外路徑已由 worker 登入 guard ＋ Cloudflare Access 收著。

⚠️ 動 `main.py` 會讓 `scripts/env-audit.py --template` 產生的
`.env.example`／`settings/env/ENV-VARIABLE-INVENTORY.md` 裡的**行號**失效
（那兩個檔記錄 `誰讀` 的 `file:line`）。改完要重新產生，否則
`tests/test_env_audit.py::test_template_has_no_lan_ip_assignment` 會紅。

## 6. 驗收

```bash
pytest -q
python3 scripts/env-audit.py          # 不是 --quiet（那個會跳過根 .env 的稽核）
docker compose config -q              # 只驗語法，不會印出憑證
curl -s localhost:8000/health
curl -s localhost:8000/settings/default-model
curl -s localhost:8000/query -H 'content-type: application/json' \
  -d '{"question":"契約解除後雙方有何回復原狀義務？"}'   # 確認 RAG 真的通（/health 不夠）
```

⚠️ **`/health` 抓不到很多東西。** 2026-10-02 刪掉一個環境變數之後 `/health`
一直是 200，是後來手動打 `POST /query` 才發現 RAG 已經斷了。ollama 相關尤其如此
（見上節 `_ollama_probe` 的 `need <= have` 靜默劣化）。改檢索／模型相關的東西
一定要真的打一次 `/query`。

## 待補（owner）

- 路由一覽（每個 endpoint 的用途、認證要求）
- rules（題庫）在 pg 與 `data/rules` 的分工
- 降級鏈的完整決策流程圖（ollama / qdrant / pg 三條鏈各自的候選與快取）
- **（scope D，不在本模組做）** 把本節〈刻意不傳入容器的 3 個變數〉升級成
  `settings/env/manifest.tsv` 的顯式欄位（例如 `forward=always｜never｜host-only`
  ＋ 理由欄）。現在這條「不傳」的理由只存在於本檔與 `tests/test_env_audit.py`
  的斷言裡 —— 換成 manifest 之後，`env-audit` 就能把「刻意不傳」與
  「忘了傳」分開報，而不是兩者都長得一模一樣。
