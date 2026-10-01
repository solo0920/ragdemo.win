# mbp 交接（2026-09-30）

給 **mbp 上的 opencode** 讀。**逐項查證後回報，不要先假設原因。**

前置：`git pull`（x570 那份建議用 `host-sync.sh`，那是為 x570 寫的；
mbp 只要 `git pull`）。想先確認環境用 `bash scripts/host-doctor.sh`。

⚠️ 指令**只印指紋／長度，不印值**。不要改成 `cat .env`／`env | grep`，
不要對含憑證的指令加 `bash -x`（2026-09-26 三次憑證外洩都是這樣）。

> 事項編號與 `X570-HANDOFF.md` 對齊（5＝age 公鑰、6＝git hooks），
> 讓 `SCOPE.md` 的「收 age 公鑰｜x570、mbp」一列指向同一件事。
>
> **★ 2026-10-01 更新：事項 5（age 公鑰）已完成，不需要你回報。**
> `.sops.yaml` 三把公鑰都在（wsl／x570／mbp），`secrets.common.enc.env` 內
> 也有三筆 recipients —— `sops updatekeys` 確實跑過。
> **現在的阻塞是「本機後端沒起來」（502），見〈現況〉。**

---

## 現況：✅ 本機後端已恢復（2026-10-01 22:00 wsl 端實測）

```
api-mbp.ragdemo.win  → HTTP 200   ← 原本 502，現已恢復
api-x570.ragdemo.win → HTTP 200
api-wsl.ragdemo.win  → HTTP 403   ← Access 擋著，正常（見下）
```

三台 peer 探測在 wsl 端都回「連線成功」。

✅ **三台都有 Access 保護**（2026-10-01 22:35 複驗，全 403）。上面那組
`api-mbp 200` 是 22:00 的量測，你的 Access app 當時還沒建好。

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

### 為什麼 wsl 那台是 403 不是 200

2026-10-01 補上了 Cloudflare Access。`fbb9325` 那個 commit 從 2026-09-30
就假設 Access 存在，但**那個前提從來不成立** —— Access 直到 2026-10-01 才啟用。
那段期間 `api-wsl.ragdemo.win` 是全網可達的。

現在 `api-wsl` 是 **Service Auth 保護**（不帶 `CF-Access-Client-Id` /
`-Secret` 一律 403）。**`api-mbp` 與 `api-x570` 還沒建 Access app**，
見〈★新的待辦：建 Access app〉。

`ragdemo.win` 前端目前只送 `wsl=https://api-wsl.ragdemo.win` 一台
（`API_ORIGINS`），所以 403/502 的差異現在不影響查詢。
**但那兩台修好、你要加回 `API_ORIGINS` 之前，必須先建好它們的 Access app。**

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

實測過：msi 重灌後這項不見了，連續 11 個 commit 在無任何檢查下產生。

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
| **msi** | 空 | `127.0.0.1:6333` | **不能**（只在本機） |
| **x570** | `100.119.83.111` | `100.119.83.111:6333` | 能（你已實測過） |

msi 的 `TS_IP` 是空的（2026-09-29 重灌後清空，IP 不進被追蹤的檔案），
所以 **msi 的 qdrant 從別台連不到**。你實測過 `100.119.83.111:6333` 通 —— 那
就是 x570。

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
- **從 msi 的備份還原 `.env`** —— `/mnt/d/backup/.../root.env` 是 **msi 那台的**，
  裡面的 `QDRANT_API_KEY`／`POSTGRES_PASSWORD` 是 msi 自己重選的值，
  **mbp 不能用**。共用憑證（6 把）要靠事項 5 的 sops 管道，不要走備份
- **查 pg 密碼／`POSTGRES_PEER_PASSWORD`** —— 沒有任何程式讀它（全 repo 只剩
  註解、`.example` 說明文字、測試 docstring）。加了就是幽靈鍵
- **把 `OLLAMA_URLS` 改成 `localhost:11434`** —— ⚠️ **我 2026-09-30 上午寫錯過，
  已在此更正**。當時說「mbp 的 ollama 在本機所以用 localhost」。**錯**：
  這個變數是**容器內**的 `gateway.py` 讀的，容器裡的 `localhost` 是 api 容器
  自己，不是你的 Mac。你實測 `localhost:11434` → ConnectError、
  `host.docker.internal:11434` → 200，正解是後者。
  （msi 用 WSL 閘道 IP 是因為那是**原生 Linux Docker**、沒有
  `host.docker.internal` 自動解析；macOS 的 Docker Desktop 有。）

---

## ⚠️ 外部症狀不足以判斷「有沒有問題」

2026-09-30 上午我從 msi 端看到 `api-mbp` 200，就寫下「已上線、沒有故障要修」，
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

**2026-10-01 起改兩項**（事項 5 已完成，不用再報公鑰）：

1. **本機後端**：`docker compose ps` 的輸出 ＋ `bash scripts/host-doctor.sh`
   的摘要 —— 這是現在的主要待辦
2. **事項 6**：`git config --get core.hooksPath` 的結果

事項 7（快照同步）仍有效，但**排在後面**：它要 `LAW_SYNC_SOURCE` 指向一個
可連的 qdrant，而 `api-x570` 還 502。**x570 修好之前先不要設那個變數** ——
現在設等於指向一個連不上的位址，而症狀會是「同步靜默失敗」。

**不要回報任何憑證值。** 要證明某鍵有值，打印長度或前 3 個字元就夠
（`${#v}` 或 `echo "${v:0:3}…"`）。**age 私鑰（`age-secret-key1...`）絕對不能回報。**

若 `api-mbp.ragdemo.win` 當下不是 200，一併回報 HTTP 狀態碼與
`curl -sS -D-` 的**前 10 行**（response header，不是 body）。

⚠️ **502 與 403 的意思不同，不要一起當故障回報**：

| 你看到 | 回報時寫 |
|---|---|
| `api-mbp` **502** | 本機後端沒起來（要修）|
| `api-wsl` **403** | 正常，Access 擋著，不用修 |
