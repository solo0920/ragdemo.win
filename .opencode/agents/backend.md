---
description: FastAPI 後端與 Qdrant/PG；動 backend/ 與 compose.yaml；含「變數有傳進容器嗎」的可達性
mode: subagent
---

負責 `backend/app/`、Dockerfile、`compose.yaml`。模型與服務位址一律讀環境變數，
不可寫死 IP。改完跑 `GET /health` 驗證。

## 模組契約（M2 backend，見 ARCHITECTURE.md〈架構表〉）

1. **目標**：檢索本體 —— 路由、模型選擇與降級鏈、registry、rules 儲存。
   做到什麼算完成：`pytest -q`（含 `tests/test_env_audit.py`）＋ `GET /health` 回 200。
2. **邊界**：不動 `ingest/`、不動 `frontend/src/`。
   環境變數的**分層與分發**是 M1 `env-ops` 的事（本模組只負責宣告
   「這個變數必須傳進容器」）；部署與 runbook 是 M6 `ops` 的事。
3. **不變量**：
   - 不可寫死 IP／主機名／憑證，一律讀環境變數。
   - `os.getenv("VAR", 預設)` 的預設值與 `compose.yaml` 的 `${VAR:-預設}` **必須逐字一致**
     —— 不一致時 `.env` 沒設的那台會安靜地用錯的值。
   - 追蹤檔不得出現 `^LAN_IP=`。
   - `docker compose config` 會展開所有憑證，**絕不可把輸出貼進對話**（`config -q` 只驗語法，安全）。
4. **陷阱**：
   - **「程式讀得到」不等於「容器拿得到」**：compose 沒列 `environment:` 的變數，
     在容器裡會 fallback 到原始碼預設，而且**不報錯**。2026-09-27 實測 19 個變數
     是這種狀態，其中 `OLLAMA_MODELS` 讓 MSI 永遠降級成用自己的 8b。
     判斷工具：`python3 scripts/env-audit.py`（看「沒傳入容器」區段）。
   - 改 `compose.yaml` 的 `environment:` 後必須重新產生 `.env.example`：
     `python3 scripts/env-audit.py --template > .env.example`（由本模組擁有，
     `env-ops` 不要動它；`tests/test_env_audit.py` 會逐字比對）。
5. **驗收**：`pytest -q`、`python3 scripts/env-audit.py`、`docker compose config -q`、
   `GET /health` 回 200。

## 降級鏈不可破的兩條（2026-09-27 查證）

- `rag.py:290` 的 `_ollama_probe` 會檢查**該機對應的 LLM model 是否真的存在**
  （`need = {_llm_model_for(url), EMBED_MODEL} <= have`）。所以 `OLLAMA_MODELS`
  進不了容器時，症狀不是報錯而是**安靜地永遠挑最差的候選**。
- `OLLAMA_MODELS` 與 `OLLAMA_URLS` **位置對應**（`rag.py:215` 用 index 取值），
  長度不一致只會讓某台 ollama 拿到別台的模型。改其一必改其二。

## 不知道怎麼辦時（三段升級，不准跳）

1. 先讀 `backend/DESIGN.md`（本模組架構書）與本檔
2. 答不了 → 讀 `ARCHITECTURE.md`〈LLM 雲端路由〉〈備援機制〉 ＋ `SCOPE.md` 當前 scope 的驗收條款
3. 還是答不了 → **停下來問使用者**。不要猜、不要拿未查證的值頂替。
