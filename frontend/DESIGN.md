# frontend 模組架構書（M4）

> **擁有者：`frontend` agent。** 本檔骨架由 `ARCHITECTURE.md`〈架構表〉建立，
> 內容由 owner 逐題補齊。

## 1. 目標

UI 與 Cloudflare Pages worker。做到什麼算完成：`pnpm run build` 通過
＋ 三台主機切換按鈕與「連線與來源」log 正確。

## 2. 邊界（不負責）

- 不動 `backend/app/`、不動 `ingest/`
- 跨機主機網址（`HOST_API_*`）歸 M1／M2；本模組只消費 `/health`、`/status` 既有欄位
- 部署到 Pages 與版本相容性歸 M6 `ops`

## 3. 不變量

- 只打後端相對路徑 `/api/*`；**不可直連 Ollama / Qdrant / pg**
- `VITE_*` 不得內嵌憑證；`frontend/.env` 不進版控、必須 `chmod 600`
- prod 的 worker 會自己覆蓋「連線與來源」log，dev 才走 backend 補的 log ——
  兩邊字串必須一致（`rag.py:_host_probe_log`）
- 抓遠端 `/status` 必須帶 `probe=0`

## 4. 陷阱（實測踩過）

- `frontend/.env`（`GOOGLE_CLIENT_ID`／`GOOGLE_CLIENT_SECRET`／`SESSION_SECRET`）
  與根 `.env` 是**兩套不相干的東西**，不參與 `env-audit`。看到鍵重疊不要以為同一真相。
- worker 在 Cloudflare edge 執行，沒有 `localhost`；直連後端要靠 Pages 的
  dev proxy 或已知的 tunnel 網址。
- mbp 是本機 proxy，dev 模式（vite proxy 直連 backend）與 prod 行為不同 ——
  「連線與來源」log 的來源在兩者之間不一致是正常的。

## 5. 驗收

```bash
cd frontend && pnpm run build
# dev：開「連線與來源」彈窗確認三台狀態與 law_version
```

## 待補（owner）

- 頁面與路由一覽（`/query`／`rules`／`hosts` 等各頁職責）
- worker 與 SvelteKit 的職責分界（哪些邏輯在 edge、哪些在 origin）
- 法規更新按鈕的觸發路徑（前端 → `/rules` 類 endpoint → host worker → `data/.ops/`）
