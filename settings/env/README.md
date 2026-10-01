# settings/env — 三機共用憑證與 per-host 值的分層與分發（M1 env 模組架構書）

> **擁有者：`env-ops` agent。** 本檔骨架由 `ARCHITECTURE.md`〈架構表〉建立，
> 內容由 owner 逐題補齊 —— 補不完的題目代表該處有沒被記錄下來的知識，
> 應該在下次動到時補上，而不是先留空。

## 1. 目標

三台環境變數的**單一真相**：哪個鍵共用、哪個 per-host、哪個是 per-host 機密，
全部由**機器驗證**而不是靠人記。
做到什麼算完成：`scripts/env-sync.sh --check` 三台都過 ＋
`python3 scripts/env-audit.py` 報出的落差**每一項都有已知解釋**（不是「沒漏掉」，
是「漏掉的理由都寫在案」）。

## 2. 邊界（不負責）

- **不動 `compose.yaml`** —— 哪個變數「必須傳進容器」是 M2 `backend` 的事
  （`backend/DESIGN.md` §4 有「刻意不傳入容器的 3 個變數」清單）。
  本模組只負責變數**怎麼被定義、分類、分發、驗證**。
- **不動 backend／frontend／ingest 程式碼**（`ARCHITECTURE.md`〈架構表〉M1 邊界欄）
- 部署與輪換的**執行**歸 M6 `ops`（`scripts/DESIGN.md`）；本模組只定義規則
- 不碰 `frontend/.env`（那是 CF Pages／OAuth 的獨立三鍵，`env-audit` 明確不納入）

## 3. 不變量

壞了會出事、但**測試不一定抓得到**的規則：

- **一個鍵只能被一層認領。** 追蹤檔不得出現任何憑證值；per-host 機密永不進 sops、
  永不進總表。驗證清單見 §5。
- **per-host 機密永不進 sops**：`QDRANT_API_KEY`、`POSTGRES_PASSWORD` 只留本機
  `.env`（600、gitignored）。進了 sops 就變成三台鎖步輪換 —— 正是 §7 要消除的成本。
- **`HOST_ID` 絕不能讀錯**：它是 `render` 的選擇器，要先知道本機是誰才挑得到列。
  讀錯 → 拿到別台的 per-host 值，而且**不會報錯**。
- **Tailscale IP 必須正確**：追蹤檔不得出現 `^LAN_IP=`（`ARCHITECTURE.md`〈IP 準則〉），
  寫 LAN_IP 會被 `registry.py` 過濾掉而**無人發現**。
- **不猜值。** 不知道某台的值就在總表留空 —— 空值＝該機沿用自己 `.env` 現值。
  猜值寫進**被追蹤**的檔等於把謊言版本化，會被 `env-sync pull` 自動分發到另外兩台。
- **只有根 `.env` 是執行期真相**，且不帶前綴。不得再出現第二份副本
  （`backend/.env` 已於 2026-09-26 刪除，當時的漂移造成過兩次不對稱故障）。

## 4. 陷阱（實測踩過）

- **不要 `cat .env`、不要 `env | grep KEY`、不要對 `env-sync.sh` 跑 `bash -x`**
  （腳本偵測到 xtrace 直接拒絕執行；2026-09-26 的三次外洩都是這類）。
  ⚠️ **要檢視 `.env` 結構就用 `scripts/env-prune.py --dry-run`** —— 它只印鍵名與
  處置方式，不印值。想看憑證狀態用 `--fingerprints`（只給長度 + sha12）。
  2026-10-01 實測教訓：用 `grep -E '^(A|B|C)='` 這種「挑幾個鍵印出來」的做法會把
  密碼印進終端機紀錄——`POSTGRES_DSN` 的值裡就內嵌了密碼。
- ⚠️ **`.env` 裡的 `KEY=`（空值賦值）不等於「沒設」**（2026-10-01）：
  `os.getenv(K, default)` 對 `KEY=` 回傳 `""` 而不是 default。所以那些空值是**主動
  把鍵設成空字串**，踩過最嚴重的一例是 `CF_AIG_TOKEN_FILE=`（`gateway.py` 沒有
  `or` 保護 → `Path("")` → 例外 → 回 `""`，token 檔 fallback 整個被弄壞）。
  **要停用一個鍵是刪掉整行，不是寫空值。** `scripts/env-prune.py` 就是為此存在。
- **不要 `docker compose config` 後貼輸出** —— 它會展開所有憑證。
  只驗語法請用 `config -q`。
- **`POSTGRES_PASSWORD`（本機 pg 密碼）≠ `POSTGRES_DSN` 裡的密碼**（若 DSN 指向
  別台，那是別台的）。2026-09-27 MSI 實測兩者指紋不同（`e328bd31728a` vs
  `55cebf3c8276`），用 `${POSTGRES_PASSWORD}` 展開 DSN 會製造「設定都有、心跳就是
  password authentication failed」。
  **2026-09-30 更新**：各台 `/hosts` 已改讀**自己的** pg（實測 `api-wsl /hosts` 只
  回 wsl、`api-x570 /hosts` 只回 x570），沒有跨機讀 pg 的需求，所以
  `POSTGRES_PEER_PASSWORD` **不納管** —— 它沒有任何程式讀取（全 repo 只剩註解、
  `.example` 說明文字、測試 docstring）。
  **不要加這個沒人讀的幽靈鍵，也不要為了填它去查別台的 pg 密碼。**
  總表的 `<機台>_POSTGRES_DSN` **該機自己填自己的**（值用
  `${POSTGRES_PASSWORD}` 展開本機密碼），別台留空表示「沿用該機 `.env` 現值」
  —— 留空是合法的默認狀態，不是待辦，也不是「三列都要空」。
- **前綴不能放進 `.env`**：`docker compose` 只認 `${VAR}` 插值，沒有「依 `HOST_ID`
  動態選 `wsl_`／`x570_`」的能力。放進去會讓 WSL 的 8b 設定被**靜默吃掉**
  （回退原始碼預設 `qwen3:14b`、零錯誤訊息）；`TS_IP` 更嚴重，`ports` 會去 bind
  預設值那台的 IP，docker 直接啟動失敗。細節見 §9。
- **`OLLAMA_MODELS` 與 `OLLAMA_URLS` 長度必須一致**（`rag.py:215` 用 index 取值）。
  長度不符不會報錯，只會「某台 ollama 拿到別台的模型」。
- **`${VAR}` 引用為空要硬失敗**：寫出空密碼的 DSN 比不寫更糟。
- **共享憑證是啟動參數**：換值不重啟容器等於沒換。
- **不要重跑 `--init-secrets`** —— 它是「第一台建立加密檔」用的，會覆蓋整份。
- ⚠️ **共用憑證清單裡可能混著「沒有任何一台設過」的空殼**（2026-09-30 實測：
  `ZEN_API_KEY` 在共用層待了兩年，enc 檔裡是空值、沒有機台設過、`zen_ready` 永遠
  false）。**判斷一把共用憑證該不該留著，看 `secrets.common.enc.env` 裡那行有沒有
  `ENC[...]` 就知道** —— 空值＝沒人設過，那不是保險，是幽靈鍵。分發它還有個隱形
  成本：`--check` 的鍵覆蓋率會要求**每台** `.env` 都有那一行，於是沒用過的機器
  天天報「缺鍵」（`ZEN_API_KEY` 當時就是這樣讓 mbp 的 `--check` 恆紅）。
  **程式還在 `os.getenv()` 讀它，不是留在共用層的理由。**
- ⚠️ **「變數被分發」≠「變數有消費者」**：`RERANK_MODEL` 被 `env-sync.sh` 當成
  `SHARED_CONFIG` 分發、被 `env-audit.py` 當成「程式有讀」而豁免，但它在
  `backend/app/` 裡**只出現在自己的定義行**（`rag.py:42`），沒有任何地方使用它。
  見 `evals/README.md` §7 末。這是 M1 的分類與 M2 的接線之間的落差。
- **per-host 機密不要加回 `SHARED_SECRETS` 或任何 `py_apply` layer** ——
  那就是把它們變回三台鎖步輪換。
- **明文檔絕不進版控**：`secrets.common.env`（明文）、`secrets.host.env`（明文）、
  `.decrypted.*` 暫存檔（`--check` 與 CI 會擋）。
- **回報只給「鍵名＋長度＋sha256 前 12 碼」**，格式見 `--fingerprints`。
- ⚠️ **不要用 `grep -rn` 帶 `--include='*.env'` 掃全 repo 找消費點**：
  它會匹配到 repo 根那份**未追蹤的 `.env`**，把值印進終端機紀錄。
  2026-10-01 x570 實踩（`QDRANT_PEER_API_KEY` 的值被印出來一次）。
  **用 `git grep`** —— 只掃被追蹤的檔，`.env` 天然不在其中。
  要看未追蹤檔的鍵名請用 `scripts/env-prune.py --dry-run`。
- ⚠️ **`host-doctor.sh` 的 `rotate-hint` 會建議「輪換 peer 那把」讓它與
  `QDRANT_API_KEY` 不同值。照做會讓兩台備援機永久 401。**
  那個 warn 只比對指紋、不知道 peer key 的語意。輪換之前先讀
  §7〈peer 那把的語意〉。

## 5. 驗收

```bash
scripts/env-sync.sh --check          # 鍵覆蓋率＋總表 schema＋漂移＋版控衛生
python3 scripts/env-audit.py         # 從程式碼反查落差（不要用 --quiet，會跳過根 .env 的稽核）
scripts/env-sync.sh render --dry-run # 只印「會動哪幾個鍵」，不寫檔、不印值
bash -n scripts/env-sync.sh          # 語法
pytest -q tests/test_env_audit.py tests/test_env_sync.py
```

`--check` 驗的東西：鍵覆蓋率（`.env` 必須有 8 把共用 ＋ 2 把 per-host 機密 ＋
`common.env` 的鍵）、`secrets.common.env.example` 的鍵 == 腳本 `SHARED_SECRETS`、
`secrets.host.env.example` 的鍵 == 腳本 `PER_HOST_SECRETS`、
**per-host 機密的鍵名不得出現在 `secrets.common.enc.env`**（sops 的 dotenv 輸出
讓鍵名保持明文、只有值是 `ENC[...]`，所以這條**不需要解密就能驗**，
CI 沒有 age 私鑰也跑得到）、`secrets.host.env`（明文）不得被追蹤。

本機（`msi`）2026-09-27 實測：`--check` exit 0（`.env` 26 keys、8 個 per-host 鍵與總表一致）。

---

# 分層與分發（詳細設計）

以下 §6–§10 是本模組的機制細節。契約 5 題在 §1–§5，權威版只有一份。

## 為什麼有這層

兩個問題，兩種病：

1. **共用憑證會漂移**：`QDRANT_PEER_API_KEY` 等 8 把有跨機讀寫關係，必須三台一致，
   過去靠人工複製，2026-09-26 已造成兩次不對稱故障（本機 200、遠端 401；registry 心跳失敗）。
2. **per-host 值會寫錯地方**：`HOST_ID`、`TS_IP`、`LLM_MODEL`（wsl 是 8b）、
   `OLLAMA_URLS`、`POSTGRES_DSN` 每台不同，整份 `.env` 同步會直接寫壞機器。

## 6. 分層

| 檔案 | 追蹤 | 內容 |
|---|---|---|
| `common.env` | 是（明文） | 共用**非敏感**鍵。值空＝用 compose 預設 |
| `hosts.shared.env` | 是（明文） | **per-host 值的唯一真相**：`<機台>_<鍵>=<值>`，三台同檔 |
| `secrets.common.env.example` | 是（明文） | **8 把**共用憑證的鍵名，值一律空 |
| `secrets.common.enc.env` | 是（**加密**） | 8 把共用憑證真值，sops+age 加密 |
| `secrets.host.env.example` | 是（明文） | **2 把** per-host 機密的鍵名，值一律空 |
| `secrets.host.env`（若有人手建） | **否**（.gitignore） | 從不建立也不建議存在；真值只留各機 `.env` |
| `.sops.yaml`（repo 根） | 是 | age recipient 公鑰清單 |
| `.env`（repo 根） | 否（600） | 執行期唯一真相，**不帶前綴** |

合併順序（低 → 高）：`common.env` → 解密後的 secrets → render 出的 per-host 值。
本機 `.env` 裡不在任何分層的鍵（例如 `HOST_ID`）永遠保留。

## 7. 共用憑證 vs per-host 機密（2026-09-27 改正：9 把 → 7＋2；2026-09-30：7 → 6；2026-10-02：6 → 8）

**判斷標準只有一個：這個值有沒有跨機的讀寫關係？** 沒有就是 per-host。
分類錯了不會立刻壞掉，症狀是「時間全花在 debug key 上」—— 所以標準要寫死成
可查證的規則，而不是「我覺得它該共用」。

### 共用憑證（8 把，必須三台同值 → sops 分發）

| 鍵 | 跨機關係 |
|---|---|
| `QDRANT_PEER_API_KEY` | 唯一跨機的 qdrant 認證：`scripts/sync-snapshot.sh` 拉 **x570** 的快照 |
| `ADMIN_TOKEN` / `CF_AIG_TOKEN` / `HF_TOKEN` / `NVIDIA_API_KEY` / `TYPESAFE_API_KEY` | 三台拿**同一個值**去跟**同一個外部服務**認證 |
| `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` | 同上，外部服務是 **Cloudflare Access**。三台各帶同一組 service token 去認證彼此的 peer 探測與快照拉取 |

**2026-10-02 納入：`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`（6 把 → 8 把）**。
這是 Access 護三台（2026-10-01）之後**漏掉的分類**，不是新設計：判斷標準就是
上面那一句，三台同值、對同一個外部服務認證，與 `ADMIN_TOKEN` 完全同類。

⚠️ 它**缺席 sops 層一天**的實際後果（記在這裡因為症狀是隱性的）：

1. `mbp`／`x570` 只能人手貼 39 字元 hex ＋ 54 字元 `cfast_`，而**貼反**的症狀
   與「Access 沒開」完全一樣（都回 403）
2. 輪換要動三個 `.env`，中間必然有一段 peer 探測全紅
3. `--fingerprints` 不涵蓋它 → 兩台值不一致是**無聲**的

**新增一把共用憑證的正確做法**（不要用 `--init-secrets`，它覆蓋整份）：

```bash
# 1. 三處宣告同步（缺任何一處症狀都不同，所以要一次改齊）
#    settings/env/secrets.common.env.example 加一行 <KEY>=
#    scripts/env-sync.sh 的 SHARED_SECRETS 加 <KEY>
#    tests/test_env_sync.py 的 SHARED_SECRETS 加 <KEY>
# 2. 把值加進加密檔（走與輪換同一條「重加密→驗三件事→原子取代」的路徑）
bash scripts/rotate-secret.sh <KEY> --add --from-stdin < 新值
# 3. 三台 pull ＋ 重建容器，然後 --fingerprints 確認三台一致
```

⚠️ **Pages worker 那組值不在這條分發鏈** —— `frontend/src/routes/api/[...path]/+server.ts`
讀的是 **Cloudflare Pages 後台**的環境變數。輪換 Access token 時要記得**兩邊都換**，
否則後端 peer 正常但前端全 403。

**2026-09-30 移出：`ZEN_API_KEY`（7 把 → 6 把）**。判定依據是可查證的，不是推測：

* `secrets.common.enc.env` 裡它**是空值**（其餘 6 把都有 `ENC[...]`）。該檔由
  `--init-secrets` 從第一台機器的 `.env` 抽值建檔，**空值代表沒有任何一台設過它**
* msi/mbp 實測 `GET /models` 回 `zen_ready: false`；msi 的 `usage` snapshot 沒有
  任何 zen 記錄
* 分發一個沒人設的空值，唯一效果是讓每台 `.env` 被 `pull` 塞一行永遠不會變的
  `ZEN_API_KEY=` —— 而 `--check` 的鍵覆蓋率還會把它當成「該機缺鍵」報錯

⚠️ **移出共用層 ≠ 程式不能讀它。** `rag.py` 照樣 `os.getenv("ZEN_API_KEY")`，
拿不到值就讓 `_zen_complete` 回 false，前端已優雅降級（列項但 disable），不會壞。
要復活就重新申請 key 並同時加回 `SHARED_SECRETS` 與 `secrets.common.env.example`
（`tests/test_env_sync.py` 會鎖兩者同步）。**不要因為「程式還在讀」就把一把
沒人設的憑證留在共用層** —— 那正是幽靈鍵的來源。

### per-host 機密（2 把，各機不同 → **不分發**）

`QDRANT_API_KEY`、`POSTGRES_PASSWORD` —— 鍵名宣告在 `secrets.host.env.example`（值空），
真值只留在該機 `.env`（600、gitignored）。**永不進 sops、永不進被追蹤的檔。**

逐點 grep 查證（2026-09-27），每一個消費點都只指向自己那台：

| 鍵 | 消費點 | 指向 |
|---|---|---|
| `QDRANT_API_KEY` | `compose.yaml:15` `QDRANT__SERVICE__API_KEY` | 自己那台的 qdrant 容器 |
| | `compose.yaml:71` ＋ `rag.py:331`（api 打的是 `compose.yaml:70` 寫死的 `QDRANT_URL: http://qdrant:6333`） | 自己那台的 qdrant |
| `QDRANT_PEER_API_KEY` | `compose.yaml:30` `QDRANT__SERVICE__ALT_API_KEY` | **每台都要有** —— `sync-snapshot.sh` 用它打來源機（`:235`/`:256`）**與本機**（`:263`/`:265`），見 §7 |
| `POSTGRES_PASSWORD` | `compose.yaml:51` | 自己那台的 pg 容器 |
| | `compose.yaml:74`（DSN 預設值裡的 `@postgres:5432`） | 自己那台的 pg |

⚠️ 唯一的跨機 fallback：`sync-snapshot.sh:132` 的
`PEER_KEY="${QDRANT_PEER_API_KEY:-$QDRANT_API_KEY}"`。那是**舊單機設定的相容路徑**
（第 46 行註解如此寫），三台都有 `QDRANT_PEER_API_KEY` 時不會觸發。

### peer 那把的語意：為什麼它天生等於來源機的 `QDRANT_API_KEY`

**2026-10-01 查證，這是個差點就永久故障的耦合，寫在這裡免得下次有人照
`host-doctor.sh` 的 `rotate-hint` 去「輪換 peer 那把」。**

`QDRANT_PEER_API_KEY` 的定義就是「**能認證到來源機（x570）qdrant 的那把 key**」。
而 x570 的 qdrant 只認一個值 —— `compose.yaml:15` 的
`QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY:-}`。所以 peer 那把與 x570 自己的
`QDRANT_API_KEY` **同值是結構性必然**，不是忘了輪換。

qdrant v1.19.1 沒有「多把 key 的清單」，但有**第二個讀寫槽**
（上游 `src/settings.rs`：`api_key` / `alt_api_key`（註解明寫 "can be used for
rolling key rotation"）／ `read_only_api_key`；`src/common/auth/mod.rs` 的
`can_write = read_write || alt_read_write`）。**正解是走 `alt_api_key`**：

```yaml
QDRANT__SERVICE__API_KEY:     ${QDRANT_API_KEY:-}         # 自己那台
QDRANT__SERVICE__ALT_API_KEY: ${QDRANT_PEER_API_KEY:-}    # 跨機（sync-snapshot）
```

⚠️ **`read_only_api_key` 不能用**：peer 對來源機要 `POST` 建快照（`:235`）與
`DELETE` 清舊快照（`:256`），唯讀會擋掉。

⚠️ **只輪換 peer 那把（沒有 alt_api_key）會怎樣**：mbp／wsl `pull` 到新值後拿去
打 x570 → 401，**而且再 pull 幾次都不會好**（值一致了，只是 x570 不認）。
`auth_precheck`（`:59-71`）會在建立快照與刪本機 collection **之前** exit 1，
所以**不會損資料**，症狀是「備援資料悄悄停更」（只有 log，cron/launchd 只看得到
非零 exit）。

⚠️ **另一個後果：那個外洩值會繼續有效**。它同時是 x570 自己的 `api_key`
（2026-10-01 實測兩者 `sha12` 都是 `fe4b2d4ba82a`）。只輪換 peer 那把換不掉它 ——
`host-doctor.sh` 的 `rotate-hint` 會轉成 `[ ok ]`，但那是**指紋不同了，不是
外洩值失效了**。真正要吊銷它得另外輪換各機自己的 `QDRANT_API_KEY`（per-host）。

⚠️ **`compose.yaml` 是 M2 的範圍，本模組不動它。** 順序是
M2 先加 `alt_api_key` 並在三台部署 → 才輪換 peer 那把。反過來做就是上面那個
永久 401。

**沿革（為什麼舊的 9 把分類是錯的）**：2026-09-26 的「本機 200／遠端 401」根因是
`backend/.env` 與根 `.env` **兩份副本**（已刪），不是「key 需要三台同值」。根因修掉後
舊分類被留著當保險，副作用是造出「mbp 的 qdrant key 還是第三把舊的」這個**不存在的
故障** —— mbp 的 key 只對 mbp 自己的 qdrant 有意義。代價是每次輪換都要三台鎖步加重啟。

**連帶效果（這是改分類最大的收益）**：各機**不需要做任何事**。`pull` 只新增／覆寫、
從不刪除 `.env` 既有的鍵，所以 x570/mbp 現有的 `QDRANT_API_KEY`／`POSTGRES_PASSWORD`
原封不動（`tests/test_env_sync.py` 有測試證明這點，不只靠推理）。
輪換這兩把 = 只改該機 `.env` ＋ 重啟該機容器，不需要 commit、不需要另外兩台 pull。

⚠️ **`POSTGRES_DSN` 裡的密碼不是 `POSTGRES_PASSWORD`**：後者是本機 pg 容器的密碼
（各機可不同），DSN 指向 x570。**值到齊前總表三列 `POSTGRES_DSN` 保持空值**，
不要寫死 —— 理由與正解見 §4。

## 8. 一個鍵只能被一層認領

追蹤檔不得出現任何憑證值；per-host 機密永不進 sops、永不進總表。`--check` 會驗：

* 鍵覆蓋率：`.env` 必須有 6 把共用 ＋ 2 把 per-host 機密 ＋ `common.env` 的鍵
* `secrets.common.env.example` 的鍵 == 腳本的 `SHARED_SECRETS`
* `secrets.host.env.example` 的鍵 == 腳本的 `PER_HOST_SECRETS`
* **per-host 機密的鍵名不得出現在 `secrets.common.enc.env`**（sops 的 dotenv 輸出
  格式讓鍵名保持明文、只有值是 `ENC[...]`，所以這條**不需要解密就能驗**，CI 沒有
  age 私鑰也跑得到）
* `secrets.host.env`（明文）不得被追蹤


## 9. 為什麼前綴不在 `.env` 裡

`docker compose` 只認 `${VAR}` 插值，沒有「依 `HOST_ID` 動態選 `msi_`／`x570_`」
的能力。所以若把 `.env` 裡的 `LLM_MODEL` 改名成 `msi_LLM_MODEL`：

* compose 插到空值 → 回退 `rag.py` 的原始碼預設 `qwen3:14b`，MSI 的 8b 設定
  **被靜默吃掉**（沒有任何錯誤訊息）
* `TS_IP` 更嚴重：`ports: ["${TS_IP}:6333"]` 會去 bind 預設值那台機器的 IP，
  docker 直接啟動失敗

因此前綴活在**被追蹤的總表**裡，`render` 才挑列寫成不帶前綴的鍵。
好處是三台的值第一次變成同一個檔裡可比對、可 review、受 code review 保護。

同一台機器**同時**要有多組設定時，機台名放進**值**而不是變數名：
`HOST_API_URLS=x570=https://…,msi=https://…`（逗號分隔，順序無特別意義；
未設＝單機無 peer）。格式與 `OLLAMA_URLS`／`QDRANT_URLS` 一致，純網址也收
（id 由主機名第一段推導）。

> 2026-09-27 之前這裡是 `HOST_API_X570` / `HOST_API_MBP` / `HOST_API_MSI`
> 三個變數（`rag.py:167-171`）。那種寫法把機台名燒進**變數名**，第 4 台就得
> 改程式重新 build。舊鍵全刪、不留相容 fallback；`.env` 裡若有殘留，
> `env-audit.py` 會報成幽靈鍵並給遷移指引。

## 10. 總表規則（`hosts.shared.env`）

* 格式 `<機台前綴>_<鍵名>=<值>`，前綴只允許 `x570` / `mbp` / `msi`
* **每個鍵三台都要有列**（缺列＝漏改，render 直接報錯）；值可以空
* **空值＝該機沿用自己 `.env` 現值**，render 不覆蓋
  —— 我不知道那台的值時就留空，不要猜：猜值寫進被追蹤的檔等於把謊言版本化
* `${VAR}` 在 render 時用該機 `.env` 現值展開；引用的變數為空就**硬失敗**
  （寫出空密碼的 DSN 比不寫更糟）
* `OLLAMA_MODELS` 與 `OLLAMA_URLS` 位置對應，長度不一致 render 會擋
  （`rag.py:215` 用 index 取值，長度不符只會「某台 ollama 拿到別台的模型」）
* `HOST_ID` **不在表內**：它是 render 的選擇器，要先知道本機是誰才挑得到列

## 11. 各機一次性設定

### 1. 裝工具

```bash
# macOS
brew install age sops

# Ubuntu / WSL（免 sudo，放 ~/.local/bin）
B=https://github.com/getsops/sops/releases/download/v3.13.3
curl -fsSL -o /tmp/sops-v3.13.3.linux.amd64 "$B/sops-v3.13.3.linux.amd64"
curl -fsSL -o /tmp/checksums.txt "$B/sops-v3.13.3.checksums.txt"
# ↓ 檔名必須跟 checksums.txt 記的一致（否則驗不到），且要在同一個目錄
(cd /tmp && grep 'linux.amd64' checksums.txt | sha256sum -c -)
install -m 755 /tmp/sops-v3.13.3.linux.amd64 ~/.local/bin/sops
rm -f /tmp/sops-v3.13.3.linux.amd64 /tmp/checksums.txt

A=https://github.com/FiloSottile/age/releases/download/v1.3.2
curl -fsSL -o /tmp/age-v1.3.2-linux-amd64.tar.gz "$A/age-v1.3.2-linux-amd64.tar.gz"
(cd /tmp && tar xzf age-v1.3.2-linux-amd64.tar.gz \
  && install -m 755 age/age age/age-keygen ~/.local/bin/ \
  && rm -rf age age-v1.3.2-linux-amd64.tar.gz)
```

> **arm64 請把上面三處 `amd64` 換成 `arm64`**（`uname -m` 看）。

**兩條下載路徑的信任強度不對等，說明如下**（2026-09-30 x570 回報）：

| | 有 checksums 檔 | 完整性怎麼驗 |
|---|---|---|
| **sops** v3.13.3 | ✅ `sops-v3.13.3.checksums.txt` | `sha256sum -c` 可機械驗證 |
| **age** v1.3.2 | ❌ 只有 `minisign` 的 `.proof` | 只能靠 TLS + GitHub 帳號；`.proof` 要先有 minisign 公鑰才能驗，而那把公鑰本來也從同樣的管道來 |

**實務結論**：sops 那條可以機械驗，age 那條不行 —— 這是上游的取捨，不是本專案
的漏洞。兩者都是從 `github.com` 的 release 抓，攻擊者要同時攻破 TLS 與 GitHub
帳號才能換掉二進位。若要更強，兩者都改用 distro 套件或自己編譯 + 記錄
`--version` 的輸出。

> **踩過的坑**：`checksums.txt` 裡記的是**原始檔名**
> （`sops-v3.13.3.linux.amd64`）。若下載後 rename 成 `sops`，
> `sha256sum -c` 會報 `No such file or directory` —— 那不是下載失敗，
> 是檔名對不上。要嘛在原始檔名的目錄裡驗，要嘛先改名成它記的名字。

驗版本（README 從這裡開始釘死這兩個版本）：

```bash
age --version    # age 1.3.2
sops --version   # sops 3.13.3
```

### 2. 生自己的 key（已有則跳過，**絕不重建**）

```bash
mkdir -p ~/.config/sops/age && chmod 700 ~/.config/sops ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt
```

> 重建會讓已加密的檔案解不開（舊私鑰不在名單裡了）。要撤銷只能換新的公鑰，
> 那等於所有共用憑證都要換值。

### 3. 在 `.env` 設 `HOST_ID`（render 的選擇器）

```bash
HOST_ID=msi   # 或 x570 / mbp
```

### 4. 回報公鑰

```bash
# 只跑這行 —— 公鑰是公開的，可以回報。私鑰絕對不要貼，也不要 cat 整個 keys.txt。
grep -oE 'age1[0-9a-z]+' ~/.config/sops/age/keys.txt
```

### 5. 公鑰進 `.sops.yaml` 後，**有私鑰的人**跑一次

```bash
sops updatekeys settings/env/secrets.common.enc.env
git add .sops.yaml settings/env/secrets.common.enc.env
git commit && git push
```

⚠️ **只加 recipient 不 updatekeys 是無效的** —— 檔案裡的資料金鑰仍只加密給原本的
recipients，新公鑰形同虛設。症狀是該機器 `env-sync.sh pull` 報：

```
Failed to get the data key required to decrypt the SOPS file.
  age1...: FAILED
    - age: no identity matched any of the recipients.
```

⚠️ **`updatekeys` 會互動式問 y/n**（2026-09-30 msi 實測：直接跑會卡在
`Is this okay? (y/n):` 然後 EOF 失敗）。非互動環境要 `echo y | sops updatekeys ...`。

⚠️ 執行 `updatekeys` 的機器只需要**任一把**在名單裡的私鑰，不需要三台都有 ——
它重加密整份檔案，其他機器之後 `pull` 自然就通了。（2026-09-30 實測：msi 手上
只有自己的私鑰，一次 updatekeys 兩台都解得開。）

**2026-09-30 現況**：三把公鑰都已在名單裡（msi／x570／mbp），`updatekeys` 已跑。

## 12. 日常操作

```bash
scripts/env-sync.sh pull            # 解密＋合併＋render（一次同步所有共用層）
scripts/env-sync.sh render          # 只做 per-host（不需 sops）
scripts/env-sync.sh render --dry-run  # 只印「會動哪幾個鍵」，不寫檔、不印值
scripts/env-sync.sh --check         # 鍵覆蓋率＋總表 schema＋漂移＋版控衛生
scripts/env-sync.sh --fingerprints  # 6 把共用（跨機比對）＋2 把 per-host（不跨機比對）
scripts/env-prune.py --dry-run      # 檢查 .env 裡的空值賦值（見 §4 追加條）
scripts/env-prune.py                # 清理：刪掉有預設的空值、註解掉設定了也不生效的
```

輪換**共用**憑證：用 `scripts/rotate-secret.sh <KEY> --from-stdin`
（它會解密→換那一行→重加密，並驗「鍵數沒變、recipients 沒變、換完仍解得開、
其他鍵的指紋不變」）。**不要手動 `sops -d`／改檔／`sops -e`** ——
`--filename-override` 與 `--config` 兩個坑都在那支腳本裡註解著。
**不要重跑 `--init-secrets`** —— 它是「第一台建立加密檔」用的，會覆蓋整份。

⚠️ 輪換 `QDRANT_PEER_API_KEY` **有前置條件**，見 §7〈peer 那把的語意〉：
`compose.yaml` 必須先有 `QDRANT__SERVICE__ALT_API_KEY`（M2 的範圍）並在三台部署好。
在那之前輪換 = 兩台備援機永久 401。

換完之後各機（**順序有意義**，見下）：

```bash
git pull && bash scripts/env-sync.sh pull && docker compose up -d
```

備援機（mbp／wsl）**必須先 `env-sync.sh pull` 再 `docker compose up -d`**：
反過來的話 `.env` 已是新 peer key 而容器裡的 `alt` 還是舊的，那一輪同步會
對本機 qdrant 401（`:263` 容忍、`:265` 中止 → 只是白跑一輪，log 有誤導性，
不會損資料）。想完全避開就**先暫停同步排程**再照上面做。

輪換**per-host 機密**（`QDRANT_API_KEY`／`POSTGRES_PASSWORD`）：**只改該機 `.env`**，
重建該機容器。不進 sops、不 commit、另外兩台**不需要做任何事**。要確認這台真的換掉了，
就在換前後各跑一次 `--fingerprints` 看那兩行的 sha12 變了（值不同不是故障，
本來就各機不同）。

per-host 值要改：改 `hosts.shared.env` 那一列 → commit → 各機 `render`。
`--check` 會在 `.env` 與總表不同時失敗並列出鍵名（不印值）。

## 13. 絕對不要做的事

**權威版在 §4〈陷阱〉** —— 避免第二真相，這裡只放最要命的摘要，
完整清單（含每條的理由）請讀 §4。

* 不要 `cat .env`／`env | grep KEY`／對 `env-sync.sh` 跑 `bash -x`
* 不要 `docker compose config` 後貼輸出（用 `config -q` 只驗語法）
* 回報只給「鍵名＋長度＋sha256 前 12 碼」，格式見 `--fingerprints`
* 不要在總表裡寫死任何密碼，特別是內嵌 x570 密碼的 `POSTGRES_DSN`

## 待補（owner）

- **scope D**：`settings/env/manifest.tsv` ＋ 4 個雙向檢查 ＋ 接進 CI。
  現在 67 個變數裡 41 個（61%）沒有歸屬，`ci.yml` 完全沒跑 env 稽核 ——
  **目前沒有任何自動證據顯示三台一致**。
- **scope E**：`common.env` 填真值（現在 7 個鍵值全空，等於沒有分發作用）
  ＋ `.env` 內 provenance 標記（`pull` 只增不覆寫手改的鍵，
  所以手改與被 sync 的鍵長得一模一樣，無法分辨）。
- 把 `backend/DESIGN.md` §4 的「刻意不傳入容器的 3 個變數」升級成 manifest 的
  顯式欄位（`forward=always｜never｜host-only` ＋ 理由欄），
  讓 `env-audit` 能把「刻意不傳」與「忘了傳」分開報。
- `RERANK_MODEL` 是**幽靈分發鍵**（有分發、無消費者，見 §4 倒數第三條）：
  要嘛接上線（`backend` 的 scope H），要嘛從 `SHARED_CONFIG` 拿掉。
