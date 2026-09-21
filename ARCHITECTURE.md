# RagDemo 架構（維護用）

```
compose.yaml      # qdrant + postgres + api
backend/          # FastAPI：/health /ingest /query /eval
frontend/         # SvelteKit：只打 /api/*
evals/            # 評測題庫，目標 50 題
.opencode/agents/ # ingest / backend / frontend / eval 四個子代理
```

## 三機分工
* Linux .99：主力，`OLLAMA_BASE_URL=http://192.168.0.99:11434`，`LLM_MODEL=qwen3:14b`
* mbp .93.85：加速，`OLLAMA_BASE_URL=http://192.168.93.85:11434`，`LLM_MODEL=qwen3-coder:latest`
* MSI 本機 demo：`OLLAMA_BASE_URL=http://127.0.0.1:11434`，`LLM_MODEL=qwen3.5:4b`，資料用精簡包

## 模型清單（2026-09-21 實測後）
* Linux：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3:14b`、`qwen3-coder:latest`
* mbp：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3-coder:latest`、`qwen3-coder-next:latest`、`qwen3:14b`
* MSI：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen2.5-coder:7b`、`qwen3.5:4b`

## 實測速度（eval tok/s，同 prompt num_predict=200）
* Linux coder 94.8 ＞ mbp coder 73 ＞ MSI 4b 79 ＞ Linux 14b 熱機 82（冷機 19，待再驗）＞ mbp 14b 25
* 結論：重推理放 Linux，日常寫碼可用 mbp coder，MSI 只跑輕量

## 服務埠
* 8000 api / 6333 qdrant / 5432 postgres / 5173 前端 dev / 11434 ollama

## 環境變數（backend/.env）
`OLLAMA_BASE_URL`、`LLM_MODEL`、`EMBED_MODEL=bge-m3:latest`、
`RERANK_MODEL=qllama/bge-reranker-v2-m3:latest`、`COLLECTION=laws`、`QDRANT_URL`、`POSTGRES_DSN`

## 評測門檻
`POST /eval` hit_rate 未達 0.8 不進 UI，先修切分/召回。

## TODO
* `rag.py rerank()` 還是 stub，待接真正 reranker 打分
* `evals/questions.json` 佔位 3 題，待擴 50 題
* 判決注意個資去識別化，回答僅供參考非法律意見

## 啟動
```bash
cp backend/.env.example backend/.env  # 再改 OLLAMA_BASE_URL
docker compose up -d --build  # 首次建 api 映像，之後改碼重跑加 --build
curl localhost:8000/health
cd frontend && npm install && npm run dev
```

## Demo 精簡包
Linux 全量 → `Qdrant snapshot + PG dump` → MSI 匯入精選 500~1000 筆，離線可跑。
