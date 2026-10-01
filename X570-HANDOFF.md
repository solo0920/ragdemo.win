# x570 交接（2026-10-01 修訂，取代 2026-09-30 版）

給 **x570 上的 opencode** 讀。**逐項查證後回報，不要先假設原因。**

> **代號對照（2026-10-01 改的，看得懂的話跳過）**：跑後端的那台的代號已從
> `msi` 改成 **`wsl`** —— 後端跑在 WSL 上，WSL 自己有 tailscale、hostname 是
> `wsl`。`msi` 現在指的是同一台實體機器的 **Windows 主機**（ollama 跑在那裡，
> `wsl_OLLAMA_URLS=msi=http://msi.…:11434` 是**活的值**，別動它）。
> 本文件裡講到「後端那台」的地方，都是 `wsl`。

前置：`git pull`。想先確認環境用 `bash scripts/host-doctor.sh`，**先跑它再決定要不要動手**。

⚠️ 指令**只印指紋／長度，不印值**。不要改成 `cat .env`／`env | grep`，
不要對含憑證的指令加 `bash -x`（2026-09-26 三次憑證外洩都是這樣）。

> **★★ 唯一還沒做的：Access Service Token —— 先做這件。**
>
> ### ⭐ 2026-10-02：不用手動貼了，跑 `env-sync.sh pull` 就好
>
> `CF_ACCESS_CLIENT_ID`／`CF_ACCESS_CLIENT_SECRET` 已納入**共用憑證層**
> （sops 加密，8 把之一），wsl 端已把值寫進加密檔。所以你那台只要：
>
> ```bash
> git pull && bash scripts/env-sync.sh pull
> ```
>
> 舊指示（步驟 2 的 `printf 'CF_ACCESS_CLIENT_ID=<39字元hex>...' >> .env`）
> **作廢**。本節保留它，是為了讓你知道為什麼不要照做：
> ① 值要經過聊天／剪貼簿；② **貼反的症狀與「Access 沒開」完全一樣**
> （都回 403，所以要靠 `access-check.sh` 第 3 段才分辨得出）；
> ③ `--fingerprints` 不涵蓋它 → 兩台不一致是**無聲**的。
> 走 `pull` 這三個問題整類消失。
>
> 驗證（不印值）：
> ```bash
> bash scripts/env-sync.sh --fingerprints | grep CF_ACCESS
> # CF_ACCESS_CLIENT_ID      len=39   sha12=11cb9bc42f55
> # CF_ACCESS_CLIENT_SECRET  len=54   sha12=f00a6bd62ddc
> ```
> **這兩組指紋三台必須相同**。不同 = `pull` 沒成功。
>
> 然後 **`docker compose up -d --build api`** —— `--build` 不能省，見下方
> 步驟 3 與〈坑二〉。
>
> ---
>
> 三台的 Access app 都建好了（三台一致 403）。沒 token 的症狀（都很安靜）：
>
> - 前端「連線詳細」顯示 **x570／mbp 連線失敗**（但查詢正常）
> - `sync.log` 出現 `law version: 取不到` → `.law_version` 凍結在舊版
> - `host-doctor.sh` 的 `law-version` **warn**（「快照已 N 小時沒成功更新」）
>
> ---
>
> **以下為 2026-10-01 更新（本機 x570 先前已執行完畢，該部分為「已解決 + 移交」）：**
>
> 1. **事項 5（age 公鑰）已完成** —— `.sops.yaml` 三把公鑰都在
>    （wsl／x570／mbp），`secrets.common.enc.env` 內也有對應的三筆 recipients，
>    `sops updatekeys` 確實跑過，pull 不會再遇到
>    `no identity matched any of the recipients`。
> 2. **「本機後端 502」已解決** —— 根因不是容器停掉，是 2026-09-30 一次
>    `git reset` 吃掉了 watchdog 腳本。已修復並 push（3 個 commit）。
>    完整證據鏈見〈已解決：2026-09-30 的 git reset 事件〉。
> 3. **本文件推翻了三處舊指示**（都是舊版把程式碼行為記反了）：
>    事項 7a（worker 狀態檔）、事項 7b（不要跑 `render`）、
>    以及「`api-wsl` 403 是故障」。理由都寫在該節內。
>
> 編號沿用舊版：repo 裡有 8 處引用（`.sops.yaml`、`SCOPE.md`、
> `settings/env/README.md`、`.opencode/agents/*.md`、`scripts/env-sync.sh`）。

---

## ✅ wsl 端 M2 部署完成（2026-10-01 22:00 wsl 端實測）

x570 的 6 個 commit（`a950313`…`8d59bde`）已 pull 並部署。**這是往下走的前提**，
所以記錄在這裡 —— 輪換 peer key 之前這件事必須先成立。

### 部署

```bash
git pull --ff-only          # → 8d59bde
docker compose up -d        # 三個容器全 Recreate
```

`compose.yaml` 改了 healthcheck 與 `depends_on`，所以**必須 recreate**，
不是 `restart`。實測日誌看得到新的門控真的作用：

```
Container ragdemo-postgres-1  Recreated
Container ragdemo-qdrant-1   Recreated
Container ragdemo-api-1      Recreated
Container ragdemo-qdrant-1   Waiting
Container ragdemo-postgres-1 Waiting
Container ragdemo-postgres-1  Healthy
Container ragdemo-qdrant-1   Healthy
Container ragdemo-api-1      Starting     ← 底層 healthy 才起來
```

### healthcheck 現況

```
postgres  healthy
qdrant    healthy
api       （無 healthcheck，僅 running）
```

### ALT slot 實測：兩個槽都活著

wsl 這台 `QDRANT_PEER_API_KEY` 有值（43 字元），所以
`QDRANT__SERVICE__ALT_API_KEY: ${QDRANT_PEER_API_KEY:-}` 拿到真值。

```
本機 qdrant  self key → /collections  200
本機 qdrant  peer key → /collections  200   ← ALT slot 生效
本機 qdrant  亂數 key → /collections  401   ← 亂數仍被擋
```

**別把 peer key 當成「弱一點的 self key」** —— 同事實測確認它是讀寫槽
（`GET` 200 / `PUT` 200 / `DELETE` 200），而 `read_only_api_key` 不可用
（peer 需要 `POST :235` ＋ `DELETE :256/:263`）。

### 快照同步實測

```bash
bash scripts/sync-snapshot.sh     # exit 0，無輸出
cat data/laws/.law_version
```

```
{"update_date": "2026-09-18", "source": "http://100.119.83.111:6333",
 "synced_at": "2026-10-01 21:59:16"}
```

**資料面無損**（這是重點 —— 同事關掉的正是那個資料空窗）：

```
本機 laws  39,879 筆
x570 laws  39,879 筆   ← 兩邊一致
```

### host-doctor：0 fail / 2 warn（與 x570 同基準）

```
[ ok ] container:api / postgres / qdrant
[ ok ] env-check    per-host 值與總表一致（9 鍵，該機 wsl）
[ ok ] rotate-fp    8 把憑證的長度＋sha12 已取得
[warn] rotate-hint  同值是結構性必然，不要直接輪換 peer 那把
[warn] law-version  快照已 28 小時沒成功更新
```

**兩個 warn 都是預期的**：

- `rotate-hint`：同事已把那條文字改成明確警告（見 `settings/env/README.md §7`）
- `law-version`：`LAW_SYNC_SOURCE` 指向 x570（`100.119.83.111`，實測 laws
  可讀），但 x570 那台的 `law-update` 排程狀態仍待確認。**這個 warn 會在
  x570 恢復每日同步後消失**，現在亮著是誠實的。

### 端對端

```
localhost:8000/health              200
https://api-wsl.ragdemo.win/health  403   ← Access 正常
https://ragdemo.win/api/health      200  host_id=wsl
peer 探測  x570 ✅  mbp ✅  wsl ✅
```

### ✅ wsl 端驗證 Access Service Token 全鏈路通（2026-10-01 23:21）

`4a0bf52` 加的 peer 探測帶 token 已在 wsl 實測生效。**三段守衛全過**：

```
不帶 token      → 403   （Access 護著）
帶假 token      → 403   （驗簽：假 token 也被擋，這才是真的開著）
帶真 token      → 200   （值正確）
```

⚠️ **踩到一個坑，值得記：`docker compose up -d` 不會重建映像。**
第一次驗證時容器有拿到 token、但 peer 探測仍全紅 —— 因為
`ragdemo-api` 映像是三小時前 build 的，裡面的 `gateway.py` 根本沒有
`_access_headers`。`up -d` 只 Recreate 容器（換環境變數），**程式碼要
`up -d --build api`**。症狀會是「設定都對了但行為是舊的」。

另外：Service Token 的正確組合是 **Client ID 39 字元（hex）／Client Secret
54 字元（`cfast_` 開頭）**。貼反的話症狀是帶 token 也 403 —— 與
「Access 沒開」長得一模一樣，要靠 `scripts/access-check.sh` 第 3 段分辨。

驗證結果（wsl）：

```
peer 探測   x570 連線成功 / mbp 連線成功 / wsl 連線成功
sync        law version updated: 2026-09-18 -> 2026-09-18
.law_version synced_at = 2026-10-01 23:21:36
host-doctor law-version 那條 warn 消失（原本 warn 28 小時沒更新）
```

---

## ★★ 待辦（x570 端）：把 Access Service Token 加進本機 `.env`

**這是現在唯一還沒做的。** wsl 端已完成並驗證（見上），**x570 與 mbp 尚未**。

沒有它的症狀（都很安靜）：

- 前端「連線與來源」顯示 x570／mbp **連線失敗**（但查詢正常）
- `sync.log` 出現 `law version: 取不到` → `.law_version` 凍結在舊版
- `host-doctor.sh` 的 `law-version` **warn**（「快照已 N 小時沒成功更新」）

### ⭐ 步驟 1（2026-10-02 取代舊版）：pull，不要手動貼

```bash
git pull && bash scripts/env-sync.sh pull
bash scripts/env-sync.sh --fingerprints | grep CF_ACCESS
# 期望：
# CF_ACCESS_CLIENT_ID      len=39   sha12=11cb9bc42f55
# CF_ACCESS_CLIENT_SECRET  len=54   sha12=f00a6bd62ddc
```

⚠️ `pull` **不會覆蓋你的 `HOST_ID` 與 per-host 值**（`TS_IP`／`LLM_MODEL`／
`OLLAMA_*`）—— 那三類各機不同，總表對應欄空值＝沿用本機現值。實測
`render --dry-run` 只列 `HOST_API_URLS`，其餘鍵不動。

### 步驟 2：重建（`--build` 不能省）

```bash
docker compose up -d --build api
```

⚠️⚠️ **`--build` 是必要的。** `up -d` 只 Recreate 容器（換環境變數），
**不會重建映像** —— wsl 端第一次驗證時就踩到：容器裡有 token、但映像是
三小時前 build 的，`gateway.py` 根本沒有 `_access_headers`（實測容器內
`grep -c` = 0、工作區 = 3）。症狀是「設定都對了但行為是舊的」。

### 步驟 3：驗收（四項都要看）

```bash
docker compose ps
bash scripts/env-sync.sh --check          # per-host 值與總表一致
bash scripts/sync-snapshot.sh --force
tail -3 ~/qdrant/sync.log        # 應出現 law version updated，不是「取不到」
bash scripts/host-doctor.sh      # law-version 那條應從 warn 變 ok
```

peer 探測在**前端面板**看（`https://ragdemo.win` → 連線詳細），
或 `curl -s http://localhost:8000/status | python3 -m json.tool`。

### ⚠️ 這組值是跨機共用機密（2026-10-02 起已進 sops 層）

三台的 `.env` 用**同一組**，值只活在 `settings/env/secrets.common.enc.env`
（sops+age 加密）。`env-sync.sh pull` 分發，**不要**手動貼進 `.env`。
輪換用 `bash scripts/rotate-secret.sh CF_ACCESS_CLIENT_SECRET --from-stdin`，
三台一起 pull，中間會有一段 peer 探測全紅。順序見 `settings/env/README.md` §7。

**回報**：`--fingerprints | grep CF_ACCESS` 的兩行（不要回報 token 值）、
`sync.log` 最後 3 行、`host-doctor` 的 `law-version` 那條。

### 🗄 作廢的舊步驟（2026-10-02 之前，留著當反面紀錄）

<details>
<summary>舊步驟 1–2：手動貼 token</summary>

```bash
bash scripts/access-check.sh      # 互動式，token 用 read -rs 不落地
printf 'CF_ACCESS_CLIENT_ID=<39字元hex>\nCF_ACCESS_CLIENT_SECRET=<54字元cfast_>\n' >> .env
```

⚠️ **正確組合：Client ID 39 字元（hex）／Client Secret 54 字元（`cfast_` 開頭）。**
貼反的症狀與「Access 沒開」完全一樣（都回 403），只能靠 `access-check.sh`
第 3 段分辨。**這就是不要手動貼的理由** —— `pull` 不可能貼反。

</details>

<details>
<summary>舊步驟 4：三段守衛（現在由 --fingerprints ＋ peer 探測取代）</summary>

```bash
bash scripts/access-check.sh
# 不帶 token → 403（Access 護著）
# 帶假 token → 403（驗簽：假 token 也被擋，這才是真的開著）
# 帶真 token → 200（值正確）
```

仍可用於**診斷**（例如 peer 探測全紅時，分辨「Access 沒開」與「token 不對」），
但**取得** token 不再需要它。

</details>

---

### ✅ Access 三台已建好（2026-10-01 22:35 之後）

⚠️ **本段記的是 21:59 的量測，不是現況。** 那時 `api-mbp` 與 `api-x570`
**還沒有** Access app，所以回 200。三台的 Access app 已在 22:0x–22:2x 建好，
22:35 之後複驗是**三台全 403**（x570 端與 wsl 端各自量到一致）。

| 時間 | api-x570 | api-wsl | api-mbp |
|---|---|---|---|
| 21:59（本段原始量測）| 200 | 403 | 200 |
| 22:35（x570 端複驗）| **403** | 403 | **403** |

**兩個數字都是真的**，是狀態變了不是有人量錯。讀這份文件時請以 22:35 那列為準。

`API_ORIGINS` 已改成三台。Access 三台都有，所以 failover 用的每一台都受保護
—— 這是當初「先建 Access 再加回 `API_ORIGINS`」那個順序的目的。

驗證用 `scripts/access-check.sh`（x570 端提供）：第 2 段「帶假 token 也 403」
才是驗簽的證據，只看「不帶 token 是 403」無法區分「Access 開著」與
「只是沒有登入頁」。

---
## 現況：三台全綠，Access 已護三台（2026-10-02 wsl 端實測）

**2026-10-02 複驗，三台一致 403 —— 這就是健康的樣子**：

```
api-wsl.ragdemo.win    → HTTP 403   ← Access 擋著，正常
api-x570.ragdemo.win   → HTTP 403   ← 同上
api-mbp.ragdemo.win    → HTTP 403   ← 同上
ragdemo.win/api/health → HTTP 200   host_id=wsl
```

peer 探測（wsl 端，帶 Service Token）：`x570 連線成功 / mbp 連線成功 /
wsl 連線成功`。

> ⚠️ 本節上一版寫的是「`api-x570 200`、`api-mbp 200`、`api-wsl 403`」。
> 那組數字是 **2026-10-01 22:00 的量測**，Access app 當時還沒建好。
> 22:0x–22:2x 三台建好後，22:35 複驗就是**三台全 403**。
> **兩個數字都是真的**，是狀態變了不是有人量錯。以本段為準。
> 詳見〈✅ Access 三台已建好〉。

Cloudflare API：三條 tunnel **全部 healthy、conns=4**
（`ragdemo-x570`／`ragdemo-mbp`／`ragdemo-wsl`）。

| 狀態 | 意義 | 處置 |
|---|---|---|
| **530** | tunnel 沒連線（`cloudflared` 沒跑）| 拉起 tunnel |
| **502** | **tunnel 連著，但本機後端沒起來** | **查 `docker compose ps`** |
| **403** | Cloudflare Access 擋住了 | **正常**，見下 |
| **200** | 通了 —— **但不代表沒問題**，見下 |

⚠️ **200 也不等於健康。** 真正的驗收是 `host-doctor.sh`，不是 HTTP 狀態碼
（`MBP-HANDOFF.md`〈外部症狀不足以判斷〉記錄過：那次 `api-mbp` 回 200，
但容器是空的、映像落後 4 天、DSN 指向離線的別台讓查詢多 60s）。

### 為什麼三台現在都是 403

2026-10-01 補上了 Cloudflare Access（三台的 Access app 都在 22:0x–22:2x 建好）。
`fbb9325` 那個 commit 從 2026-09-30 就假設 Access 存在（它的註解寫「後端只綁
127.0.0.1，唯一入口是 tunnel，所以邊緣驗證就是完整防護」），但那個前提
**從來不成立** —— Access 直到 2026-10-01 才真的啟用。那段期間三個 API
hostname 都是全網可達的。

現在三台都是 **Service Auth 保護**：不帶 `CF-Access-Client-Id` /
`CF-Access-Client-Secret` 一律 403。

**若你看到 `api-x570` 是 403，那是正常的，不是故障。** 本機測試請用
`http://localhost:8000`，不要從公網打那個 host。

### 法規版本：✅ 已直接查上游確認，9/18 確實仍是最新（2026-10-01）

舊版這裡寫「三台 `law_version` 都是 `2026/9/18`，9/18 就是最新版，不用重跑」，
又說「那是 2026-09-30 的判斷，現在不能照做，請自己查上游」——
**兩句都不準。** 第一句的結論對，但理由錯（不能靠「三台一致」推論）；
第二句的懷疑合理，但**上游查完之後答案就是不用重跑**。

2026-10-01 x570 端**直接比對上游**（不是看本機的 `law_version`）：

```bash
.venv/bin/python ingest/laws/sync_daily.py     # 不帶 --apply = dry-run
→ INFO 開始每日檢查（--apply=False）；上次 sha=bc7402385de4 更新日=2026/9/18 上午 12:00:00
→ INFO 無更新：sha=bc7402385de4… update=2026/9/18 上午 12:00:00（法規 1347，條文 51343）
```

上游的 sha 與本機記錄的 sha **相同** → 沒有新版，**不需要重跑**。
2026/9/18 是真的最新，不只是「三台一起過期」。

**「一致也可能是一起過期」那個疑慮是對的**，但解法不是靠本機狀態猜，
而是**去比對上游** —— 而且要記得用 `.venv/bin/python`，
見事項 7a 的「紅鯡魚」小節（系統 `python3` 會 SSL 失敗，容易誤判成「查不到上游」）。

---

## 事項 5：~~報回 age 公鑰~~ —— ✅ 2026-10-01 確認已完成，不要再回報

**2026-10-01 wsl 端查證完成：**

```
.sops.yaml 的 age recipients = 3 把（wsl / x570 / mbp）
secrets.common.enc.env 內的 recipients = 3 筆
```

兩邊一致代表 **`sops updatekeys` 真的跑過** —— 只加公鑰不 updatekeys 是無效的
（那正是你 2026-09-30 回報過的症狀）。所以 `env-sync.sh pull` 在你這台應該通。

**若你的 pull 仍然失敗**，那不是公鑰問題，請直接回報錯誤訊息**第一行**：

```bash
bash scripts/env-sync.sh pull 2>&1 | head -1
```

⚠️ **絕對不要重建 `keys.txt`**（重建會讓已加密的檔案解不開）。
per-host 的 2 把機密（`QDRANT_API_KEY`／`POSTGRES_PASSWORD`）不在加密檔裡，
用你原本的值就對。

---

## ✅ 已解決：2026-09-30 10:01 的 `git reset` 吃掉了 3 個 commit（2026-10-01）

**如果你讀到這段，代表你在看一份過時的「首要事項」。** 那個 502 的根因已查清並修復，
以下是完整證據鏈 —— **重點不是這次怎麼修，是同樣的事還會再發生**。

### 症狀：`api-x570` 502，而且「自動 up」消失

`docker compose ps` 是**完全空的**（0 個容器），但 `docker system df` 顯示
**image 全在、named volume 全在**：

```
Images      7 個（ragdemo-api:latest / pgvector / qdrant…）
Containers  0 個                              ← 只有這個是 0
Local Volumes 2 個  ragdemo_pg_data / ragdemo_qdrant_data
```

「image 與 volume 完好、只有容器消失」是 `docker compose down` 的指紋
（刪容器、保留 named volume）。**資料沒事，不需要重建。**

### 真正的機制：從來不是 Docker 自動 up

你會記得「進系統 docker 就自己 up 了」，那是**三層機制疊出來的錯覺**：

| 層 | 內容 | 能不能「建立」容器 |
|---|---|---|
| 1 | `compose.yaml` 的 `restart: unless-stopped` | **不能**。只重啟**已存在**的容器，daemon 重開時用 |
| 2 | cron watchdog：`ensure-stack.sh --cron`（@reboot ＋ `*/10`）| **這才是**。冪等跑 `docker compose up -d` |
| 3 | `docker.service` 是 `enabled` | 不能，只是讓 daemon 開機起來 |

所以真相不是「開機自動起」，而是「**10 分鐘內被 watchdog 補回來**」——
你自然會歸因成 Docker 自動啟動。**第 1 層救不了被刪掉的容器**，
這次就是這樣：容器沒了，第 1 層無能為力，而第 2 層的腳本已經不在了。

### 根因：`scripts/ensure-stack.sh` 被 `git reset` 丟掉

crontab 是 per-host、不在版控裡，所以**排程活著、腳本死了**，每 10 分鐘
cron 都在失敗，而且安靜地失敗。

那個腳本**從未被 commit 過** —— 它只是某台機器上手放的未追蹤檔。
然後：

```
9e16aa8  HEAD@{2026-09-30 10:01:57}: reset: moving to origin/main
```

而 `data/laws/ensure-stack.log` 最後一行是 `[2026-09-30 10:00:01] OK` ——
**差 56 秒**。reflog 是這次唯一能還原真相的地方。

旁證：那個 reset 丟掉的不只一個檔案。`compose.yaml` 當時有
`healthcheck:` 與 `depends_on: condition: service_healthy`，**reset 後的 main 沒有**
（是裸的 `depends_on: [qdrant, postgres]`）→ 那個修復也一起被回退了。

### 2026-10-01 已修復（3 個 commit）

| commit | 內容 |
|---|---|
| `a950313` | `scripts/ensure-stack.sh` 回版控（65 行，不印憑證，判準是 `/health` 200 ＋ 三容器 running，不健康才 `--force-recreate`）|
| `8b11e2d` | compose.yaml：三服務 `restart: always` ＋ healthcheck ＋ `depends_on: condition: service_healthy` |
| `6427273` | 重新產生 `.env.example` |

⚠️ **前兩個 commit 讓 compose.yaml 多 27 行，`.env.example` 裡 112 處
「讀取處：compose.yaml:NN」註解全部位移**，於是
`tests/test_env_audit.py::test_template_has_no_lan_ip_assignment` 失敗
（它斷言 `.env.example` 與 `--template` 產出**逐字相同**）。
症狀看起來完全不相關。修法：`python3 scripts/env-audit.py --template > .env.example`。

**任何改 `compose.yaml` 行數的 commit，都要跟著重產 `.env.example`**，
否則 pre-push 的 pytest 會擋你 —— 那時你會誤以為是自己改壞了別的地方。

救援分支 `x570-rescue-20260930`（另有 `...b` 備援）仍指向原始的 `19a8128`，
objects 在 gc 前一直撐著。確認 main 已完整後可以刪。

### 第三個 commit（`19a8128`）**刻意沒有併回來**

它改 HOST-UPGRADE.md（46 行）與 ROADMAP.md（5 行），內容是 2026-09-26 的
「手動金鑰輪換流程」：`ssh solo@x570 "grep … .env"` 抓新值、指紋表
`fe4b2d4ba82a`、以及「§0b .env 只有一個」（與 main 現有的 §0 重複）。

**兩半都過期了，救回來是負資產**：

- `§0b` 與 main 現有的 `## 0. .env 只有一個` 重複 —— 那段已經被改寫收斂
- 手動輪換流程被 `scripts/rotate-secret.sh`（2026-09-30）取代，而它的做法
  （ssh 過去 grep `.env`）正是本專案明文禁止的
- 指紋 `fe4b2d4ba82a` 對現在的 key 已經無效（key 模型 2026-09-27 換成
  「共用層 6 把 ＋ per-host 2 把」）
- ROADMAP 那條第 7 點寫「三台共用同一組，x570 已換完，mbp/msi 需接手」——
  事實上已不成立，寫回去會誤導

**教訓**：救援不是無條件 restore。dangling commit 可能是「被回退是有原因的」
或「已被後續架構取代」。逐個 commit 看內容再決定，
`git show <sha> --stat` 加讀檔案，別只看 commit message。

---
## 事項 6：git hooks —— ✅ 2026-10-01 確認已在位，不用動

```bash
git config --get core.hooksPath || echo "✗ 沒設"
```

x570 實測回 `.githooks`，`.githooks/commit-msg` 與 `.githooks/pre-push` 都在，
2026-10-01 那次 push 也真的跑過（`✓ pytest 338 passed`、`✓ compose 插值 OK`）。

沒輸出才要設：`git config core.hooksPath .githooks`

per-clone 的 git 設定，不在版控裡。漏了**不報錯**，只是 pre-push 的三項檢查
（commit 前綴／語法＋`LAN_IP=`／pytest）靜默消失 —— 症狀要等到違規 commit 上線
才浮現，那時已難回溯是哪台機器按的。

實測過：msi 重灌後這項不見了，連續 11 個 commit 在無任何檢查下產生。

驗證：`git push --dry-run origin main` 應看到「✓ pytest N passed」。

---

## 事項 7：兩件事都已查證（7a 另發現一個要移交的問題）

### 7a. 你是「來源機」，確認排程有沒有在跑

`scripts/law-update-worker.sh` 的角色判斷**只看一個檔案**在不在：

```bash
if [ -f "$ROOT/data/laws/.law_sync.json" ]; then
  ROLE="source"; CMD="uv run ingest/laws/sync_daily.py --apply"
```

你的 `/status` 回報 `law_version 來源 = law_sync.json` → **系統判定你是 `source`**。
（與 2026-09-26 版的前提不同：當時「x570 是唯一來源機」，現在 wsl 也跑完
`sync_daily` 了，**兩台都是 source**，彼此不依賴。這是好事。）

```bash
crontab -l | grep law-update-worker || echo "✗ 沒有 law-update-worker 排程"
ls -la --time-style=long-iso data/.ops/.worker.lock     # ← 這個才看得出排程有沒有跑
ls -la --time-style=long-iso data/laws/.law_sync.json
```

路徑依據 `law-update-worker.sh:18`（`OPS="$ROOT/data/.ops"`）。

> **★ 2026-10-01 更正：本節原本叫你檢查 `law-update.status` 並用「檔案不存在」
> 推論 worker 從未執行過。那個推論是錯的，別再照做。**
>
> `law-update-worker.sh:53` 是 `[ -f "$REQ" ] || exit 0` —— 沒有人按前端
> 「更新」鈕、沒有請求檔時，worker **靜默退出、什麼都不寫**。所以
> `law-update.status` 不存在是**正常狀態**，不是故障。
>
> **要看排程有沒有在跑，看 `.worker.lock` 的 mtime**：worker 每次被 cron 叫到
> 都會 `exec 9>"$OPS/.worker.lock"` 開一次（`:47`），所以那個檔的 mtime 應該
> 不超過一分鐘。x570 實測 mtime 與當下時間差 < 60 秒 → 排程正常。
>
> 舊版那句「✗ 沒狀態檔 → worker 從未執行過」會把一台**完全正常**的機器標成故障。

**判讀**：有排程 ＋ `.worker.lock` mtime 在 1 分鐘內 → 排程正常；
有排程但 `.law_sync.json` 的 `last_checked` 很久以前 → 那**不是排程沒跑**，
是排程跑了但**跑失敗**（見下方 x570 實測的兩個實例）。
**沒有排程也先不要自己加** —— 回報後由 wsl 端決定（前端「更新」按鈕是走這條路，
加錯了會跟別的排程打架）。

#### ⚠️ x570 實測：兩條 cron 各自靜默失敗過（2026-10-01，請交 ingest／ops 處理）

兩條都是 per-host、不在版控裡、**失敗時完全沒有聲音**。這台機器上三條 cron 有兩條壞過：

| cron | 症狀 | 根因 |
|---|---|---|
| `ensure-stack.sh`（@reboot ＋ `*/10`）| 後端沒起來，`api-x570` 502 | 見〈已解決：2026-09-30 的 git reset 事件〉|
| `30 6 * * * .venv-ingest/bin/python ingest/laws/sync_daily.py --apply` | `last_checked` 凍結在 2026-09-26T21:47 | **`.venv-ingest` 這個 venv 不存在**。只有 `.venv`（`pyproject.toml` 明寫「統一以 uv 管理環境」）|

後者讓每日檢查**連續失敗 6 天而無人察覺**。所以「`last_checked` 很舊」的第一個
解釋不是「沒人跑」，而是「跑了但失敗」—— 先確認 interpreter 存在：

```bash
crontab -l | grep sync_daily          # 看它指向哪個 python
ls -d .venv .venv-ingest 2>&1        # 指向的那個在不在
```

**x570 端刻意沒有自行修改這條 crontab**（修正既有排程不在本 handoff 的授權範圍）。
交由 ingest／ops 端統一處理三台，避免各機各自發明寫法（`python` 絕對路徑 vs
`uv run` 會讓三台再產生一種分歧）。

#### 查上游時的紅鯡魚：用系統 `python3` 會 SSL 失敗

```bash
python3 ingest/laws/sync_daily.py
→ 下載失敗：SSL: CERTIFICATE_VERIFY_FAILED … Missing Subject Key Identifier
   （連續 3 次後「同步中止（未更動資料）」）

.venv/bin/python ingest/laws/sync_daily.py
→ 無更新：sha=bc7402385de4… update=2026/9/18 上午 12:00:00（法規 1347，條文 51343）
```

同一台機器、同一個上游，**換 interpreter 結果相反**。系統 `python3` 的憑證庫
對 `law.moj.gov.tw` 的憑證鏈處理不了，`.venv` 裡的可以。
**看到那個 SSL 錯誤不要回報成「上游連不上」或「法規站掛了」** —— 先換
`.venv/bin/python` 再說。

### 7b. `env-sync.sh render` 可以跑，但先看 dry-run（★2026-10-01 推翻舊指示）

> **★ 舊版這裡寫「先不要跑 `env-sync.sh render`」，理由是「它會用總表的空值生成
> `.env` → `OLLAMA_URLS=` 空值 → `gateway.py` 退回原始碼預設 → 查詢靜默降級」。
> 那個理由與現行程式碼相反，別再照做。**

`settings/env/hosts.shared.env` 的 `x570_TS_IP`／`x570_HOST_MACHINE_ID`／
`x570_OLLAMA_URLS`／`x570_OLLAMA_MODELS` **刻意留空**（IP 不進被追蹤的檔案）。
對運作**無影響** —— `/health` 的 `machine_id`／`ips`／`llm_src` 是你自己心跳寫進
自己 pg 的（實測 `machine_id=af8eaa67…`、`ips=["100.119.83.111"]` 都正常）。

**空值在這個專案裡的語意是「該機沿用自己現值」，不是「清空」。** 兩處實作：

```python
# env-sync.sh:205-208　總表逐列挑本機那欄
for base, cols in sorted(table.items()):
    v = cols.get(host, "")
    if v:                      # 空值＝該機沿用自己現值，不覆蓋
        layer[base] = v

# env-sync.sh:239-240　「生效值」＝總表有值用總表、否則沿用 .env 現值
def eff(key):
    return layer.get(key) or cur.get(key) or ""
```

所以 x570 跑 `render` **不會**把 `OLLAMA_URLS` 寫成空值 —— 總表那欄是空的，
`if v:` 判不到，`layer` 裡根本不會有這個鍵，本機現值原封不動。

**2026-10-01 x570 實測**（順手修掉一個 msi→wsl 改名殘留時順便驗的）：

```bash
bash scripts/env-sync.sh render --dry-run
→ HOST_API_URLS          會覆寫
  env-sync: dry-run，4 個鍵在表內、1 個與 .env 不同（值不顯示）
```

`OLLAMA_URLS`／`OLLAMA_MODELS`／`LLM_MODEL` **都不在列表裡** —— 舊版擔心的
那三個鍵沒有任何一個會被動。`render` 完再比對備份，**鍵集合零變化**，
只有 `HOST_API_URLS` 一個鍵的值改變（`msi=` → `wsl=`），兩個憑證的指紋不變。

**正確做法是先 dry-run**（只印鍵名不印值，安全）：

```bash
bash scripts/env-sync.sh render --dry-run    # 看會動哪幾個鍵
bash scripts/env-sync.sh render              # 確認無誤才寫
bash scripts/env-sync.sh --check             # 應該回 per-host 值與總表一致
```

**改完 `.env` 記得讓容器重讀** —— 環境變數是啟動參數，而且這台現在是
`restart: always`（見〈已解決：2026-09-30 的 git reset 事件〉），**`restart`
只重啟進程、不重讀 `.env`**：

```bash
docker compose up -d        # 會 recreate；單用 docker compose restart 不夠
```

實證：`gateway.py:113` 的 `HOST_API = _parse_id_urls(os.getenv("HOST_API_URLS",""))`
是**模組層級常數**，容器啟動時讀一次。改完 `.env` 但沒 `up -d`，`/query` 回來的
`log` 就還是舊的主機名。

**若你確實需要改模型設定**，先回報（不要用 `cat .env`，會連帶印出兩個機密）：

```bash
grep -E '^(OLLAMA_URLS|OLLAMA_MODELS|LLM_MODEL)=' .env | cut -c1-60
curl -s -m 5 http://localhost:11434/api/tags -o /dev/null -w "ollama HTTP %{http_code}\n"
ollama list
```

你的 LLM 是 `qwen3:14b`（總表 `x570_LLM_MODEL=qwen3:14b` 已填好），
**不要**改成 wsl 的 `qwen2.5-coder:latest`（那是另一台的設定）。

---

## 不需要做的事（舊版的事項，已作廢 —— 別去查）

- **輪換 qdrant key 到同一把** —— 2026-09-27 起改為共用（2026-09-30 為 6 把）＋ 2 把 per-host
  機密，per-host 本就該各機不同。舊指示會製造三台共用同一密碼的問題
- **查 x570 的 pg 密碼／`POSTGRES_PEER_PASSWORD`** —— **沒有任何程式讀它**
  （全 repo 只剩註解、`.example` 說明文字、測試 docstring）。當初要查是因為
  三台 `/hosts` 指向同一個 pg，現在各讀自己的，鎖死不存在了。**不要為了填它去
  查密碼，也不要加那個鍵** —— 加了是幽靈鍵
  （對照：`QDRANT_PEER_API_KEY` 真的有人在讀，`sync-snapshot.sh:61`）
- **2026-09-26 異常關機的根因** —— 從那之後持續運行至今，沒有重現的環境
- **動 `SRC_API_URL`** —— 它指向你自己，但你是 source 機、不走
  `sync-snapshot.sh` 那條路，現在無害。改它等於製造第三種狀態

---

## 回報格式

**2026-10-01：x570 端全部執行完畢。以下是實際回報的結果，供下一手對照。**

| 項目 | 結果 |
|---|---|
| 本機後端 | ✅ 三容器 running，`host-doctor.sh` 12 過 / 1 fail / 2 warn，`api-x570` 200 |
| 事項 5（age 公鑰）| ✅ 已完成（2026-10-01 上游確認）|
| 事項 6（hooksPath）| ✅ `core.hooksPath = .githooks`，`commit-msg`／`pre-push` 都在 |
| 事項 7a（排程）| ⚠️ 排程**有**且在跑（`.worker.lock` mtime < 60s）；但每日 06:30 那條指向不存在的 `.venv-ingest`，**已移交 ingest／ops，x570 未自行修改** |
| 法規版本 | ✅ **上游確實沒更新** —— `.venv/bin/python ingest/laws/sync_daily.py` 實測回「無更新：sha=bc7402385de4… update=2026/9/18（法規 1347，條文 51343）」。9/18 仍是最新，不需重跑 |
| `HOST_API_URLS` | ✅ 已修（`.env` 還是 `msi=`，總表已 `wsl=`）—— `env-sync --check` 現回「per-host 值與總表一致（4 鍵，該機 x570）」 |

剩餘 1 fail ＋ 2 warn（皆非本機後端問題）：

- `[warn] rotate-hint` `QDRANT_PEER_API_KEY` 與 `QDRANT_API_KEY` 同值 → 已交
  env-ops 輪換 peer 那把
- `[warn] law-version` 無 `data/laws/.law_version` —— **措辭是寫給備援機的**
  （該檔由 `sync-snapshot.sh` 帶過來）。x570 是 source 機、有 `.law_sync.json`，
  本來就該沒有它；`law-update-worker.sh:110-117` 本來就是先讀它再 fallback
- `git reset` 事件 → 已修復並 push，另留救援分支 `x570-rescue-20260930`

**下一手拿到這份文件的時候，請先跑 `bash scripts/host-doctor.sh`** ——
本文件的結論都帶日期，`api-mbp` 那台也還在 502。

---

## 回報的硬性規則（不變）

**不要回報任何憑證值。** 要證明某鍵有值，打印長度或前 3 個字元就夠
（`${#v}` 或 `echo "${v:0:3}…"`）。**age 私鑰（`age-secret-key1...`）絕對不能回報。**

若 `api-x570.ragdemo.win` 當下不是 200，一併回報 HTTP 狀態碼與
`curl -sS -D-` 的**前 10 行**（response header，不是 body）。

⚠️ **502 與 403 的意思不同，不要一起當故障回報**：

| 你看到 | 回報時寫 |
|---|---|
| `api-x570` **502** | 本機後端沒起來（要修）。**先查 `docker compose ps`，不要動 cloudflared／DNS** |
| `api-mbp` **502** | **不是這台的事**，回報即可（mbp 的後端沒起來）|
| `api-wsl` **403** | 正常，Access 擋著，不用修 |
