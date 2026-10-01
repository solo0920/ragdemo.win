# x570 交接（2026-09-30，取代 2026-09-26 版）

給 **x570 上的 opencode** 讀。**逐項查證後回報，不要先假設原因。**

前置：`git pull`。想先確認環境用 `bash scripts/host-doctor.sh`，**先跑它再決定要不要動手**。

⚠️ 指令**只印指紋／長度，不印值**。不要改成 `cat .env`／`env | grep`，
不要對含憑證的指令加 `bash -x`（2026-09-26 三次憑證外洩都是這樣）。

> **★ 2026-10-01 更新：事項 5（age 公鑰）已完成，不再需要你回報。**
> 舊版讓你「★先做事項 5」，那是當時唯一的硬性阻塞；現在 `.sops.yaml` 三把
> 公鑰都在（wsl／x570／mbp），`secrets.common.enc.env` 內也有對應的三筆
> recipients —— `sops updatekeys` 確實跑過，所以 x570 的 pull 不會再遇到
> `no identity matched any of the recipients`。
>
> **現在的阻塞是「本機後端沒起來」（502）—— 見〈現況〉。其餘事項都是確認性質。**
> 編號沿用舊版：repo 裡有 8 處引用（`.sops.yaml`、`SCOPE.md`、
> `settings/env/README.md`、`.opencode/agents/*.md`、`scripts/env-sync.sh`）。

---

## 現況：⚠️ 本機後端沒起來（2026-10-01 wsl 端實測）

```
api-x570.ragdemo.win → HTTP 502
api-mbp.ragdemo.win  → HTTP 502
api-wsl.ragdemo.win  → HTTP 403   ← Access 擋著，正常（見下）
```

Cloudflare API：三條 tunnel **全部 healthy、conns=4**
（`ragdemo-x570`／`ragdemo-mbp`／`ragdemo-wsl`）。

**所以 tunnel 是好的，壞的是本機後端。** 照下面那張表，502 = 容器沒跑。
**不要去動 cloudflared、不要動 DNS** —— 那兩樣都是好的。

| 狀態 | 意義 | 處置 |
|---|---|---|
| **530** | tunnel 沒連線（`cloudflared` 沒跑）| 拉起 tunnel |
| **502** | **tunnel 連著，但本機後端沒起來** | **查 `docker compose ps`** ← 你現在是這個 |
| **403** | Cloudflare Access 擋住了 | **正常**，見下 |

### 為什麼 wsl 那台是 403 不是 200

2026-10-01 補上了 Cloudflare Access。`fbb9325` 那個 commit 從 2026-09-30
就假設 Access 存在（它的註解寫「後端只綁 127.0.0.1，唯一入口是 tunnel，所以
邊緣驗證就是完整防護」），但那個前提**從來不成立** —— Access 直到 2026-10-01
才真的啟用。那段期間 `api-wsl.ragdemo.win` 是全網可達的。

現在 `api-wsl` 是 **Service Auth 保護**：不帶 `CF-Access-Client-Id` /
`CF-Access-Client-Secret` 一律 403。

**若你看到 `api-wsl` 是 403，那是正常的，不是故障。** 本機測試請用
`http://localhost:8000`，不要從公網打那個 host。

### 法規版本：9/18 不能再當「最新」

舊版這裡寫「三台 `law_version` 都是 `2026/9/18`，9/18 就是最新版，不用重跑」。

**那是 2026-09-30 的判斷，一個多月過去了，現在不能照做。**
wsl 端 2026-10-01 實測仍是 `2026-09-18` —— 但那只證明**沒更新過**，
不證明上游沒有新版。請自己查上游再決定要不要重跑。

尤其別因為「三台一致」就當成最新的：**一致也可能是「一起過期」**。

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

## ★ 新的首要事項：讓本機後端起來

`api-x570` 現在是 **502**（tunnel 連著、源站死）。照〈現況〉那張表，
這是容器沒跑。先確認：

```bash
bash scripts/host-doctor.sh          # 一次看完容器、版本、設定指向
docker compose ps                    # 空 → 容器沒起來
```

若容器是 stopped，起來：

```bash
docker compose up -d
```

⚠️ **`MBP-HANDOFF.md:228-237` 記錄過一次教訓**：200 也不代表沒問題。
那次 `api-mbp` 回 200，但容器是空的、映像落後 4 天、DSN 指向離線的別台讓
查詢多 60s。**真正的驗收是 `host-doctor.sh`**，不是 HTTP 狀態碼。

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

## 事項 7：確認兩件事（回報即可，先不要動手）

### 7a. 你是「來源機」，確認排程有沒有在跑

`scripts/law-update-worker.sh` 的角色判斷**只看一個檔案**在不在：

```bash
if [ -f "$ROOT/data/laws/.law_sync.json" ]; then
  ROLE="source"; CMD="uv run ingest/laws/sync_daily.py --apply"
```

你的 `/status` 回報 `law_version 來源 = law_sync.json` → **系統判定你是 `source`**。
（與 2026-09-26 版的前提不同：當時「x570 是唯一來源機」，現在 msi 也跑完
`sync_daily` 了，**兩台都是 source**，彼此不依賴。這是好事。）

```bash
crontab -l | grep law-update-worker || echo "✗ 沒有 law-update-worker 排程"
cat data/.ops/law-update.status 2>/dev/null || echo "✗ 沒狀態檔 → worker 從未執行過"
ls -la --time-style=long-iso data/laws/.law_sync.json
```

路徑依據 `law-update-worker.sh:18`（`OPS="$ROOT/data/.ops"`）。

**判讀**：有排程 ＋ `last_checked` 近期 → 正常；有排程但 `last_checked` 很久以前
→ 資料是碰巧還對著，請回報日期由 msi 端判斷。**沒有排程也先不要自己加** ——
回報後由 msi 端決定（前端「更新」按鈕是走這條路，加錯了會跟別的排程打架）。

### 7b. 先不要跑 `env-sync.sh render`

`settings/env/hosts.shared.env` 的 `x570_TS_IP`／`x570_HOST_MACHINE_ID`／
`x570_OLLAMA_URLS`／`x570_OLLAMA_MODELS` **刻意留空**（IP 不進被追蹤的檔案）。
對運作**無影響** —— `/health` 的 `machine_id`／`ips`／`llm_src` 是你自己心跳寫進
自己 pg 的（實測 `machine_id=af8eaa67…`、`ips=["100.119.83.111"]` 都正常）。

但若在 x570 跑 `render`，它會用這些空值生成 `.env` → `OLLAMA_URLS=` 空值 →
`gateway.py` 退回原始碼預設 → **查詢靜默降級，不報錯**。

**若你確實需要改模型設定**，先回報（不要用 `cat .env`，會連帶印出兩個機密）：

```bash
grep -E '^(OLLAMA_URLS|OLLAMA_MODELS|LLM_MODEL)=' .env | cut -c1-60
curl -s -m 5 http://localhost:11434/api/tags -o /dev/null -w "ollama HTTP %{http_code}\n"
ollama list
```

你的 LLM 是 `qwen3:14b`（總表 `x570_LLM_MODEL=qwen3:14b` 已填好），
**不要**改成 msi 的 `qwen2.5-coder:latest`。

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

**2026-10-01 起改三項**（事項 5 已完成，不用再報公鑰）：

1. **本機後端**：`docker compose ps` 的輸出 ＋ `bash scripts/host-doctor.sh`
   的摘要（不用貼全文）—— 這是現在的主要待辦
2. **事項 7a**：`crontab -l | grep law-update-worker` 有沒有；`.law_sync.json` 的
   `last_checked` 與 mtime
3. **事項 6**：`git config --get core.hooksPath` 的結果

**不要回報任何憑證值。** 要證明某鍵有值，打印長度或前 3 個字元就夠
（`${#v}` 或 `echo "${v:0:3}…"`）。**age 私鑰（`age-secret-key1...`）絕對不能回報。**

若 `api-x570.ragdemo.win` 當下不是 200，一併回報 HTTP 狀態碼與
`curl -sS -D-` 的**前 10 行**（response header，不是 body）。

⚠️ **502 與 403 的意思不同，不要一起當故障回報**：

| 你看到 | 回報時寫 |
|---|---|
| `api-x570` **502** | 本機後端沒起來（要修）|
| `api-wsl` **403** | 正常，Access 擋著，不用修 |
