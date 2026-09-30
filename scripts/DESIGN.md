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
| `HOST-UPGRADE.md` 313 行 ＋ `X570-HANDOFF.md` ＋ `MBP-HANDOFF.md` ＝ 600+ 行散文 | 這就是「開發完另外兩台又要自己改」的來源。**2026-09-30 已把兩份 handoff 精簡到只剩待辦**（原 288 ＋ 200 行 → 200 ＋ 160 行）：x570／mbp 的問題已解決或失效，寫著只會誤導那兩台的 opencode 去查已作廢的事 |
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
| `host-sync.sh` | **部署的單一入口**：preflight → fetch → ref → `env-sync pull` → `compose up -d --build` → 驗收 | 每次升級 |
| `host-doctor.sh` | 一次回答「哪台壞了、哪個鍵、要不要輪換」，永不印值 | 手動／出問題時 |
| `env-sync.sh` | 解密＋合併＋render（三機共用層） | 每次 `pull` |
| `env-audit.py` | 從程式碼反查 `.env` 的落差 | 手動／CI |
| `sync-snapshot.sh` | 從 x570 拉 qdrant 快照到本機 | 每 10 分鐘（crontab／launchd） |
| `law-update-worker.sh` | 執行每日 ingest（source 機跑 sync_daily） | 每日排程 |

### host-sync.sh

`--ref <tag|branch|sha>` 存在的目的是**讓 replica 只吃 tag、不吃半成品**。從此
「三台共用一份 repo 所以只能開一個 scope」這條稅消失：開發機可以在 `work/<scope>`
分支上做未完成的事，而 x570/mbp 只部署打過 tag 的版本。

分支與 tag/sha 刻意走不同路徑：tag/sha → `checkout --detach`（版本不可變），
branch → 留在分支上（否則下次不帶 `--ref` 的執行會卡在 detached 上 pull 不了）。

**刻意不提供** `--force`／`--discard`／`stash`：工作樹不乾淨就 exit 3 並列出檔名。
「要不要蓋掉別人的東西」不該由一支部署腳本決定。

exit code 契約（呼叫者只看得見數字，所以寫在這裡）：`0` 部署完成且驗收通過／
`1` xtrace 拒絕／`2` 引數錯誤／`3` preflight 阻擋／`4` 步驟失敗／
`5` 部署完成但**驗收未通過**／`6` 部署完成但**驗收被略過**（`--skip-verify`）。
5 與 6 分開是刻意的：把「略過」回成 0 等於把沒驗裝成驗過。

**部署步驟失敗與驗收失敗要分開回報**，而且驗收失敗**不回滾**前 5 步：回滾一台已經
在跑舊版本的機器，比留著一台跑新版本但驗收紅燈更危險。

### host-doctor.sh

紀律：**任何 `KEY=value` 形式的輸出絕不出現 value**（鍵名＋長度＋sha12）。
唯一的例外是非憑證的機器代號（`HOST_ID`、日期），而且刻意不用 `KEY=value` 排版。

第二條紀律：**沒有資料 ≠ 故障**。`registry.list_hosts` 裡的 `except: pass` 會把
「連不到共享 pg」也變成空清單，與「沒人」症狀一樣，所以取不到就 `skip` 並寫明原因。

⚠ 但這條**不適用於 parser 自己壞掉**：`docker compose ps` 有資料而我們解不開時
必須報 `fail`。parser 壞掉回報 `skip`，等於讓「醫生自己生病」看起來像「病人沒事」。

指紋直接呼叫 `env-sync.sh --fingerprints`，**不在 doctor 裡重算一份**——兩份真相
必然漂移，而「少印一把」看起來跟「那台沒設」一樣難查。

## 6. 驗收

```bash
bash -n scripts/*.sh
scripts/env-sync.sh --check
docker compose config -q
python3 scripts/env-audit.py

# 部署入口（--dry-run 不寫檔、不 fetch、不切 ref、不動容器）
scripts/host-sync.sh --dry-run
# 診斷（含「輸出無憑證值」的自我檢查）
scripts/host-doctor.sh
scripts/host-doctor.sh --json | python3 -m json.tool >/dev/null
```

## 待補（owner）

- `host-onboard.sh`：從零到能跑（`age` keygen、`HOST_ID`、per-host 機密、sops recipients）—— scope `F2`，**排在 `F` 之後**：沒有 doctor 當診斷基準就寫 onboard，等於再造一份散文 runbook
- `HOST_ROLE=source｜replica` 顯式化（取代「有沒有 `.law_sync.json`」的隱式判定，scope G）
