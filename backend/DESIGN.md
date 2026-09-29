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

## 5. 驗收

```bash
pytest -q
python3 scripts/env-audit.py          # 不是 --quiet（那個會跳過根 .env 的稽核）
docker compose config -q              # 只驗語法，不會印出憑證
curl -s localhost:8000/health
```

## 待補（owner）

- 路由一覽（每個 endpoint 的用途、認證要求）
- registry 的 `backends` 表 schema 與心跳語意
- rules（題庫）在 pg 與 `data/rules` 的分工
- 降級鏈的完整決策流程圖（ollama / qdrant / pg 三條鏈各自的候選與快取）
- **（scope D，不在本模組做）** 把本節〈刻意不傳入容器的 3 個變數〉升級成
  `settings/env/manifest.tsv` 的顯式欄位（例如 `forward=always｜never｜host-only`
  ＋ 理由欄）。現在這條「不傳」的理由只存在於本檔與 `tests/test_env_audit.py`
  的斷言裡 —— 換成 manifest 之後，`env-audit` 就能把「刻意不傳」與
  「忘了傳」分開報，而不是兩者都長得一模一樣。
