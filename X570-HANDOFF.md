# x570 交接：待確認與待修事項（2026-09-26）

給 **x570 上的 opencode** 讀。這是 MSI 端無法自行確認、必須在 x570 上查证的
四件事。**請逐項查证後回報，不要先假設原因。**

前置：x570 已有最新程式（實測 `/law-update` 200、`/status` 含 `law_version`），
推測已 pull。請先 `git log --oneline -1` 確認是否為 `24a5d17` 或更新。

---

## 事項 1：qdrant api key —— MSI 與 mbp 都被 x570 拒絕（401）

### 現象（MSI 端實測，2026-09-26）

```
用 MSI .env 的 QDRANT_API_KEY 測：
  x570  http://100.119.83.111:6333/collections/laws  → HTTP 401
  mbp   http://100.64.121.9:6333/collections/laws    → HTTP 401
  msi   http://100.65.68.106:6333/collections/laws   → HTTP 200
不帶 key 測 x570 → "Must provide an API key or an Authorization bearer token"
```

→ x570 的 qdrant **有開認證**，但它認的那把不是 MSI 手上這把。
推測是 2026-09-26 在 x570 輪換過 key（當時為了處理 MSI 端 key 外洩），
但 MSI／mbp 還沒跟上。

### 請在 x570 上查（**只印指紋，不要印值**）

```bash
cd ~/projects/ragdemo          # 依你的實際 checkout 路徑調整
set -a; . ./.env; set +a

# A) x570 的 role（container 實際生效值）
docker inspect ragdemo-qdrant-1 --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -o 'QDRANT__SERVICE__API_KEY=.*' | sed 's/.*=//' \
  | { read -r v; printf 'container 指紋 = %s 長度=%d\n' \
      "$(printf '%s' "$v" | sha256sum | cut -c1-12)" "${#v}"; }

# B) x570 的 .env
printf '.env 指紋 = %s 長度=%d\n' \
  "$(printf '%s' "$QDRANT_API_KEY" | sha256sum | cut -c1-12)" "${#QDRANT_API_KEY}"

# C) MSI 手上那把的指紋（這是已知的比對基準）
printf 'MSI 指紋   = 96c2dec03d81 長度=32\n'
```

### 判定與回報

| A 與 B 關係 | 意義 | 你要回報什麼 |
|---|---|---|
| A ≠ B | `.env` 與容器不同步（改了沒重啟） | 說明差異，**不要自行改值** |
| A = B ≠ MSI | x570 輪換過、MSI 未跟上（我的推測） | **只回報指紋**，由 MSI 端決定要不要對齊 |
| A = B = MSI | 我的推測錯，401 另有原因 | 請追查（`docker logs ragdemo-qdrant-1`、compose 是否有第二份設定） |

> ⚠️ **絕對不要把 key 的值貼回任何 agent 對話。** 只報 `sha256` 前 12 碼。
> 2026-09-26 當天已因 `env | grep`、`bash -x`、`docker compose config`
> 外洩過三次，第三次是三台共用的 key。

---

## 事項 2：POSTGRES_PASSWORD —— MSI／mbp 無法認證 x570 的 registry

### 現象（MSI 端實測）

```
MSI 的 api log 持續 30 秒一次：
  heartbeat skipped: registry heartbeat failed:
  password authentication failed for user "rag"

MSI 本機 postgres：用 .env 的密碼 ✅ 可以認證
x570 的 postgres：  用同一個密碼 ❌ 拒絕

三台 /hosts 回報（讀同一個 registry，所以結果一致）：
  x570 /hosts → 1 台（只有自己）
  mbp  /hosts → 0 台
  msi  /hosts → 0 台
```

→ `POSTGRES_PASSWORD` 三台不一致。`/hosts` 回空是因為心跳寫不進去，
不是 API 壞掉。

### 請在 x570 上查

```bash
cd ~/projects/ragdemo
set -a; . ./.env; set +a

# A) x570 的 .env 密碼能否認證它自己的 role
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -h 127.0.0.1 -U rag -d ragdemo -tAc 'select count(*) from backends'
# 能跑出數字 → x570 的 .env 與 role 一致，錯的是 MSI／mbp 的 .env
# 報 password authentication failed → 相反，x570 的 .env 才是錯的那個

# B) registry 資料是否還在（上面那個 count 就是）
```

### 請回報

1. A 的結果（能跑 / 失敗）
2. `backends` 表的列數
3. **x570 `.env` 裡 `POSTGRES_PASSWORD` 的 sha256 前 12 碼**（不要值）

> 不要自行 `ALTER USER` —— 那會讓 x570 自己的 api 也需要重啟，
> 且方向要由 MSI／mbp 端配合。**只查证並回報。**

---

## 事項 3：2026-09-26 x570 異常關機的根因（尚未證實）

### 已知事實

- 症狀：x570 關機後開機，主機在線（ollama 11434 有回應）但
  **postgres(5432) 與 qdrant(6333) 不通**
- 當時判斷是「不當關機」，你提到原因是用了 `sudo shutdown now` 而非
  `sudo poweroff`
- **但我查證後撤回這個判斷**：systemd 上 `/usr/sbin/shutdown` 就是
  `systemctl` 的 symlink（實測 `lrwxrwxrwx /usr/sbin/shutdown -> ../bin/systemctl`），
  兩者行為完全一致，換寫法不會改變任何事

### 請在 x570 上查

```bash
# A) 上次是否真的完整關機（分辨「關機重開」與「從 suspend 恢復」）
journalctl --list-boots | head -5

# B) 開機時 docker daemon 狀況
systemctl status docker --no-pager | head -10
journalctl -b -u docker --no-pager | tail -40

# C) 容器是否處於「曾被手動停止」狀態
#    （unless-stopped 遇到手動 stop 就不會自啟 —— 曾是本次的初始假設）
docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null \
  || docker inspect $(docker ps -aq) -f '{{.Name}}  status={{.State.Status}}  restart={{.HostConfig.RestartPolicy.Name}}'

# D) 有沒有 suspend 設定（預設 IdleAction=ignore，不會自動睡眠）
systemd-analyze cat-config systemd/logind.conf | grep -E '^#?(IdleAction|HandleLidSwitch|HandlePowerKey)' 
```

### 請回報

- A 的輸出（boots 清單）
- B 的關鍵錯誤行
- C 的三容器 restart policy 與狀態
- D 的設定值

---

## 事項 4：x570 首次執行每日 ingest（尚未排程）

### 現況

```
x570 的 .law_sync.json 已有 → law_version = 2026/9/18 上午 12:00:00
```

→ **手動跑過至少一次**，但沒有排程。三台的「法規版本」欄位目前是：

```
x570: 2026-09-18   mbp: —   msi: —
```

mbp 與 msi 沒有版本，是因為快照同步的 `sync_law_version` 需要
`SRC_API_URL` 指向來源機（8000 綁 loopback，不能用 `IP:8000`），
而 mbp 還沒設。

### 請在 x570 上做

```bash
cd ~/projects/ragdemo

# A) 確認工具鏈（管線是 host 端的 uv 工具鏈，容器跑不了）
uv sync && uv run python -c "import duckdb, asyncpg; print('duckdb', duckdb.__version__)"

# B) 確認 .env 有 POSTGRES_DSN（x570 本機應留空走 compose 預設；
#    若有值且指向別台，metadata 會寫到錯的地方）
grep -E '^(POSTGRES_DSN|HOST_ID|TS_IP)=' .env

# C) 先 dry-run（只檢查不下載）
uv run ingest/laws/sync_daily.py

# D) 確認排程是否存在（應該是空的）
crontab -l | grep sync_daily || echo "尚未排程"
```

### 排程建議（**先回報，不要直接加**）

```
30 2 * * * cd ~/projects/ragdemo && uv run ingest/laws/sync_daily.py --apply >> data/laws/sync_daily.log 2>&1
```

⚠️ 這會**整庫重建 `laws` collection**。加排程前請先回報 D 的結果，
並確認是否有備份需求。

---

## 回報格式

請逐項回報，**每個值都用 sha256 前 12 碼或長度，不要貼原值**：

```
1. qdrant key：A=xxx B=xxx MSI=96c2dec03d81 → 判定是 ___
2. POSTGRES_PASSWORD：A 的結果=___ backends 列數=___ 指紋=xxx → 判定是 ___
3. 關機根因：boots=___ docker 錯誤=___ 容器 restart policy=___ logind=___
4. ingest：duckdb=___ POSTGRES_DSN=___ dry-run 結果=___ 排程=___
```

## 不要做的事

- ❌ 不要把任何憑證的值貼進對話（用指紋）
- ❌ 不要自行 `ALTER USER` 或改 `QDRANT_API_KEY`（方向要三台協商）
- ❌ 不要對含憑證的檔案跑 `bash -x`（會印出 `-H 'api-key: ...'`）
- ❌ 不要跑 `docker compose config` 後貼輸出（會展開所有憑證）
- ❌ 關機請用 `sudo systemctl poweroff`，**不要**先 `docker stop`
  （`unless-stopped` 遇手動 stop 就不會自啟，會讓下次開機少三個容器）
