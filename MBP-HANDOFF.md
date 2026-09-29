# mbp 交接：待確認與待修事項（2026-09-30）

給 **mbp 上的 opencode** 讀。這是 MSI 端無法自行確認、必須在 mbp 上查證的事。
**請逐項查證後回報，不要先假設原因。**

前置：先 `git pull` 到最新（`X570-HANDOFF.md` 對 x570 建議用 `host-sync.sh`，那是
為 x570 寫的；mbp 只要 `git pull` 即可）。若只想確認環境而不想改檔案，用
`bash scripts/host-doctor.sh` —— 那支腳本（530 行）就是設計來診這種狀況的，
**先跑它再決定要不要動手**。

⚠️ 下面每段指令都**只印指紋／長度，不印值**。照抄即可，不要改成 `cat .env` 或
`env | grep`，也不要在含憑證的指令上加 `bash -x`（2026-09-26 三次憑證外洩都是這樣）。

---

## 核心事實：mbp 的症狀是 502，不是 530

兩者意義完全不同，**先分清楚再動手**：

| 狀態碼 | 意義 | 誰是這個狀況 |
|---|---|---|
| **530** | Cloudflare 找不到 tunnel 的連線端 = `cloudflared` 沒在跑 | x570 |
| **502** | tunnel **連著**，但它轉發的目標沒有回應 = **本機後端沒起來** | **mbp（你）** |

MSI 端 2026-09-30 實測：

```
api-mbp.ragdemo.win  →  HTTP 502   ← 你的情況
api-x570.ragdemo.win →  HTTP 530
api-msi.ragdemo.win  →  HTTP 200
```

**所以你大概不需要裝 cloudflared，也不需要動 DNS。** tunnel 那端已經通了，
502 是「tunnel 轉發到 `http://localhost:8000` 但那裡沒東西」。

**但這是從外部症狀推論的，不是實測。** 請照下面逐項查證，不要因為我這樣寫就跳過。

---

## 事項 1：後端容器有沒有在跑

### 為什麼先查這個

502 的直接原因就是 `localhost:8000` 沒有服務在聽。msi 重灌後那台的 `docker`
daemon 活著但**沒加 docker 群組**，症狀是第一次 `docker compose up` 才出現的
`permission denied ... /var/run/docker.sock`。

### 查證

```bash
cd ~/projects/ragdemo.win
docker compose ps
```

- 有容器且 `Up` → 跳到事項 2
- 沒有容器、或 `Exited`／`Restarting` → 往下走

### 起它

```bash
docker compose up -d
docker compose logs api --tail 30
```

把 log 的**錯誤訊息**回報（不要回報整份 log，只報 `Error`／`Traceback` 附近）。

### 若出現 `permission denied`（macOS 也要看）

```bash
id -nG | grep -qx docker && echo "✓ 在 docker 群組" || echo "✗ 不在 docker 群組"
```

不在的話需要 `sudo usermod -aG docker $USER`（macOS 是 `sudo dscl . -append /Groups/docker
GroupMembership $(whoami)`），**然後登出重登** —— 群組變更不重登不會生效。

---

## 事項 2：`.env` 還在嗎

### 為什麼可能不在

`.env` 是 gitignored，**`git pull` 不會帶過來**。msi 重灌時的 `root.env` 備份在：

```
/mnt/d/backup/wsl/20260928-005056-ragdemo/env/root.env
```

⚠️ **但那是 MSI 那台的備份，裡面的 `POSTGRES_PASSWORD`／`QDRANT_API_KEY` 是 msi
自己重選的值，mbp 不能直接用。** 共用憑證（7 把）可以從那裡取，per-host 機密不行。

### 查證

```bash
cd ~/projects/ragdemo.win
test -f .env && echo "有 .env" || echo "✗ 沒有 .env —— 這很可能就是 502 的原因"
```

### 檢查關鍵鍵有沒有值（只印有無，不印值）

```bash
for k in HOST_ID POSTGRES_DSN QDRANT_API_KEY POSTGRES_PASSWORD OLLAMA_URLS; do
  v=$(grep -m1 "^$k=" .env 2>/dev/null | cut -d= -f2-)
  [ -n "$v" ] && echo "  $k 有值" || echo "  $k ★空★"
done
```

`HOST_ID` 應該是 `mbp`。

---

## 事項 3：OLLAMA_URLS 在 mbp 上該填什麼

### 這是 2026-09-29 才引入的機制，msi 的做法可以直接照抄

`OLLAMA_URLS` 必須是「**該機能連到的 ollama 位址**」，而 mbp 的 ollama 跑在
**本機**（不是另一台），所以是 `localhost`：

```
OLLAMA_URLS=mbp=http://localhost:11434
OLLAMA=http://localhost:11434
```

（兩個都填 ollama。`OLLAMA_URLS` 是**容器內**的 `gateway.py` 讀、
`OLLAMA` 是 **host 端**的 `ingest/laws/qdrant_load.py` 讀 —— mbp 若沒有
container 跑 ollama，兩個視角都指向本機 `localhost`，所以值相同。）

**為什麼不能用 msi 的做法**：msi 的 ollama 跑在 **Windows host**（WSL 之外），
所以要填 WSL 預設閘道 IP（而且那個 IP 會變，現在改成 `env-sync.sh render`
自動偵測）。mbp 是 macOS、ollama 在本機，**直接 `localhost`**，沒有那個問題。

### 確認 ollama 真的在跑

```bash
curl -s -m 5 http://localhost:11434/api/tags -o /dev/null -w "HTTP %{http_code}\n"
```

200 = 通。連不上就是 ollama 沒啟動，或 `OLLAMA_URLS` 填錯。

### 模型名要對

`OLLAMA_MODELS`／`LLM_MODEL` 必須是**這台真的有**的模型名。填錯的症狀很陰晦：
**不報錯**，只是 `gateway._ollama_probe()` 的 `need <= have` 判定失敗 → 判定該機
不可用 → 查詢靜默降級。

```bash
ollama list
```

把 `OLLAMA_URLS` 的**第一個位址**對應到該機的 LLM 模型（位置對應，
`gateway._llm_model_for()` 依 index 取值）。`EMBED_MODEL` 與 `RERANK_MODEL` 要
有對應的 embedding / rerank 模型，沒有就留空走程式預設。

---

## 事項 4：git hooks 設定（`git pull` 不會帶過來）

```bash
git config --get core.hooksPath || echo "✗ 沒設"
```

沒輸出就設上：

```bash
git config core.hooksPath .githooks
```

**這是 per-clone 的 git 設定，不在版控裡，`git pull` 不會帶過來。** 漏了不報錯，
只是 pre-push 的三項檢查（commit 前綴／語法＋`LAN_IP=`／pytest）靜默消失 ——
症狀要等到違規 commit 上線才浮現。

驗證：

```bash
git push --dry-run origin main   # 應看到「✓ pytest N passed」
```

---

## 事項 5：回報格式

請回報以下四項，**每項都要有實測輸出**：

1. `docker compose ps` 的結果（容器名 + STATUS）
2. `.env` 那五個鍵有無值
3. `curl -s -m 5 http://localhost:11434/api/tags -o /dev/null -w "HTTP %{http_code}\n"` 的結果
4. 事項 4 的 `git config --get core.hooksPath` 結果

**不要回報任何憑證值。** 若要證明 `.env` 某鍵有值，打印長度或前 3 個字元就夠
（例：`${#v}` 或 `echo "${v:0:3}…"`）。

---

## 附：若 mbp 恢復後，Pages 那邊要不要動

**不用。** Cloudflare Pages 的 `API_ORIGINS`（2026-09-30 已設成三台、msi 第一）
是 MSI 端設定的，與 mbp 無關 —— mbp 恢復後 worker 就會自動輪到它。

已確認線上現況（2026-09-30）：

```
https://ragdemo.win/api/status
  → x-ragdemo-origin: https://api-msi.ragdemo.win   （worker 正常轉發中）
```

msp 三台備援清單已含 mbp，只是 mbp 還沒回應。
