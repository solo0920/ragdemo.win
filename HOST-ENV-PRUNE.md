# HOST-ENV-PRUNE — x570 / mbp 請照這份做（2026-10-01）

> 對象：`x570`、`mbp`。`wsl` 已做完，本檔是同一套步驟的另一份拷貝。
> 兩件事，其中**第 1 件是 bug 修復、不是清理，不做就會繼續壞著**。

---

## 0. 前提

```bash
cd ~/projects/ragdemo            # 換成你的實際路徑
git pull
```

拉完先確認一件事——**你的 `.env` 裡 `HOST_ID` 是什麼**：

```bash
grep '^HOST_ID=' .env
```

| 你看到 | 動作 |
|---|---|
| `HOST_ID=x570` 或 `HOST_ID=mbp` | ✅ 不用改，繼續第 1 步 |
| `HOST_ID=msi` | ⚠️ **先改成你現在的代號再繼續**，否則下面每一個 `render`／`--check` 都會硬失敗（`主機代號不合法: msi`）。`wsl` 那台就是這個情況。 |

```bash
# 只有 msi 的情況才需要（後面兩台的代號照抄 HOSTS= 那一行的第一個）
sed -i 's/^HOST_ID=msi$/HOST_ID=<你的代號>/' .env
scripts/env-sync.sh render      # 從總表抓回你該機的 per-host 值
```

---

## 1. 🔴 修 `pg.py`：registry 心跳與 usage 記錄現在是死的

### 症狀（你應該已經見過，只是沒歸因）

```
$ curl -s http://127.0.0.1:8000/hosts
{"hosts":[]}          ← 永遠空

$ docker compose logs api | grep heartbeat
heartbeat skipped: registry heartbeat failed:
  connect() got an unexpected keyword argument 'connect_kwargs'
```

### 根因

`backend/app/common/pg.py` 的 pool 建立寫法不對（2026-09-30 的 `b1df6d0` 引入）：

```python
# ❌ 錯的（已修）
asyncpg.create_pool(DSN, min_size=1, max_size=3,
                    connect_kwargs={"timeout": PG_CONNECT_TIMEOUT})

# ✅ 對的
asyncpg.create_pool(DSN, min_size=1, max_size=3,
                    timeout=PG_CONNECT_TIMEOUT)
```

`create_pool` 沒有「名叫 `timeout`」的參數，但它的 `**connect_kwargs` 會**整包轉給
`connect()`**。所以 `connect_kwargs={"timeout": 3}` 會被轉成
`connect(..., connect_kwargs={...})`，而 `connect()` 沒有這個參數 → `TypeError`。

**為什麼會靜默**：pool 建不起來的例外被 try 包起來，所以沒有 traceback，只有
`heartbeat skipped`。於是 `/hosts` 永遠空、`usage` 永遠沒紀錄——而這些正是
「三台一致」唯一的自動證據。

### 動作

```bash
git pull                                  # 已含修復
docker compose up -d --build api          # ← 一定要重建，舊 image 裡還是壞的
```

### 驗證

```bash
sleep 40
curl -s http://127.0.0.1:8000/hosts | head -c 200
docker compose logs api --since 2m | grep -c 'heartbeat skipped'   # 期望 0
```

`/hosts` 應該出現一筆你自己的紀錄（`host_id` / `machine_id` / `models` / `last_seen`）。

> **這段 bug 是共用程式碼，所以 x570 與 mbp 也一樣受影響。**兩台的 registry 應該都是空的。

---

## 2. 🟡 清理 `.env`：365 行 → 300 行

`.env` 是 `cp .env.example .env` 出來的骨架，所以天生帶著幾十個**空值賦值**。
問題是空值賦值不等於「沒設」：

```python
os.getenv("KEY", default)    # 對 `KEY=` 回傳 ""，不是 default
```

所以 `KEY=` 不是留白，是「設成空字串」。踩過最嚴重的一個：
`gateway.py` 的 `CF_AIG_TOKEN_FILE` 沒有 `or` 保護，寫成 `CF_AIG_TOKEN_FILE=`
會讓 token 檔 fallback 變成 `Path("")` → 例外 → 回 `""`，等於把它弄壞。

### 動作

```bash
scripts/env-prune.py --dry-run      # 先看會動哪些鍵（不寫檔）
scripts/env-prune.py                # 執行
```

它做三件事，判準寫在腳本開頭：

| 處置 | 鍵 | 理由 |
|---|---|---|
| **刪整行** | 17 個 `*_MODELS` / `*_GATEWAY_URL` / `*_BASE_URL` / `TYPESAFE_URL` / `CF_AIG_GATEWAY_ID` | 空值沒意義；預設值在 `compose.yaml` 寫得更好。留一份空值當備忘，那份副本本身就是漂移的來源 |
| **註解掉留原因** | `EMBED_MODEL`、`RERANK_MODEL`、`POSTGRES_DB/USER`、`QDRANT_URL(S)`、`HOST_*_FILE`、`CF_AIG_TOKEN_FILE`、`ZEN_API_KEY`、`HOST_API_LOCAL`、`QDRANT` | 設了也不生效（compose 寫死／沒傳入容器）或本來就是幽靈鍵 |
| **保留空值＋寫理由** | `TS_IP`、`RAG_MIN/MID/HIGH_DENSE`、`LIMIT` | 這些的「空」本身就是明確決策 |

備份會寫到 `~/.ragdemo-env.bak-<日期>`，**在 repo 外**——因為 `.gitignore` 的
`.env` 是精確比對，`.env.bak` 不會被擋，`git add -A` 就會把全明文憑證 commit 進去。
（2026-10-01 已補上忽略規則，但別依賴它。）

### 驗證

```bash
scripts/env-sync.sh --check     # exit 0
docker compose config -q        # 只驗語法、不展開憑證
docker compose up -d --force-recreate api
sleep 40 && curl -s http://127.0.0.1:8000/health
```

> ⚠️ `--force-recreate` 不能省。移掉的空值剛好等於 compose 預設，所以
> **resolved config 沒變 → compose 不會重建容器**。不強制重建等於沒驗證。

---

## 3. 順帶一提：commit message 前綴

`.githooks/commit-msg` 改成**讀 `settings/env/hosts.shared.env` 的 `HOSTS=` 那一行**，
不再寫死 `msi|mbp|x570`。所以：

- `x570:` / `mbp:` / `wsl:` 都合法
- `msi:` **會被拒**——`msi` 現在是 Windows 主機的 tailscale 名，不是後端主機

如果你的 `git config core.hooksPath` 還沒設：

```bash
git config core.hooksPath .githooks
```

---

## 不要做的事

- **不要 `cat .env`**、不要 `env | grep KEY`、不要對 `env-sync.sh` 跑 `bash -x`
- **不要 `docker compose config` 後貼輸出**（會展開所有憑證）。只驗語法用 `config -q`
- **不要把 `.env` 的備份放 repo 內**（見上）
- **不要手改 §3 那六把共用憑證**——手改會在下一次 `pull` 被覆蓋
- **不要為了填 `*_TS_IP` 去查 tailscale IP** 填進總表。該在該機自己的 `.env` 設
- 回報憑證狀態只給「鍵名＋長度＋sha256 前 12 碼」：`scripts/env-sync.sh --fingerprints`

---

## 順帶一提：`HOST_ID` 改名這件事本身

`msi` → `wsl` 是因為 **WSL 現在自己裝了 tailscale**，hostname 是 `wsl`，而後端跑在
WSL 上。同一台實體機器的 Windows 主機仍然叫 `msi`，它**不是**後端主機，只出現在
「ollama 在哪」的位置（`OLLAMA_URLS=msi=http://msi.tailfe3f3d.ts.net:11434`）。

四個角色現在是分開的：

```
wsl     = 後端跑的地方（WSL，Linux，有自己的 tailscale 節點）
msi     = Windows 主機，ollama 跑在這裡
x570    = 另外那台後端主機 + 模型伺服器
mbp     = 另一台後端主機
```

連帶刪掉的是 `env-sync.sh` 的 `detect_host_endpoint()`：它抓 WSL NAT 的預設閘道
（`172.24.x.1`）當 Windows 主機的位址，那個 IP 由 Windows 分配、重啟會變。走
MagicDNS 的 FQDN 就穩定了。**死掉的機制比沒有機制更糟**——它會讓人以為有人負責。