---
description: FastAPI 後端與 Qdrant/PG，只動 backend 與 compose
mode: subagent
---

負責 backend/app、Dockerfile、compose.yaml。模型與服務位址一律讀環境變數，
不可寫死 IP。改完跑 POST /health 驗證。
