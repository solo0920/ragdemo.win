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

## 啟動
```bash
cp backend/.env.example backend/.env  # 再改 OLLAMA_BASE_URL
docker compose up -d
curl localhost:8000/health
cd frontend && npm install && npm run dev
```

## Demo 精簡包
Linux 全量 → `Qdrant snapshot + PG dump` → MSI 匯入精選 500~1000 筆，離線可跑。
