# RagDemo 架構（維護用）

```
compose.yaml      # qdrant + postgres + api
backend/          # FastAPI：/health /ingest /query /eval
frontend/         # SvelteKit：只打 /api/*
evals/            # 評測題庫，目標 50 題
.opencode/agents/ # ingest / backend / frontend / eval 四個子代理
```

## 三機分工
* Linux .99：主力，`OLLAMA_BASE_URL=http://192.168.0.99:11434`，`LLM_MODEL=qwen3:14b`，api/qdrant/pg 全跑
* mbp .93.85：加速，`OLLAMA_BASE_URL=http://192.168.93.85:11434`，`LLM_MODEL=qwen3-coder:latest`
* MSI .0.2（demo）：api 在 WSL2 裡（`uvicorn --env-file .env`，開機自動啟動），
  `OLLAMA_URLS=http://192.168.0.2:11434`（直連 Windows 本機 ollama），`LLM_MODEL=qwen3:4b`，
  資料層（Qdrant/pg）暫指 Linux

## 模型清單（2026-09-21 實測後）
* Linux：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3:14b`、`qwen3-coder:latest`
* mbp：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3-coder:latest`、`qwen3-coder-next:latest`、`qwen3:14b`
* MSI：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3:4b`、`qwen2.5-coder:7b`（qwen3.5:4b 已棄用）

## 實測速度（eval tok/s，同 prompt num_predict=200）
* Linux coder 94.8 ＞ mbp coder 73 ＞ MSI 4b 79 ＞ Linux 14b 熱機 82（冷機 19，待再驗）＞ mbp 14b 25
* 結論：重推理放 Linux，日常寫碼可用 mbp coder，MSI 只跑輕量

## 服務埠
* 8000 api（Linux docker compose＋MSI WSL 各一） / 6333 qdrant（Linux）/ 5432 postgres（Linux） / 5173 前端 dev / 11434 ollama（各機 native）

## 環境變數（backend/.env，各機一份不進版控）
`OLLAMA_BASE_URL`（單機版）/ `OLLAMA_URLS`（候選清單）、`LLM_MODEL`、`EMBED_MODEL=bge-m3:latest`、
`RERANK_MODEL=qllama/bge-reranker-v2-m3:latest`、`COLLECTION=laws`、`QDRANT_URLS`、`POSTGRES_DSN`、`HOST_ID`

## 評測門檻
`POST /eval` hit_rate 未達 0.8 不進 UI，先修切分/召回。

## TODO
* `rag.py rerank()` 還是 stub，待接真正 reranker 打分
* `evals/questions.json` 佔位 3 題，待擴 50 題
* 判決注意個資去識別化，回答僅供參考非法律意見

## 啟動
**Linux（docker compose）**：
```bash
cp backend/.env.example backend/.env  # 再改 OLLAMA_BASE_URL
docker compose up -d --build  # 首次建 api 映像，之後改碼重跑加 --build
curl localhost:8000/health
cd frontend && npm install && npm run dev
```
**MSI（WSL2，吃 Windows 本機 ollama）**：
```bash
bash backend/start-msi.sh        # 冪等：已啟動就跳過（開機自動啟動見 ROADMAP §2.5）
curl localhost:8000/health       # 回 host_id=msi, llm=qwen3:4b
```
MSI 資料層（Qdrant/pg）目前仍指 Linux，離線接手靠精簡包（見下）。

## Demo 精簡包
Linux 全量 → `Qdrant snapshot + PG dump` → MSI 匯入精選 500~1000 筆，離線可跑。
