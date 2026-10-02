# mbp 交接（2026-09-30）

給 **mbp 上的 opencode** 讀。**逐項查證後回報，不要先假設原因。**

## ★★★ 2026-10-02 23:11 最新待辦（其下章節是歷史，別照舊指示做）

### 1. `git pull` → 你回報時的 `0be580b` 之後有 4 個 commit，其中一個是**修你的 bug**

```bash
git pull
docker compose up -d --build api      # 你說已 rebuild，但那次是 pull 之後
bash scripts/host-doctor.sh
```

你回報的 3 項 fail，**兩項是同一個根因、一項是我寫錯**：

| 你回報的 | 根因 | 狀態 |
|---|---|---|
| `[FAIL] code-drift` | pull 沒 rebuild api（新增 `cloud_probe.py`／`host_settings.py`／`readiness.py`，舊映像沒有）| **已修版會自己消失** |
| `[FAIL] readiness HTTP 404` | 同上 —— 舊程式碼沒有 `/ready` 這個端點 | 同上 |
| `[FAIL] sops rc=127: timeout: command not found` | **我的 bug**：我寫 `timeout 60 sops`，而 **macOS 沒有 `timeout`** | **已在 `1d38a13` 修好**（可攜的 `run_with_timeout`：`timeout` → `gtimeout` → 直接跑） |

⚠️ 那一項的 fail 理由**看起來像「sops 解不開」，實際是「指令不存在」** ——
症狀指向完全錯誤的方向。所以 pull 之後請**確認 sops 那條真的轉綠**，不要只看
「沒有 fail 就好」。

### 2. 確認 `OLLAMA_URLS` 清空完成了（**我沒有你的回報**）

下方「☆☆ 最優先」那節指派了這件事，但還沒看到你執行後的回報。驗收兩項：

```bash
bash scripts/env-sync.sh --check | head -1      # 必須是 27 keys, layout sha12=eab0ab8f9d4e
curl -s -o /dev/null -m 150 -w '  /query HTTP %{http_code}\n' \
  -X POST http://localhost:8000/query -H 'Content-Type: application/json' \
  -d '{"question":"測試","top_k":2}'
```

⚠️ **`/query` 才是真驗收，`/health` 抓不到 ollama 掛掉** —— 我在 wsl 實測過：
刪掉那個鍵之後 `/health` 全程 200，是後來手動打了一次 `/query` 才發現 RAG 已斷。

### 3. 不必重跑的事

* **新的 `source-sync-*` 檢查對你會是 `skip`** —— 那是給 source 機的，你是備援機
  （靠 `sync-snapshot.sh` 帶快照）。**skip 是正確的，不是故障。**
* `law-version` 那條 warn（快照 23 小時沒更新）是**上游 `law.moj.gov.tw` 回 HTTP 500**
  造成的，**不是你的設定問題**，不要去動它。
* `rotate-hint` 你回「peer 與本機 key 不同值（已拆分，正確）」—— 那是三台裡
  **唯一正確**的狀態，wsl／x570 還沒拆。**不要配合他們改回去。**

### 4. 回報請貼這些

`bash scripts/host-doctor.sh` 的**完整輸出**（值一律不顯示，那是腳本自己的行為），
外加第 2 項那兩行的結果。

## ☆☆ 已指派、等你回報：`OLLAMA_URLS` 清空（2026-10-02 20:10，使用者決定：選 (b)）

**一行的事。把那個值清空就好，不要刪掉那一行。**

```bash
sed -i.bak 's|^OLLAMA_URLS=.*|OLLAMA_URLS=|' .env && rm -f .env.bak
bash scripts/env-sync.sh --check
```

**為什麼是「清空」而不是「刪除」**：使用者原本指示「三台統一全刪」，但我在 wsl
做了 A/B 實測（同一台機、同一個問題、只差這個鍵）：

| | 結果 |
|---|---|
| 刪掉 `OLLAMA_URLS` | 查詢 **HTTP 500**，`httpx.ConnectError: ollama unreachable`，掛在 **`/api/embed`** |
| 放回去 | 查詢 **HTTP 200**（2.9s）|

`EMBED_MODEL=bge-m3:latest` 是 **ollama 模型**，所以每次查詢都要先算嵌入 ——
**沒有 ollama 位址＝整條 RAG 斷掉**，而 `/health` 全程 200 抓不到。

而「全刪」會讓規格一致嗎？**會，而且更糟**：`OLLAMA_URLS` 是 per-host 鍵，
刪掉之後連鍵都不在三台的 `.env` 裡，但 wsl 需要它有值。

**清空同時達成兩件事**：

* 三台的 `.env` 都有 `OLLAMA_URLS=` 這一行 → **鍵集合相同 → 指紋相同**
* 你那個值是「總表 `mbp_OLLAMA_URLS` 是空的、你卻有值」——清空後就與總表一致
* `--check` 的 `⚠️ 沿用自己的值` **只在有值時觸發**，清空後不再報

wsl 已實測：清空後 `--check` **乾淨**（無 ⚠️），指紋不變。

⚠️ **不要順便跑 relayout 或 prune。** 你已經遷移過了，這一步只改一個值。
跑完 `--check` 應該是 `27 keys, layout sha12=eab0ab8f9d4e`，把那行貼回來。

### 順手確認：清空後你這台的查詢還正常嗎

`OLLAMA_URLS` 是 gateway 連 ollama 的位址清單。清空後你這台會落到 compose
預設 `http://host.docker.internal:11434`。你沒有本機 ollama，所以那個位址
**連不上是預期中的** —— 只要**查詢仍然正常**就不影響你：

```bash
curl -s -o /dev/null -m 150 -w '  /query HTTP %{http_code}\n' \
  -X POST http://localhost:8000/query -H 'Content-Type: application/json' \
  -d '{"question":"測試","top_k":2}'
```

⚠️ **那個查詢才是真正的驗收。** `/health` 不會抓 ollama 掛掉 —— 我在 wsl 刪掉
這個鍵之後 `/health` 一直是 200，是後來手動查了一次才發現 RAG 已經斷了。

## ✅ 已完成：三機 `.env` 標準規格（2026-10-02 19:00）

版面指紋已達成 **`27 keys, layout sha12=eab0ab8f9d4e`**，與 wsl／x570 相同。
從 422 行、沒有註解、憑證靠手貼 → 27 鍵同規格、10 把共用憑證走 sops 分發、
never-effective 鍵帶理由註解，`--check` 三層全綠。

§一 B（`POSTGRES_DSN`）**已由 x570 回報並實測確認是假警報**：三台的 DSN 都是
`${POSTGRES_PASSWORD}` 佔位符，每次都從該機自己的密碼重新推導，**沒有靜態憑證、
沒有漂移風險**。我原本在規格裡把風險方向說反了，已在 `ENV-SPEC.md §一 B` 修正。

## 現況：三台全綠，Access 已護三台（2026-10-02 wsl 端實測）

**2026-10-02 複驗，三台一致 403 —— 這就是健康的樣子**，不是故障：

```
api-wsl.ragdemo.win    → HTTP 403   ← Access 擋著，正常
api-x570.ragdemo.win   → HTTP 403   ← 同上
api-mbp.ragdemo.win    → HTTP 403   ← 同上
ragdemo.win/api/health → HTTP 200   host_id=wsl
```

peer 探測（wsl 端，帶 Service Token）：`x570 連線成功 / mbp 連線成功 /
wsl 連線成功`。

> ⚠️ 本節上一版寫的是「`api-mbp 200`、`api-x570 200`、`api-wsl 403`」。
> 那組數字是 **2026-10-01 22:00 的量測**，你的 Access app 當時還沒建好。
> 22:0x–22:2x 三台的 Access app 建好之後，22:35 複驗就是**三台全 403**。
> **兩個數字都是真的**，是狀態變了不是有人量錯。讀這份文件時以本段為準。

**你這台不再是全網可達的** —— 沒有 `CF-Access-Client-Id`／`-Secret` 的人
打 `api-mbp.ragdemo.win` 會拿到 403，Access 的登入頁只對瀏覽器有意義
（後端與 Pages 都用 Service Token 進來）。

| 狀態 | 意義 | 處置 |
|---|---|---|
| **530** | tunnel 沒連線（`cloudflared` 沒跑）| 拉起 tunnel |
| **502** | tunnel 連著，但本機後端沒起來 | 查 `docker compose ps` |
| **403** | Cloudflare Access 擋住了 | **正常** |
| **200** | 通了 —— **但不代表沒問題**，見下 |


⚠️ **200 不等於沒問題。** 這段只是背景，不能當驗收 —— 你 2026-09-30 回報過
`api-mbp` 明明 200，但容器是空的、映像落後 4 天、DSN 指向離線的 x570 讓查詢
多 60s。理由見下方〈外部症狀不足以判斷〉。

### 為什麼三台現在都是 403

2026-10-01 補上了 Cloudflare Access（三台的 Access app 都在 22:0x–22:2x 建好）。
`fbb9325` 那個 commit 從 2026-09-30 就假設 Access 存在，但**那個前提從來
不成立** —— Access 直到 2026-10-01 才啟用。那段期間三個 API hostname 都是
全網可達的。

現在三台都是 **Service Auth 保護**：不帶 `CF-Access-Client-Id` /
`-Secret` 一律 403。建 app 的過程見〈✅ 建 Access app〉（已完成，你不用做）。
`API_ORIGINS` 也已改成三台，failover 用的每一台都受保護。

---

## 事項 5：~~報回 age 公鑰~~ —— ✅ 2026-10-01 確認已完成，不要再回報

**2026-10-01 wsl 端查證：`.sops.yaml` 三把公鑰都在，加密檔內三筆 recipients。**
兩邊一致代表 `sops updatekeys` 真的跑過（只加公鑰不 updatekeys 是無效的）。

若你的 pull 仍失敗，那不是公鑰問題 —— 回報錯誤訊息**第一行**：

```bash
bash scripts/env-sync.sh pull 2>&1 | head -1
```

⚠️ **絕對不要重建 `keys.txt`**。per-host 的 2 把機密不在加密檔裡，用原本的值。

---

## ✅ 建 Access app —— 已於 2026-10-01 完成

**原待辦（wsl 端 22:00 記錄）：** `api-mbp` 與 `api-x570` 回 200 不是 403，
代表沒有 Access 保護，而 `API_ORIGINS` 逐漸要把它們加回去。只護一台時
failover 會送到未護住的那台，驗證等於不存在。

**22:0x–22:2x 三台的 Access app 都建好了**，22:35 複驗三台一致 403。
你不需要做任何事 —— 這是 Cloudflare 後台的操作，本機改不了。

**驗證方式**：`scripts/access-check.sh`（x570 端提供）。第 2 段「帶假 token
也 403」才是驗簽的證據；只看「不帶 token 是 403」無法區分「Access 開著」與
「只是沒有登入頁」。

### 帶了 Access 之後，後端也要有 Service Token（2026-10-01 已補）

Access 護了三台之後，**peer 之間的探測也被擋**。症狀極其安靜：

- 前端「連線與來源」顯示 x570／mbp 連線失敗
- `sync-snapshot.sh` 的 `law version: 取不到` → `.law_version` 凍結在舊版
  （快照本身 OK，因為走 `LAW_SYNC_SOURCE` 的 tailscale 位址，不經過 Access）

`gateway.py` 的 `_access_headers()` 與 `sync-snapshot.sh` 讀同一組
`CF_ACCESS_CLIENT_ID`／`CF_ACCESS_CLIENT_SECRET`。**值與 Pages 那組相同。**

⚠️ 這組值是**跨機共用機密**，`.env` 兩台都要有。要輪換就三台一起換，
中間會有一段 peer 探測全紅。

---
## 事項 6：git hooks（`git pull` 不會帶過來）

```bash
git config --get core.hooksPath || echo "✗ 沒設"
```

沒輸出就設：`git config core.hooksPath .githooks`

per-clone 的 git 設定，不在版控裡。漏了**不報錯**，只是 pre-push 的三項檢查
（commit 前綴／語法＋`LAN_IP=`／pytest）靜默消失 —— 症狀要等到違規 commit 上線
才浮現，那時已難回溯是哪台機器按的。

實測過：2026-09-28 那次重灌後這項不見了，連續 11 個 commit 在無任何檢查下產生。

驗證：`git push --dry-run origin main` 應看到「✓ pytest N passed」。

---

## 事項 7：讓法規快照同步真的運作 ★這件會擋住規則題庫★

### 現況

`api-mbp /status` 的 `law_version = {}` —— 因為 `data/laws/` 是空的。
**查詢不受影響**（payload 都在 qdrant 裡，實測 cos@0.73 正常回法條），
但規則題庫拿不到後設資料（api log 有 `laws_meta 未找到`），
`host-doctor` 的 law-version warn 也會一直亮著。

### 缺什麼

**兩件事，缺一不可。**

```
1. .env 的 LAW_SYNC_SOURCE=<來源機的 qdrant 位址>
2. launchd 的 com.ragdemo.sync-snapshot（每 10 分鐘）
```

我實測過：`grep -c LAW_SYNC_SOURCE .env` → 0（空值），
`launchctl list | grep sync-snapshot` → 沒有。兩件都缺，所以
`sync.log` 是 0 bytes。

### 來源機填哪台：**只能填 x570**

我查過三台的 qdrant 綁定：

| 主機 | `TS_IP` | 實際綁定 | 別台能連嗎 |
|---|---|---|---|
| **wsl** | 空 | `127.0.0.1:6333` | **不能**（只在本機） |
| **x570** | `100.119.83.111` | `100.119.83.111:6333` | 能（你已實測過） |
| **mbp**（你自己）| 依本機 | — | 你不需要這條路徑 |

wsl 的 `TS_IP` 是空的（IP 不進被追蹤的檔案，所以總表 `wsl_TS_IP` 刻意留空），
所以 **wsl 的 qdrant 從別台連不到**。你實測過 `100.119.83.111:6333` 通 —— 那
就是 x570。

> **2026-10-02 更新**：這台（跑後端的那台）的代號已從 `msi` 改成 **`wsl`**
> —— 後端跑在 WSL 上，WSL 自己有 tailscale、hostname 是 `wsl`。
> `msi` 現在指的是同一台實體機器的 **Windows 主機**（ollama 跑在那裡，
> `wsl_OLLAMA_URLS=msi=http://msi.…:11434` 是**活的值**，別動它）。
> 所以本文件以下出現 `msi` 的地方，指的都是 `wsl` 這台後端主機。

```bash
# LAW_SYNC_SOURCE 是**位址不是機密**，所以直接用 sed 換掉那一行就好
# （.env 裡這行目前存在但值是空的，sed 的 pattern 要抓整行）
sed -i.bak 's|^LAW_SYNC_SOURCE=.*|LAW_SYNC_SOURCE=http://100.119.83.111:6333|' .env
rm -f .env.bak
grep '^LAW_SYNC_SOURCE=' .env          # 確認有值
```

**先手動跑一次確認能通，再設排程**（不要先設排程才發現位址錯）：

```bash
bash scripts/sync-snapshot.sh
tail -5 ~/qdrant/sync.log          # 應看到拉快照／上傳還原的記錄，不是一行 offline
cat data/laws/.law_version          # 應出現。
                                     # ⚠️ 2026-10-01：舊版寫「應為 2026/9/18」
                                     # 是當時的觀察值，不是驗收條件 —— 上游可能
                                     # 已有新版。回報你實際看到的值即可。
```

（`sync-snapshot.sh` 不帶參數時會讀 `LAW_SYNC_SOURCE`，所以 `.env` 填好之後
直接跑空參數即可 —— 那也正是排程會用的呼叫方式。）

⚠️ **需要 `QDRANT_PEER_API_KEY` 與 x570 的 qdrant 相符。** 三台目前同一把
（`sha12=fe4b2d4ba82a`），所以照現況會通。`sync-snapshot.sh` 的
`auth_precheck` 會在訊息裡點名是哪一把的問題。

### 步驟 2：設 launchd（mbp 的 crontab 被 macOS TCC 擋）

```bash
mkdir -p ~/Library/LaunchAgents ~/projects/ragdemo.win/data/.ops
REPO="$HOME/projects/ragdemo.win"     # 換成你的實際路徑

cat > ~/Library/LaunchAgents/com.ragdemo.sync-snapshot.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ragdemo.sync-snapshot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO/scripts/sync-snapshot.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$REPO/data/.ops/sync.log</string>
  <key>StandardErrorPath</key><string>$REPO/data/.ops/sync.log</string>
</dict>
</plist>
PLIST

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ragdemo.sync-snapshot.plist
launchctl list | grep sync-snapshot      # 應出現
```

`StartInterval` 是 **600 秒（10 分鐘）**，不是 60 —— 快照是整份 laws 集合，
每分鐘拉太浪費頻寬。`law-update-worker.sh` 才用 60（它只是查有沒有排入請求）。

### 步驟 3：另外確認 law-update worker 有沒有登錄

`HOST-UPGRADE.md` §4 有 `com.ragdemo.law-update` 的 plist。那支是執行前端
「更新」按鈕的（不同用途）。兩個都要：

```bash
launchctl list | grep -E "sync-snapshot|law-update"
```

### 回報

1. `LAW_SYNC_SOURCE` 有沒有值（**只報有無與主機名，不要貼完整位址**）
2. 手動跑 `sync-snapshot.sh` 之後 `cat data/laws/.law_version` 的內容
3. `launchctl list | grep -E "sync-snapshot|law-update"` 的輸出
4. `bash scripts/host-doctor.sh` 的摘要（law-version 那條 warn 應消失）

---

## 不需要做的事（別去查）

- **裝 cloudflared／動 DNS** —— tunnel 早就通了（`ragdemo-mbp` status=healthy、
  conns=4、ingress 正確）。DNS 記錄在 Cloudflare 側，本來就沒動過
- **從 `/mnt/d/backup/...` 還原 `.env`** —— 那份是 **wsl 那台的備份**
  （`msi` 的 D: 槽，2026-09-28 重灌前），裡面的 `QDRANT_API_KEY`／
  `POSTGRES_PASSWORD` 是那台自己重選的值，**mbp 不能用**。共用憑證
  （**8 把**）要靠事項 5 的 sops 管道，不要走備份
- **查 pg 密碼／`POSTGRES_PEER_PASSWORD`** —— 沒有任何程式讀它（全 repo 只剩
  註解、`.example` 說明文字、測試 docstring）。加了就是幽靈鍵
- **把 `OLLAMA_URLS` 改成 `localhost:11434`** —— ⚠️ **我 2026-09-30 上午寫錯過，
  已在此更正**。當時說「mbp 的 ollama 在本機所以用 localhost」。**錯**：
  這個變數是**容器內**的 `gateway.py` 讀的，容器裡的 `localhost` 是 api 容器
  自己，不是你的 Mac。你實測 `localhost:11434` → ConnectError、
  `host.docker.internal:11434` → 200，正解是後者。
  （wsl 用 MagicDNS FQDN 是因為那是**原生 Linux Docker**、沒有
  `host.docker.internal` 自動解析；macOS 的 Docker Desktop 有。）

---

## ⚠️ 外部症狀不足以判斷「有沒有問題」

2026-09-30 上午我從 wsl 那端看到 `api-mbp` 200，就寫下「已上線、沒有故障要修」，
並把「查容器／`.env`／ollama」列成不需要做的事。

**那次是錯的。** 你實測：`docker compose ps` 是空的（無容器）、api 映像建於
2026-09-26 落後 HEAD 4 天、`POSTGRES_DSN` 指向離線的 x570 讓每次查詢多 60s、
`/hosts` 與 `/models` 掛死 >20s。**這些全部發生在 `api-mbp` 回 200 的同時。**

為什麼外部症狀會誤導：200 只證明 **tunnel 通、某個東西在聽**。它不證明
- 那個東西是**這個 repo 的當前版本**（你的映像落後 4 天）
- 它的**設定指向正確的依賴**（DSN 指向已離線的別台）
- 它的**每個路徑都健康**（`/health` 正常但 `/hosts` 掛死）

**所以這份文件的「現況」段落只該當背景，不能當驗收。** 真正的驗收是
`bash scripts/host-doctor.sh`（它會查容器、版本、指紋），以及本文件末尾
要求回報的項目。

---

## 回報格式

**2026-10-02 起**（事項 5 age 公鑰已完成，不用再報）：

| # | 項目 | 怎麼驗 | 什麼算過 |
|---|---|---|---|
| 1 | **Access Service Token**（先做這件） | `bash scripts/env-sync.sh --fingerprints \| grep CF_ACCESS` | 兩行都印出，且 `sha12` 是 `11cb9bc42f55` / `f00a6bd62ddc` |
| 2 | 本機後端 | `docker compose ps` ＋ `bash scripts/host-doctor.sh` | 三容器 running、`api-mbp` 403 |
| 3 | 事項 6 hooks | `git config --get core.hooksPath` | 回 `.githooks` |
| 4 | 事項 7 快照同步 | `bash scripts/sync-snapshot.sh --force` ＋ `cat data/laws/.law_version` | 有 `synced_at`（不是凍結值）＋ `launchctl list \| grep sync-snapshot` 有它 |

> ⚠️ **本節上一版寫著「事項 7 排在後面，`api-x570` 還 502，修好之前先不要設
> `LAW_SYNC_SOURCE`」。那已經過期。** x570 已修復（2026-10-01 `8d59bde`），
> wsl 端 2026-10-02 實測 peer 探測 `x570 連線成功`。
> **現在就照事項 7 走，順序是 1 → 2 → 3 → 4。**
> 留著那句是為了讓你知道為什麼不要照做：在 502 的時候設
> `LAW_SYNC_SOURCE=http://100.119.83.111:6333`，症狀是**同步靜默失敗**
> —— 不報錯、只是 `.law_version` 一直不動，看起來像「上游沒更新」。

**不要回報任何憑證值。** 要證明某鍵有值，打印長度或前 3 個字元就夠
（`${#v}` 或 `echo "${v:0:3}…"`）。**age 私鑰（`age-secret-key1...`）絕對不能回報。**

若 `api-mbp.ragdemo.win` 當下不是 403，一併回報 HTTP 狀態碼與
`curl -sS -D-` 的**前 10 行**（response header，不是 body）。

⚠️ **502 與 403 的意思不同，不要一起當故障回報**：

| 你看到 | 回報時寫 |
|---|---|
| `api-mbp` **403** | **正常**，Access 擋著，不用修（這是健康的樣子）|
| `api-mbp` **502** | 本機後端沒起來（要修）—— 先查 `docker compose ps`，不要動 cloudflared／DNS |
| `api-mbp` **530** | tunnel 沒連線（`cloudflared` 沒跑）|
| 帶真 token 仍 **403** | token 貼反或 Access app 有問題 → 跑 `scripts/access-check.sh` 第 3 段 |
