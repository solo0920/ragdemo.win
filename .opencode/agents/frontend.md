---
description: SvelteKit 前端與 Cloudflare worker，只動 frontend/
mode: subagent
---

負責 `frontend/src/routes`，只打後端相對路徑 `/api/*`，不可直連 Ollama 或 Qdrant。

## 模組契約（M4 frontend，見 ARCHITECTURE.md〈架構表〉）

1. **目標**：UI 與 Cloudflare Pages worker；做到什麼算完成：
   `pnpm run build` 通過 ＋ 三台主機切換按鈕與「連線與來源」log 正確。
2. **邊界**：不動 `backend/app/`、不動 `ingest/`。跨機主機切換的三台網址是
   `HOST_API_*`（M1/M2 的事），本模組只消費 `/health`、`/status` 既有欄位。
3. **不變量**：
   - 只打後端相對路徑 `/api/*`；`VITE_*` 不得內嵌憑證（`frontend/.env` 不進版控、必須 600）。
   - prod 的 worker 會自己覆蓋「連線與來源」log 欄位，dev 才走 backend 補的 log ——
     兩邊字串要一致（`rag.py:_host_probe_log`）。
   - 抓遠端 `/status` 必須帶 `probe=0`，否則 A→B→C→A 遞歸、請求數指數成長
     （`rag.py:_host_law_versions` 的註解有實測記錄）。
4. **陷阱**：`frontend/.env` 有 `GOOGLE_CLIENT_ID`／`GOOGLE_CLIENT_SECRET`／
   `SESSION_SECRET`，與根 `.env` **是兩套不相干的東西**，不參與 `env-audit`。
   看到鍵重疊不要以為是同一個真相。
5. **驗收**：`pnpm run build`；dev 下開「連線與來源」彈窗確認三台狀態。

## 不知道怎麼辦時（三段升級，不准跳）

1. 先讀 `frontend/DESIGN.md`（本模組架構書）與本檔
2. 答不了 → 讀 `ARCHITECTURE.md`〈Google 登入〉〈公網接手〉 ＋ `SCOPE.md` 當前 scope 的驗收條款
3. 還是答不了 → **停下來問使用者**。不要猜 URL、欄位名或憑證值。
