# x570 交接（2026-09-30 重寫，取代 2026-09-26 版）

給 **x570 上的 opencode** 讀。**請逐項查證後回報，不要先假設原因。**

前置：`git pull` 到最新。若只想確認環境而不想改檔案，用 `bash scripts/host-doctor.sh`
（530 行，專為診這種狀況寫的），**先跑它再決定要不要動手**。

⚠️ 下面每段指令都**只印指紋／長度，不印值**。照抄即可，不要改成 `cat .env` 或
`env | grep`，也不要在含憑證的指令上加 `bash -x`（2026-09-26 三次憑證外洩都是這樣）。

> **★ 請先做事項 5** —— 那是唯一的硬性阻塞，其他都是確認性質。編號沿用舊版，
> 因為 repo 裡有 8 處引用（`.sops.yaml`、`SCOPE.md`、`settings/env/README.md`、
> `.opencode/agents/*.md`、`scripts/env-sync.sh`），改號會全部斷掉。

---

## 現況：x570 已經上線，不要去修 tunnel

2026-09-30 01:39–01:45 msi 端實測：

```
api-x570.ragdemo.win  →  HTTP 200   host_id=x570  llm=qwen3:14b
api-mbp.ragdemo.win   →  HTTP 200
api-msi.ragdemo.win   →  HTTP 200
```

Cloudflare API 查 tunnel 狀態：

```
ragdemo-x570  id=6539736a-…  status=healthy  conns=4
ragdemo-mbp   id=2ae52a95-…  status=healthy  conns=4
ragdemo-msi   id=c2280387-…  status=healthy  conns=4
```

x570 的 tunnel ingress 設定也正確：

```
api-x570.ragdemo.win  →  http://localhost:8000
(catch-all)           →  http_status:404
```

x570 自己的 `/status` 回報三台全通：`{'x570': '連線成功', 'mbp': '連線成功', 'msi': '連線成功'}`。

**為什麼還要寫這份**：msi 端在 01:2x 測到的是 **530**，01:39 變 200。x570 是在這個
session 中復原的，不是本來就沒問題。**若你讀到這份時 `api-x570` 又變 530**，那說明
它會掉線 —— 那才是要查的情況，請照 `TUNNEL-530-2026-09-24.md` 的 x570 checklist 走。

**530 與 502 意義不同**，先分清楚再動手：

| 狀態 | 意義 | 處置 |
|---|---|---|
| **530** | Cloudflare 找不到 tunnel 連線端 = `cloudflared` 沒在跑 | 拉起 tunnel |
| **502** | tunnel **連著**，但轉發目標沒回應 = 本機後端沒起來 | 查容器 |

（`api-mbp` 在 09-29 下午是 502、09-30 早上就 200 了 —— 那是它自己的後端沒起來，
不是 tunnel 問題。）

### 法規資料：三台一致，且是最新的

```
api-x570 /status → law_version = 2026/9/18   來源 = law_sync.json
api-msi  /status → law_version = 2026-09-18
```

`2026/9/18` 是**法規本身的版本日期**，不是「上次同步時間」。msi 的
`data/laws/.law_sync.json` 顯示 `last_checked: 2026-09-29T23:39` ——
**昨天才查過上游，最新就是 9/18**。所以三台一致不是「一起過期」。

---

## 事項 5：報回 age 公鑰（唯一的硬性阻塞）★先做這個★

### 現況

`settings/env/secrets.common.enc.env` 裡有 **7 把三機共用憑證**，
用 sops＋age 加密。`.sops.yaml` 的 recipients 目前**只有 msi 一把**：

```yaml
creation_rules:
  - path_regex: settings/env/secrets\..*\.enc\.env$
    age:
      - age19et4d4etz4ptsp2s58838ffmfgzq8sfw3c775xc2gewgh74wtexqgej86q   # msi
      # TODO: x570 公鑰
      # TODO: mbp 公鑰
```

### 為什麼這是硬性阻塞

`env-sync.sh pull` 會 `sops -d` 那個檔案。**sops 只能用 recipients 裡的公鑰去解** ——
x570 的年齡金鑰不在名單裡，`pull` 就會失敗。症狀是共用憑證（`ADMIN_TOKEN`／
`QDRANT_PEER_API_KEY`／`CF_AIG_TOKEN`／`HF_TOKEN`／`NVIDIA_API_KEY`／
`TYPESAFE_API_KEY`／`ZEN_API_KEY`）拿不到。

**per-host 的 2 把機密（`QDRANT_API_KEY`／`POSTGRES_PASSWORD`）不在加密檔裡**，
那兩把 x570 用自己原本的值，本來就沒問題 —— 不要因為 pull 失敗就去動它們。

### 怎麼做（回報用，**在 x570 上執行**）

```bash
# 已有就跳過，絕對不要重建（重建會讓已加密的檔案解不開）
ls ~/.config/sops/age/keys.txt 2>/dev/null && grep -c . ~/.config/sops/age/keys.txt

command -v age-keygen || echo "需先裝 age：https://github.com/FiloSottile/age"
command -v sops     || echo "需先裝 sops：https://github.com/getsops/sops"

# 產生或取出公鑰（只印 age1... 那一行，那是公開的，可以回報）
grep -o 'age1[0-9a-z]*' ~/.config/sops/age/keys.txt 2>/dev/null | head -1
```

### 回報什麼

只回報 `age1...` 開頭那一行（公鑰，設計上就是公開的，可以進版控）。

**絕對不要回報 `keys.txt` 本身、`age-secret-key1...` 開頭那一行、或整個檔案內容。**

拿到後 msi 端會：加進 `.sops.yaml` recipients → 跑 `sops updatekeys` 重加密 → commit。
**x570 端不需要做任何其他事**，下次 `git pull` + `env-sync.sh pull` 就通了。

### 若 `keys.txt` 已存在但 pull 仍然失敗

回報錯誤訊息**第一行**就好。可能是私鑰與 `.sops.yaml` 裡的 recipient 對不上
（例如中途重建過），那要在 msi 端解決，不要在 x570 端反覆重試。

---

## 事項 1：~~去另外兩台輪換 qdrant key 到同一把~~ — 已作廢

2026-09-27 起 `QDRANT_API_KEY`／`POSTGRES_PASSWORD` 從「9 把共用」改成
**7 把共用 ＋ 2 把 per-host 機密**（見 `SCOPE.md` 2026-09-27 那列）。兩把 per-host
機密本來就該各機不同 —— 舊版指示「輪換到同一把」會製造三台共用同一個密碼的問題。

**不需要做任何事。**

---

## 事項 2：`POSTGRES_PEER_PASSWORD` — 沒有程式讀它，不需要動作

保留編號是因為 `SCOPE.md`、`settings/env/README.md`、`.opencode/agents/env-ops.md`
都指向這裡。

### 當初為什麼有這件事

2026-09-26 之前，三台的 `/hosts` registry 指向**同一個** pg（在 x570 上），
所以 x570 的 pg 密碼未知會讓三台互相鎖死。舊版事項 2 就是要查證那個密碼。

### 現在為什麼不需要了

**架構已改**：各台的 `/hosts` 改讀**自己的** pg。2026-09-30 實測：

```
api-msi  /hosts → 只有 msi 自己
api-x570 /hosts → 只有 x570 自己
```

開發機不污染共用 registry（這是刻意的，見 `settings/env/hosts.shared.env` 的註解）。

而且 `POSTGRES_PEER_PASSWORD` **沒有任何程式讀它** —— 全 repo 只有註解、範例檔與測試
提到那個名字：

```bash
grep -rn POSTGRES_PEER_PASSWORD --include='*.py' --include='*.sh' . | grep -v '\.git'
# 結果：只有 scripts/env-sync.sh:43 的註解、兩個 .example 的說明文字、tests/
```

**結論：值到齊也沒人讀。** 不要為了填它而去查 x570 的 pg 密碼、也不要加那個鍵 ——
加了就是幽靈鍵，CI 的「per-host 不得進加密檔」檢查會困惑。

（對照：`QDRANT_PEER_API_KEY` **是真的有人在讀** —— `scripts/sync-snapshot.sh:61`
在拉快照時用它認證來源機。x570 是來源機（見事項 4），不需要它。）

---

## 事項 3：2026-09-26 異常關機的根因 — 已被時間取代

舊版要查「那次關機是什麼造成的、為什麼開機後 docker 沒起來」。

**現在不需要查**：x570 從那之後到現在（9/30）持續運行，tunnel healthy、有 4 條連線、
`/status` 正常回應。當時的故障狀態已經不存在，**沒有重現的環境**。

若你日後在 x570 上遇到「開機後容器沒起來」，那時再查（症狀與做法見
`TUNNEL-530-2026-09-24.md` 與 `HOST-UPGRADE.md` 的自我檢查）。

---

## 事項 4：x570 現在是「來源機」，請確認每日排程

### 角色已經變了

`scripts/law-update-worker.sh` 的角色判斷**只看一個檔案**：

```bash
if [ -f "$ROOT/data/laws/.law_sync.json" ]; then
  ROLE="source"; CMD="uv run ingest/laws/sync_daily.py --apply"
elif [ -n "$SRC_Q" ]; then
  ROLE="backup"; CMD="scripts/sync-snapshot.sh --force …"
```

x570 的 `/status` 回報 `law_version 來源 = law_sync.json` → **x570 已有那個檔案 → 系統
判定 x570 是 `source`**。

**這跟舊版不一樣**：2026-09-26 時「x570 是唯一來源機」是設計前提。2026-09-29 msi
也跑完了 `sync_daily`（`data/laws/.law_sync.json` 的 `applied_at: 2026-09-29T23:54`），
所以**現在有兩台 source 機**，彼此不互相依賴。這是好事（任一台掛掉另一台還能更新）。

### 要確認什麼

`ROLE=source` 意味著前端按「更新」時，worker 會在 **x570 本機**跑 `sync_daily.py`
（抓上游 → 寫本機 pg/qdrant）。那需要排程在跑：

```bash
# 1) worker 排程（每分鐘撿前端排入的請求）
crontab -l | grep law-update-worker || echo "✗ 沒有 law-update-worker 排程"

# 2) 狀態檔 —— 這是權威紀錄，比讀 log 可靠
cat data/.ops/law-update.status 2>/dev/null || echo "✗ 沒有狀態檔 → worker 從未執行過"

# 3) 上游檢查時間（這才決定資料新不新）
cat data/laws/.law_sync.json 2>/dev/null || echo "✗ 沒有 .law_sync.json"
ls -la --time-style=long-iso data/laws/.law_sync.json

# 4) 上次執行的輸出（僅在狀態檔顯示失敗時才需要）
tail -20 data/.ops/.last-output.log 2>/dev/null
```

路徑依據 `scripts/law-update-worker.sh:17-21`：`OPS="$ROOT/data/.ops"`，
狀態檔是 `law-update.status`、輸出檔是 `.last-output.log`。

**狀態檔不存在 = worker 從未跑過**（比 log 空更好判斷 —— log 可能在被覆寫前就空）。
對照：msi 的 `data/.ops/` 目前是空的，因為 msi 的資料是**手動**跑
`sync_daily.py` 灌的（`.law_sync.json` 的 `applied_at: 2026-09-29T23:54`），
那台沒排 law-update-worker。

### 判讀

- **有排程 ＋ `last_checked` 是近期** → 一切正常，回報日期即可
- **沒排程** → x570 雖被判定為 source，但按「更新」會沒人執行。
  補上（`HOST-UPGRADE.md` §4 有 mbp 的版本，x570 用 crontab）：
  ```bash
  crontab -e
  # 加這行（每分鐘）：
  # * * * * * /home/solo/projects/ragdemo.win/scripts/law-update-worker.sh >/dev/null 2>&1
  ```
  路徑要改成 x570 上 repo 的**實際絕對路徑**（先 `pwd` 確認）。
- **`last_checked` 很久以前** → 資料是碰巧還對著，不是真的在追。請回報日期，
  msi 端判斷要不要重跑。

### 順帶：`SRC_API_URL` 在 x570 上指向自己

`settings/env/hosts.shared.env` 有 `x570_SRC_API_URL=https://api-x570.ragdemo.win`。

`SRC_API_URL` 只被 `scripts/sync-snapshot.sh` 讀（用來問來源機的 law_version），
而 x570 是 source 機、不會走那條路，**所以現在無害**。

但若哪天 x570 的 `.law_sync.json` 不見了（角色變成 backup），它會去「拉自己」的快照
—— 那是無效迴圈。**屆時應該改成指向 msi**（`https://api-msi.ragdemo.win`）。
不要現在就改，現在改等於製造第三種狀態。

---

## 事項 6：git hooks（`git pull` 不會帶過來）

```bash
git config --get core.hooksPath || echo "✗ 沒設"
```

沒輸出就設上：

```bash
git config core.hooksPath .githooks
```

**這是 per-clone 的 git 設定，不在版控裡，`git pull` 不會帶過來。** 漏了不報錯，
只是 pre-push 的三項檢查（commit 前綴／語法＋`LAN_IP=`／pytest）靜默消失 ——
症狀要等到違規 commit 上線才浮現，那時已經難回溯是哪台機器按的。

實測過：msi 重灌後這項不見了，連續 11 個 commit 在無任何檢查下產生（內容碰巧安全，
因為每次都另外手動跑了 pytest，但機制是失效的）。

驗證：

```bash
git push --dry-run origin main   # 應看到「✓ pytest N passed」
```

---

## 事項 7：總表裡 x570 的欄位是空的

`settings/env/hosts.shared.env` 這四列目前是空值：

```
x570_TS_IP=
x570_HOST_MACHINE_ID=
x570_OLLAMA_URLS=
x570_OLLAMA_MODELS=
```

**這是刻意的**（2026-09-29 決定：IP 不進被追蹤的檔案，`env-sync.sh render` 會自動
偵測 WSL 閘道與 ollama 位址）。msi 端無權也不該猜 x570 的值。

### 影響

`/health` 回報的 `machine_id`／`ips`／`llm_src` 是 **x570 自己心跳寫進自己 pg 的**，
所以**對運作沒有影響**（實測 `machine_id=af8eaa67…`、`ips=["100.119.83.111"]`、
`llm_src=http://100.119.83.111:11434` 都正常）。

但有一個情境會出事：**若 x570 上跑 `bash scripts/env-sync.sh render`**，它會用這些
空值生成 `.env`，可能產出 `OLLAMA_URLS=` 空值 → `gateway.py` 退回原始碼預設 →
**查詢靜默降級，不報錯**。

### 怎麼處理

**先不要跑 `render`**。若你確實需要（例如想改 x570 的模型設定），先回報

```bash
# 只印前 20 字元，不要整行印出
grep -E '^(OLLAMA_URLS|OLLAMA_MODELS|LLM_MODEL)=' .env | cut -c1-60
curl -s -m 5 http://localhost:11434/api/tags -o /dev/null -w "ollama HTTP %{http_code}\n"
ollama list
```

（`OLLAMA*` 的值是位址與模型名，不是機密，但**不要用 `cat .env`** —— 那會把
`POSTGRES_PASSWORD`／`QDRANT_API_KEY` 一起印出來。）

msi 端會依回報把值補進 `hosts.shared.env` 的 `x570_*` 欄，再請你跑 `render`。

**注意**：x570 的 LLM 模型是 `qwen3:14b`（`/health` 的 `llm` 欄），與 msi 的
`qwen2.5-coder:latest` 不同 —— 這是對的，總表裡 `x570_LLM_MODEL=qwen3:14b` 已經填好，
**不要**把它改成 msi 的值。

---

## 回報格式

請回報以下五項，**每項都要有實測輸出**：

1. **事項 5**：`grep -o 'age1[0-9a-z]*' ~/.config/sops/age/keys.txt | head -1` 的結果
2. **事項 4**：`crontab -l | grep law-update-worker` 有沒有；`.law_sync.json` 的
   `last_checked` 與檔案 mtime
3. **事項 6**：`git config --get core.hooksPath` 的結果
4. **事項 7**：那三行 `OLLAMA*`／`LLM_MODEL` 的值（前 20 字元）＋ `ollama list`
5. **自檢**：`bash scripts/host-doctor.sh` 的摘要（不用貼全文）

**不要回報任何憑證值。** 要證明某鍵有值，打印長度或前 3 個字元就夠
（`${#v}` 或 `echo "${v:0:3}…"`）。**age 私鑰（`age-secret-key1...`）絕對不能回報。**

若 `api-x570.ragdemo.win` 在你執行本文件期間不是 200，請一併回報當下的
HTTP 狀態碼與 `curl -sS -D-` 的**前 10 行**（response header，不是 body）。

---

## 附：Pages 那邊不用動

Cloudflare Pages 的 `API_ORIGINS`（2026-09-30 已設成三台、**msi 第一**）是 msi 端
設定的，與 x570 無關。worker 依序嘗試，x570 掛掉會自動輪到 mbp／msi。

線上實測（2026-09-30）：

```
https://ragdemo.win/api/status → HTTP 200
  x-ragdemo-origin: https://api-msi.ragdemo.win
  log: {'x570': '連線成功', 'mbp': '連線成功', 'msi': '連線成功'}
```

**唯一要注意的**：若 x570 變慢或半死（TCP 連得上但回應逾時），worker 會**先卡在
x570** 才 fallback。症狀是查詢變慢但仍成功。那時要處理的是 x570 的效能，不是設定。
