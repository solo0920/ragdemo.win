# scripts 模組架構書（M6 ops）

> **擁有者：`ops` agent。** 本檔骨架由 `ARCHITECTURE.md`〈架構表〉建立，
> 內容由 owner 逐題補齊。

## 1. 目標

讓「一台寫、另兩台照專案部署」是一條命令，而不是一個人照散文做 20 個步驟。
做到什麼算完成：`scripts/host-sync.sh` 在任一台跑完，該台 commit 版本、
三台 registry 心跳、`.env` 與 repo 的一致性全部對上。

## 2. 邊界（不負責）

- 不碰 `backend/app/`、`frontend/src/`、`ingest/`
- **不決定憑證值**（那是 M1 `env-ops`；本模組只搬運與驗證）
- 變數該不該被 `.env` 設定的分層判斷歸 M1

## 3. 不變量

- 部署腳本必須**冪等**：跑第二次不產生第二次效果、不覆蓋本機既有設定
- 診斷輸出**永不包含憑證值**（上限：鍵名＋長度＋sha256 前 12 碼）
- 部署失敗要在**該台**看得見（exit code ＋ 訊息），不靜默成功
- 絕不對含憑證的檔案跑 `bash -x`（`env-sync.sh` 在 xtrace 下會拒絕，是刻意設計）
- macOS 沒有 `shred`，刪解密暫存要 `shred || rm -f` 回退

## 4. 現況與陷阱

| 事實 | 影響 |
|---|---|
| `HOST-UPGRADE.md` 313 行 ＋ `X570-HANDOFF.md` 288 行 ＝ 601 行散文 | 這就是「開發完另外兩台又要自己改」的來源 |
| `scripts/` 下 0 個部署腳本 | 沒有可重複執行的部署路徑 |
| 全 repo 沒有任何 `git pull` | 升級完全靠人 |

- `POSTGRES_PASSWORD` 只對 pg volume **首次初始化**生效；之後改 `.env` 不更新 role，
  要 `ALTER USER rag PASSWORD`。
- qdrant / pg 的 `api-key` 與密碼是**啟動參數**，換值不重啟容器等於沒換。
- `docker compose config` 會展開所有憑證 → 絕不可貼輸出（`config -q` 只驗語法，安全）。
- 127.0.0.1 在 pg_hba 是 `trust`，會誤導成「密碼對」；要以 tailscale 來源測真正 credentials。
- `bash` 不支援 `${#VAR:-default}`（只有 zsh 支援）。

## 5. 現有腳本

| 腳本 | 用途 | 頻率 |
|---|---|---|
| `env-sync.sh` | 解密＋合併＋render（三機共用層） | 每次 `pull` |
| `env-audit.py` | 從程式碼反查 `.env` 的落差 | 手動／CI |
| `sync-snapshot.sh` | 從 x570 拉 qdrant 快照到本機 | 每 10 分鐘（crontab／launchd） |
| `law-update-worker.sh` | 執行每日 ingest（source 機跑 sync_daily） | 每日排程 |

## 6. 驗收

```bash
bash -n scripts/*.sh
scripts/env-sync.sh --check
docker compose config -q
python3 scripts/env-audit.py
```

## 待補（owner，scope F）

- `host-sync.sh`：冪等部署的單一入口（`git pull` → `env-sync pull` → `doctor` → `compose up -d --build`）
- `host-doctor.sh`：一次回答「哪台壞了、哪個鍵、要不要輪換」，永不印值
- `host-onboard.sh`：從零到能跑（`age` keygen、`HOST_ID`、per-host 機密、sops recipients）
- `HOST_ROLE=source｜replica` 顯式化（取代「有沒有 `.law_sync.json`」的隱式判定，scope G）
