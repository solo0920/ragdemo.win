# x570 交接（2026-09-30，取代 2026-09-26 版）

給 **x570 上的 opencode** 讀。**逐項查證後回報，不要先假設原因。**

前置：`git pull`。想先確認環境用 `bash scripts/host-doctor.sh`，**先跑它再決定要不要動手**。

⚠️ 指令**只印指紋／長度，不印值**。不要改成 `cat .env`／`env | grep`，
不要對含憑證的指令加 `bash -x`（2026-09-26 三次憑證外洩都是這樣）。

> **★ 請先做事項 5** —— 唯一的硬性阻塞，其餘都是確認性質。
> 編號沿用舊版：repo 裡有 8 處引用（`.sops.yaml`、`SCOPE.md`、
> `settings/env/README.md`、`.opencode/agents/*.md`、`scripts/env-sync.sh`）。

---

## 現況：已上線，**不要去修 tunnel**

2026-09-30 01:39–01:45 msi 端實測，連抽 8 輪穩定：

```
api-x570.ragdemo.win → HTTP 200  host_id=x570  llm=qwen3:14b
api-mbp.ragdemo.win  → HTTP 200
api-wsl.ragdemo.win  → HTTP 200
```

Cloudflare API：`ragdemo-x570  status=healthy  conns=4`，
ingress `api-x570.ragdemo.win → http://localhost:8000` 正確。
x570 自己的 `/status` 也回報三台全通。

**但它 30 分鐘內才從 530 變 200。** 若你讀到這份時 `api-x570` **不是 200**，
那說明它會掉線 —— 那才是要查的情況，照 `TUNNEL-530-2026-09-24.md` 的
x570 checklist 走。分辨方式：

| 狀態 | 意義 |
|---|---|
| **530** | tunnel 沒連線（`cloudflared` 沒跑） |
| **502** | tunnel 連著，但本機後端沒起來（查容器） |

**法規資料是最新的，不用重跑。** 三台 `law_version` 都是 `2026/9/18`，
而 `.law_sync.json` 的 `last_checked: 2026-09-29T23:39` 顯示 wsl 昨天才查過上游 ——
9/18 就是最新版。一致**不是**「一起過期」。

---

## 事項 5：報回 age 公鑰（唯一會擋住事的）★先做★

`.sops.yaml` 的 recipients **只有 wsl 一把**（x570 與 mbp 都還是 TODO）。
`env-sync.sh pull` 要 `sops -d` 那 6 把共用憑證，sops 只能用名單裡的公鑰解 ——
**x570 的年齡金鑰不在名單，pull 就會失敗。**

**per-host 的 2 把機密（`QDRANT_API_KEY`／`POSTGRES_PASSWORD`）不在加密檔裡**，
用你原本的值就對 —— 不要因為 pull 失敗去動它們。

在 x570 上執行：

```bash
# 已有就跳過，絕對不要重建（重建會讓已加密的檔案解不開）
ls ~/.config/sops/age/keys.txt 2>/dev/null && grep -c . ~/.config/sops/age/keys.txt
command -v age-keygen || echo "需先裝 age：https://github.com/FiloSottile/age"
command -v sops     || echo "需先裝 sops：https://github.com/getsops/sops"

# 只印 age1... 那一行（公鑰，可回報）
grep -o 'age1[0-9a-z]*' ~/.config/sops/age/keys.txt 2>/dev/null | head -1
```

**回報 `age1...` 那一行就好。絕對不要回報 `keys.txt` 本身或
`age-secret-key1...` 那一行。**

拿到後 wsl 端會加進 `.sops.yaml` → `sops updatekeys` 重加密 → commit。
**x570 端不需要再做其他事**，下次 `git pull` + `env-sync.sh pull` 就通。

若 `keys.txt` 已存在但 pull 仍失敗：回報錯誤訊息**第一行**。那要從 msi 端解決，
不要在 x570 端反覆重試。

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

四項，每項都要有實測輸出：

1. **事項 5**：`grep -o 'age1[0-9a-z]*' ~/.config/sops/age/keys.txt | head -1` 的結果
2. **事項 7a**：`crontab -l | grep law-update-worker` 有沒有；`.law_sync.json` 的
   `last_checked` 與 mtime
3. **事項 6**：`git config --get core.hooksPath` 的結果
4. **自檢**：`bash scripts/host-doctor.sh` 的摘要（不用貼全文）

**不要回報任何憑證值。** 要證明某鍵有值，打印長度或前 3 個字元就夠
（`${#v}` 或 `echo "${v:0:3}…"`）。**age 私鑰（`age-secret-key1...`）絕對不能回報。**

若 `api-x570.ragdemo.win` 當下不是 200，一併回報 HTTP 狀態碼與
`curl -sS -D-` 的**前 10 行**（response header，不是 body）。
