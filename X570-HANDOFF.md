# x570 交接：待確認與待修事項（2026-09-26）

給 **x570 上的 opencode** 讀。這是 MSI 端無法自行確認、必須在 x570 上查证的
四件事。**請逐項查证後回報，不要先假設原因。**

前置：x570 已有最新程式（實測 `/law-update` 200、`/status` 含 `law_version`），
推測已 pull。請先 `git log --oneline -1` 確認是否為 `24a5d17` 或更新。

---

## 事項 1：qdrant api key 401 —— **2026-09-27 判定：已不需要 x570 查證，作廢**

> **本項作廢，請不要在 x570 上做任何查證或設定。** 2026-09-27 逐點 grep 查證後
> 確認 `QDRANT_API_KEY` 的每個消費點都只指向自己那台的 qdrant
> （`compose.yaml:12`、`compose.yaml:34` ＋ `rag.py:331`），**跨機認證走的是
> `QDRANT_PEER_API_KEY`**。所以：
>
> - 「用 MSI 的 key 打 x570 得到 401」是**正確行為**，不是故障 —— MSI 的 key 本來
>   就不該能打開 x570 的 qdrant。
> - 原先「三台共用同一把所以等同三台外洩，請整組換掉」的判斷是錯的。
> - **`QDRANT_PEER_API_KEY` 才是三台共用那一把**，它在
>   `settings/env/secrets.common.enc.env`（sops+age 加密），`env-sync.sh pull`
>   會自動分發。x570 只需要 `git pull` ＋ `env-sync.sh pull` ＋ 重建容器，
>   **不需要手動設定任何 key**。
> - 「mbp 的 qdrant key 還是第三把舊的」這件事不是故障，不必處理。
>
> 下面的原始查證步驟保留供追溯，**不要執行**。完整推理見
> `ARCHITECTURE.md`〈密鑰管理〉與 `settings/env/README.md`。

<details>
<summary>（已作廢）原始現象與查證步驟 —— 保留供追溯，不要執行</summary>

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

</details>

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

### ⚠️ 2026-09-27 新發現：問題不是「三台 POSTGRES_PASSWORD 不一致」

MSI 端量到的事實：`POSTGRES_DSN` 裡內嵌的密碼，與 MSI 自己的
`POSTGRES_PASSWORD` **指紋不同**（`55cebf3c8276` vs `e328bd31728a`，都是 32 字元）。

```
MSI .env 的 POSTGRES_DSN = postgresql://rag:<55cebf3c8276>@100.119.83.111:5432/ragdemo
MSI .env 的 POSTGRES_PASSWORD = <e328bd31728a>     ← 這是 MSI **本機** pg 容器的密碼
```

所以正確的模型是：

| 變數 | 指向 | 三台是否需一致 |
|---|---|---|
| `POSTGRES_PASSWORD` | **本機** pg 容器 | **否**（各機自己的） |
| `POSTGRES_DSN` 內嵌的密碼 | **x570** 的 pg | **是**（mbp/msi 要靠它心跳） |

→ 原本把 MSI 的 `POSTGRES_PASSWORD` 送去對 x570 認證，是拿錯鑰匙。
請在 x570 上多量一個值，才能判定 MSI 的 DSN 到底是「對的舊值」還是「早就錯了」：

```bash
# C) x570 自己的 role 密碼指紋（與 .env 的比對用；不要值）
printf 'x570 .env POSTGRES_PASSWORD = %s\n' \
  "$(printf '%s' "$POSTGRES_PASSWORD" | sha256sum | cut -c1-12)"

# D) 容器內 role 的實際密碼指紋（從 shadow 讀不出明文，改用「改密碼驗證」法：
#    先記錄目前 .env 的指紋 → 若 D 與 C 不同代表 .env 與 role 已脫節）
#    這一步只讀不寫：查 pg_hba 與 role 的 created 狀態
docker compose exec -T postgres psql -U rag -d ragdemo -tAc \
  "select rolname, rolvaliduntil from pg_authid where rolname='rag'"
```

回報格式補一行：

```
2b. x570 role 與 .env 是否脫節：__（D 的結果）
2c. x570 的 pg role 密碼指紋 = xxxx（若能取得；這就是 mbp/msi 的 DSN 該用的值）
```

拿到 `2c` 之後，MSI 端會新增 `POSTGRES_PEER_PASSWORD`（照 `QDRANT_PEER_API_KEY`
的命名慣例）並把 `hosts.shared.env` 的三列 `POSTGRES_DSN` 接上，**不需要**動
`POSTGRES_PASSWORD`。

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
2b. x570 role 與 .env 是否脫節：___
2c. x570 的 pg role 密碼指紋 = xxxx
3. 關機根因：boots=___ docker 錯誤=___ 容器 restart policy=___ logind=___
4. ingest：duckdb=___ POSTGRES_DSN=___ dry-run 結果=___ 排程=___
5. age 公鑰：age1...（64 字，見下）
```

## 事項 5：提供 x570 的 age 公鑰（2026-09-27 新增，共用憑證改走加密分發）

9 把共用憑證改走 `settings/env/`＋`sops`+`age`（見該目錄 `README.md`）。
x570 要能解密，需把它的公鑰加進 `.sops.yaml`。**公鑰本來就是公開的，
可以貼；私鑰絕對不要貼。**

```bash
# Ubuntu/WSL（免 sudo，裝到 ~/.local/bin）
curl -fsSL -o /tmp/opencode/age.tar.gz \
  https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz
mkdir -p /tmp/opencode/age-out ~/.local/bin
tar xzf /tmp/opencode/age.tar.gz -C /tmp/opencode/age-out
cp /tmp/opencode/age-out/age/age /tmp/opencode/age-out/age/age-keygen ~/.local/bin/
# sops 同理：getsops/sops v3.13.3 的 sops-v3.13.3.linux.amd64 → ~/.local/bin/sops

# 生 key（已有則跳過，絕不重建）
mkdir -p ~/.config/sops/age && chmod 700 ~/.config/sops ~/.config/sops/age
[ -f ~/.config/sops/age/keys.txt ] || age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

# 回報這行（公鑰，可貼）
grep -oE 'age1[0-9a-z]+' ~/.config/sops/age/keys.txt
```

收到公鑰後，MSI 側跑 `sops updatekeys settings/env/secrets.common.enc.env`
並 push，x570 再 `pull`＋`scripts/env-sync.sh pull` 即可（mbp 同理，
macOS 用 `brew install age sops`）。

## 不要做的事

- ❌ 不要把任何憑證的值貼進對話（用指紋）
- ❌ 不要自行 `ALTER USER` 或改 `QDRANT_API_KEY`（方向要三台協商）
- ❌ 不要對含憑證的檔案跑 `bash -x`（會印出 `-H 'api-key: ...'`）
- ❌ 不要跑 `docker compose config` 後貼輸出（會展開所有憑證）
- ❌ 關機請用 `sudo systemctl poweroff`，**不要**先 `docker stop`
  （`unless-stopped` 遇手動 stop 就不會自啟，會讓下次開機少三個容器）
