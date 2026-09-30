# mbp 交接（2026-09-30）

給 **mbp 上的 opencode** 讀。**逐項查證後回報，不要先假設原因。**

前置：`git pull`（x570 那份建議用 `host-sync.sh`，那是為 x570 寫的；
mbp 只要 `git pull`）。想先確認環境用 `bash scripts/host-doctor.sh`。

⚠️ 指令**只印指紋／長度，不印值**。不要改成 `cat .env`／`env | grep`，
不要對含憑證的指令加 `bash -x`（2026-09-26 三次憑證外洩都是這樣）。

> 事項編號與 `X570-HANDOFF.md` 對齊（5＝age 公鑰、6＝git hooks），
> 讓 `SCOPE.md` 的「收 age 公鑰｜x570、mbp」一列指向同一件事。

---

## 現況：已上線

2026-09-30 01:39–01:45 msi 端實測，連抽 8 輪穩定：

```
api-mbp.ragdemo.win  → HTTP 200
api-x570.ragdemo.win → HTTP 200
api-msi.ragdemo.win  → HTTP 200
```

⚠️ **200 不等於沒問題。** 這段只是背景，不能當驗收 —— 你 2026-09-30 回報過
`api-mbp` 明明 200，但容器是空的、映像落後 4 天、DSN 指向離線的 x570 讓查詢
多 60s。理由見下方〈外部症狀不足以判斷〉。

`ragdemo.win` 前端 `x-ragdemo-origin: https://api-msi.ragdemo.win`，
worker 依序嘗試（**msi 第一**），任一台掛掉會自動輪到下一台。

2026-09-29 下午 `api-mbp` 曾是 **502**（tunnel 連著但本機後端沒起來），
現在已恢復。**分辨方式**（日後再遇到時用）：

| 狀態 | 意義 | 處置 |
|---|---|---|
| **530** | tunnel 沒連線（`cloudflared` 沒跑） | 拉起 tunnel |
| **502** | tunnel 連著，但本機後端沒起來 | 查 `docker compose ps` |

---

## 事項 5：報回 age 公鑰（唯一會擋住事的）★先做★

`.sops.yaml` 的 recipients **只有 msi 一把**（x570 與 mbp 都還是 TODO）。
`env-sync.sh pull` 要 `sops -d` 那 6 把共用憑證，sops 只能用名單裡的公鑰解 ——
**mbp 的年齡金鑰不在名單，pull 就會失敗**（`ADMIN_TOKEN`／
`QDRANT_PEER_API_KEY`／`CF_AIG_TOKEN`／`HF_TOKEN`／`NVIDIA_API_KEY`／
`TYPESAFE_API_KEY`）。

**per-host 的 2 把機密（`QDRANT_API_KEY`／`POSTGRES_PASSWORD`）不在加密檔裡**，
用你原本的值就對 —— 不要因為 pull 失敗去動它們。

在 mbp 上執行（完整背景見 `X570-HANDOFF.md` 事項 5）：

```bash
# 已有就跳過，絕對不要重建（重建會讓已加密的檔案解不開）
ls ~/.config/sops/age/keys.txt 2>/dev/null && grep -c . ~/.config/sops/age/keys.txt
command -v age-keygen || echo "需先裝 age：brew install age"
command -v sops     || echo "需先裝 sops：brew install sops"

# 只印 age1... 那一行（公鑰，可回報）
grep -o 'age1[0-9a-z]*' ~/.config/sops/age/keys.txt 2>/dev/null | head -1
```

**回報 `age1...` 那一行就好。絕對不要回報 `keys.txt` 本身或
`age-secret-key1...` 那一行。**

拿到後 msi 端會加進 `.sops.yaml` → `sops updatekeys` 重加密 → commit。
**mbp 端不需要再做其他事**，下次 `git pull` + `env-sync.sh pull` 就通。

若 `keys.txt` 已存在但 pull 仍失敗：回報錯誤訊息**第一行**。那要從 msi 端解決，
不要在 mbp 端反覆重試。

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
cat data/laws/.law_version          # 應出現，且 update_date = 2026/9/18
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

三項，每項都要有實測輸出：

1. **事項 5**：`grep -o 'age1[0-9a-z]*' ~/.config/sops/age/keys.txt | head -1` 的結果
2. **事項 6**：`git config --get core.hooksPath` 的結果
3. **自檢**：`bash scripts/host-doctor.sh` 的摘要（不用貼全文）

**不要回報任何憑證值。** 要證明某鍵有值，打印長度或前 3 個字元就夠
（`${#v}` 或 `echo "${v:0:3}…"`）。**age 私鑰（`age-secret-key1...`）絕對不能回報。**

若 `api-mbp.ragdemo.win` 當下不是 200，一併回報 HTTP 狀態碼與
`curl -sS -D-` 的**前 10 行**（response header，不是 body）。
