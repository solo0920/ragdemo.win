# SCOPE —— 開發範圍登錄簿

**同時最多一個「進行中」。** 這是本專案最重要的流程紀律，原因見 `ARCHITECTURE.md`
〈架構表〉：三台機器共用同一份 repo，一份跨模組的半成品會被另外兩台的
`env-sync pull` 自動分發出去，症狀是「那台莫名其妙壞掉」。

## 怎麼用這份檔

1. 動任何程式碼之前，先在下面開一個 scope，填完「所屬模組／目標／不做什麼／驗收」
2. 開發期間只碰該模組的檔案。要碰別的模組 → 關掉這個 scope，另開一個
3. 驗收條款全過 → 狀態改成 `完成`，並把結果寫進 commit 訊息
4. 驗收不過 → 留在 `進行中`，不要開新的

### 唯一的並行例外

兩個 scope 可以同時進行，條件是**檔案範圍完全不重疊**，而且各自有獨立的驗收指令。
2026-09-27 的 A（`env-ops`）與 B（`backend`）就是這樣平行跑完的。

**序列化的是共享檔**：`SCOPE.md`、`.env.example`、`ARCHITECTURE.md`、
`tests/test_env_*.py`、`.opencode/agents/*`。誰要先碰就先關掉自己的 scope。

## 進行中（最多一個）

| 代號 | 模組 | 所屬模組／目標 | 不做什麼 | 驗收 | 預計完成日 |
|---|---|---|---|---|---|
| `M` ⏸️ 擱置（未執行） | 跨 M3／M6／frontend／M1 | **解除三台鎖死：只要本機有就能跑** | 2026-10-09 使用者裁決：擱置不做。原因：scope `K` 已明寫不做其內容，A/B/C 仍是未完成的阻塞項；且檔案範圍與 `M0` 重疊（compose、前端），不能並行。A/B/C 保留在「沒做而記錄下來」，見原 `### scope M` 節（保留不刪）。**不推送** | — | 2026-09-27 |
| `M0` ✅ | 跨 backend／frontend／compose／主機層 | **判決問答止血與解鎖**：前端「判決摘引」不誤導、容器內服務可達、基線可重現 | 不動 B2/B3/B4 判定邏輯、不重 chunk 凍結語料、不解壓新卷宗、不碰 x570/mbp/wsl 的 `.env` 憑證值、不 push。**⚠ 2026-10-09 使用者裁決 `M` 擱置，本格轉正**。⚠ UnRAR 原列「不裝」**已由使用者在 scope 外授權完成**（見〈M0 驗收結果〉） | 見下方 `### scope M0 驗收結果` | 2026-10-09 |
| `L` ✅ | M6 | 修 `host-doctor.sh` 的**靜默陳舊**：本機快照同步停擺時仍報 `law-version ok` | 不加第 16 個檢查（擴充既有的 `ch_law_version`，檢查數維持 15）、不改門檻語意成 fail（跨機無法實測，只能 warn）、不碰 sync 排程本身。**沒**順手修 `env-audit.py` 的 `SH_ASSIGN`（屬 M1，且屬行為變更） | 見下方驗收表 | 2026-09-27 |
| `K` ✅ | **M1 限定**（原訂跨 M1／M6） | **msi 成為唯一開發主機**：本機自給自足，實測跑起來 | **不做**「解除三台寫死」——那要動 `backend/app/rag.py`（M3）與 frontend，屬另一個模組、另一個 scope（見〈沒做而記錄下來〉）。不動 x570/mbp 的既有設定。**不推送** | 見下方驗收表 | 2026-09-27 |
| `J` ✅ | 跨模組（**原訂文件限定，實際跨到 M6**） | 瘦身：刪掉可證明已被取代的文件與章節，準備 msi 重灌 | **不刪** `X570-HANDOFF.md`（**2026-09-30 精簡**：原本的事項 2/3/4 已作廢或失效，只剩事項 5 age 公鑰是真阻塞）、不刪 `ROADMAP.md`（歷史）、不刪 `settings/opencode/`（重灌要靠它）。⚠ 原訂「只動 `.md`」**沒守住** —— 見下方〈越界說明〉 | 見下方驗收表 | 2026-09-27 |

### 為什麼現在做（2026-09-27 使用者決策）

**msi 是唯一開發主機；開發完成後把 msi 的版本部署到任何有 Docker 的主機；不再去別台開發。**

這條決策推翻兩條既有前提，本次一并處理：

1. **「msi 的容器刻意維持 `exited`」作廢。** 那是「msi 只是 demo 機」的副產物。
   開發主機必須跑得起來，本 scope 會啟動並實測。
2. **msi 必須自給自足。** 實測當下 msi **連不到 x570 也連不到 mbp**
   （`100.119.83.111`／`100.64.121.9` 全部 TCP 拒絕），所以任何指過去的
   依賴都會讓開發機開機即半殘。

**為什麼現在做**：msi 即將重灌，而重灌後只靠 clone 這份 repo 復原，所以 repo 裡
「過期但看起來還在用」的東西**會直接誤導重灌後的自己**。這是瘦身的最佳時機。

### scope `M`：解除三台鎖死

**目標**：一台有 Docker 的機器，`git clone`＋設定完就能跑；第 2、3、4 台是**加**上去的，
不是寫在程式裡的前提。

**先盤點再改**（`grep` 全 repo，`.py`/`.sh`/`.ts`/`.svelte`/`.yaml`/`.yml`），
指名遠端機的地方共 18 處，扣掉純註解與稽核器條文後**實質 12 處**：

| # | 位置 | 性质 |
|---|---|---|
| 1 | `compose.yaml:64-66` `HOST_API_X570/MBP/MSI` | 鎖死，第 4 台要改 compose 並重新 build |
| 2 | `rag.py:167-171` `HOST_API` | 同上，且**本機只因剛好寫在清單裡才出現在 `log`** |
| 3 | `rag.py:229` `_KNOWN_IPS` | 鎖死 3 個 IP，只服務 `host_label()` |
| 4 | `rag.py:33` `OLLAMA_DEFAULT` | 預設值是 x570 的 tailscale IP |
| 5 | `compose.yaml:37-38` OLLAMA 預設 | 同上 |
| 6 | `compose.yaml:67` `TS_IP:-100.119.83.111` | 預設值是另一台機器的身份（`env-audit` 早就在報這個） |
| 7 | `+page.svelte:4-10` `BACKENDS` | 鎖死三台的選擇器 |
| 8 | `+page.svelte:289-290` `HOST_IPS`/`HOST_NAMES` | 鎖死，且 `HOST_NAMES` 是中文角色名（主機/加速/demo） |
| 9 | `+page.svelte:305,320` 兩處 `['x570','mbp','msi']` | 鎖死 |
| 10 | `+server.ts:9-13` `DEFAULT_ORIGINS` | 鎖死 |
| 11 | `+server.ts:61` `if (DEFAULT_ORIGINS.includes(single)) return [...DEFAULT_ORIGINS]` | **主動鎖死**：設單一 origin 若剛好是那三台之一，會被偷偷展開成三台 |
| 12 | `sync-snapshot.sh:19`、`law-update-worker.sh:58`、`qdrant_load.py:24` | 預設值都是 x570 的位址 |

**設計原則：單一來源 + 預設值不得是「別台機器的身份」。**

| 項目 | 從 | 改成 |
|---|---|---|
| peer 清單 | 3 個變數名 | 單一 `HOST_API_URLS=x570=url,msi=url`（後端與 worker **同格式**），未設＝單機無 peer |
| `log` | 本機靠寫在清單裡 | **永遠含本機**（能回應即證明活著），這是契約的一部分 |
| `TS_IP` | 必填，預設 x570 的 IP | **選用**：未設就綁 `127.0.0.1` → 沒有 Tailscale 也能跑 |
| OLLAMA 預設 | x570 tailscale IP | `host.docker.internal`（Docker Desktop 自動；Linux 靠 compose 的 `host-gateway`） |
| 前端主機清單 | 4 處寫死 | 從 `GET /hosts` 與 `log`/`versions` 的**鍵**推導 |
| worker origin | 內建三台＋偷偷展開 | 只認 `API_ORIGINS`；`id=url` 與純 url 都收 |

**刻意保留舊變數的自動相容？** 不保留。`HOST_API_X570/MBP/MSI` 全刪。
理由：留著就是使用者說的「非必要程式碼」。但**刪除不能是靜默的** ——
`env-audit.py` 會把殘留的 `HOST_API_*` 鍵報出來，所以三台升級時會被叫到。

### scope `M0`：判決問答止血與解鎖（2026-10-09 開工，`M` 已擱置）

**目標**：判決問答不誤導（前端「判決摘引」隱藏或標實驗性）、容器內
`/health` ok 且 `/query`、`/judgments/query` 皆 200、基線可重現
（pytest 1717 passed／S4 eval PASS）。北極星：金融法律問題→真實判決→
判決引用的法條全文→逐點可追溯的答案（jid→chunk→span→條號→法條全文）。

**不做什麼**：不新增模組／抽象層；不改 B2/B3/B4 判定邏輯（A-01~A-11 凍結）；
不重 chunk 凍結 430 筆；不裝 UnRAR、不解壓（M1 事）；不動 `.env` 憑證值；
不 push；不碰 `_strip_article_refs` 184 問題（另開 scope）。

**驗收**：
- 容器內 `GET /health` 200；`POST /query`、`/judgments/query` 皆 200
  （證據不足時 200＋誠實 abstain 亦算過，500 算不過）。
- `pytest -q` 回歸全綠；`agent/scripts/eval_judgements_slice.py` 仍 PASS。
- ollama 綁定與 `LLM_MODEL` 預設變更**只提案、不執行**（等授權，見下）。

**待授權（停等回覆，不先做）**：
1. `M`（2026-09-27）是否已完：若完，標 ✅ 關閉；若未完，M0 與之檔位衝突需另議。
2. ollama 綁定：建議 `OLLAMA_HOST=0.0.0.0`＋防火牆限 docker bridge
   （現況 127.0.0.1-only，容器全滅，連法規 `/query` 都 500）。
3. `LLM_MODEL` 預設：`qwen3:14b` 未安裝，建議改 `gemma3:12b`（benchmark 14/14）。
   改任一機 `.env` 都需逐台授權。

### scope `M0` 驗收結果（2026-10-09 關閉；全部實跑，非推論）

**三項待授權全部有回覆，且執行只做授權過的範圍**：

| # | 授權事項 | 使用者回覆 | 實際動作 |
|---|---|---|---|
| 1 | `M` 處置 | **擱置不做** | `M` 標 ⏸️，`M0` 轉正 |
| 2 | ollama 綁定 | 採**選項 B**（綁 `172.17.0.1`，禁 `0.0.0.0`）＋防火牆層 | systemd drop-in ＋ `/etc/hosts` ＋ `ufw`（見 E1／E5） |
| 3 | `LLM_MODEL` | 走總表 `x570_LLM_MODEL=gemma3:12b`，**不手改 `.env`** | `settings/env/hosts.shared.env` ＋ `render` ＋ `--check` |

**主機層驗收（x570 本機 Linux 指令；E5 是 msi 外部視角，由使用者執行）**：

| 項 | 指令 | 結果 |
|---|---|---|
| E1 綁定名單 | `ss -ltnp` | ✅ 唯一 listener `172.17.0.1:11434`（ufw 啟用**後**複測仍成立，綁定未被防火牆動到） |
| E2 容器→ollama | 容器內 `urllib` 打 `http://host.docker.internal:11434/api/tags` | ✅ 回 5 個模型；ufw 啟用**後**複測仍通（`172.18.0.0/16` 規則正確） |
| E3 端點 | 容器內端到端 | ✅ `/query` eval **14/14 hit、5/5 neg**；`/judgments/query` 200 |
| E5(a) msi 視角有效 | `Test-NetConnection -Port 22` × `192.168.0.4`／`100.119.83.111` | ✅ True／True |
| E5(b) 綁名單外不可達 | `curl http://<兩位址>:11434/api/tags` | ✅ rc=7（約 2 秒拒絕），兩個位址皆然 |
| E5(c) 同網段路由注入 | 加 host route 後 curl | ✅ **ufw 前 rc=0（通）→ ufw 後 rc=28 逾時（DROP）**：證明「沒綁也擋得住」 |
| E5 清理 | `route delete` | ✅ 回 `確定!`（不存在時會報 `cannot find the file`，故此為刪除成功的權威訊號） |
| E6 tailscale | `tailscale status`／`ip`／`debug` 前後比對 | ✅ 輸出逐字相同；全程未執行任何改設定指令 |

**回歸基線（關閉前重跑）**：

| 指令 | 結果 |
|---|---|
| `pytest -q -p no:cacheprovider` | ✅ **1723 passed, 119 skipped, 2 deselected in 43.42s** |
| `agent/scripts/eval_judgements_slice.py`（live，Qdrant＋gemma3:12b） | ✅ `SLICE: PASS`（grounded 3/3、top-jid 3/3、needles 4/4、spans 25/0、statute-identity-bad 0） |
| `npm run build --prefix frontend` | ✅ 兩段 `✓ built in 235ms`／`3.15s` |

**⚠ 主機層回滾（缺一不可，全需 sudo；每項都請使用者動，agent 無 sudo）**

1. ollama 綁定：`sudo rm /etc/systemd/system/ollama.service.d/bind-docker0.conf && sudo systemctl daemon-reload && sudo systemctl restart ollama`
2. `/etc/hosts`：移除 `172.17.0.1 host.docker.internal` 那一行
3. `settings/env/hosts.shared.env`：還原 `x570_OLLAMA` 原值後 `bash scripts/env-sync.sh render`
4. **防火牆（本次新增，`ufw` 原為關閉狀態）**：`sudo ufw disable` 回到本 scope 之前的 posture
   （只想撤 11434 那條：`echo "y" | sudo ufw delete <N>`；互動式 `y|n` 會被非 UTF-8 位元組打成 `UnicodeDecodeError`，一律用 pipe 或 `--force`）

**ufw 現況（已生效，repo 之外）**：active、`Default: deny (incoming)`、允許
`22/tcp`、`139,445/tcp`、`11434 ← 172.18.0.0/16`（含 v6 對應項）。
**啟用時曾出現一條我們沒下過的 `11434/tcp ALLOW IN Anywhere`（編號 1），已刪除**——
推測是 ufw 停用期間就寫在 `user.rules` 裡的舊規則（停用時下 allow 只寫檔不生效，
`enable` 才讓它變活）；`/etc/ufw/user.rules` 權限 0640 root，agent 讀不到，
**此推測標 UNVERIFIED**，未經證實不當結論。

**關閉時仍為 UNVERIFIED（如實記錄，不當已驗）**：

- 開機後的綁定順序（`Restart=always` 已確認，但未重開機驗過）
- 瀏覽器實際 render（`npm run build` 通過只證明編譯過，不等於畫面正確）
- mbp／wsl／sabre／note10 視角、Samba 139/445 綁 `0.0.0.0`（獨立待辦）
- `opencode.json` 內 `http://x570:11434/v1`（x570 無 IPv4 解析且從無 listener；已知問題，非 M0 回歸）
- `SC-007` 延遲歷史基線、`/judgments/answer`（B2-D）永遠誠實拒答（`LOW_RETRIEVAL_CONFIDENCE`）

**流程偏離（如實記錄）**：`specs/005` 停在 Draft，未走完
clarify／plan／tasks／analyze 就先實作（先止血後補規格）。驗收結果如上，
但規格本身未回頭補齊 FR↔SC 的逐條對照——留給文件 scope 處理。

### scope `L`：修 `host-doctor.sh` 的靜默陳舊

**找到的洞**：`ch_law_version` 只檢查 `.law_version` 檔案**存在**且有 `update_date`，
所以回 `[ ok ] law-version = 2026-09-18`。但當下實況是 **x570 離線**，
`sync-snapshot.sh` 每 10 分鐘記一次「source offline, skip」，`synced_at` 停在
21 小時前 —— 也就是說 **RAG 答的是 9 天前的法規，而 doctor 說「沒有 fail」**。
沒有任何症狀的故障比故障本身更糟。

（附帶更正：我在追查時一度以為 cron 每 10 分鐘在噴 `QDRANT_API_KEY: unbound variable`，
那是**看錯檔案**——腳本寫的是 `~/qdrant/sync.log`，我去看 `data/laws/sync.log`。
`unbound variable` 是 2026-09-26 就修好的舊版殘留，cron log 停在 22:00 之後沒新
stderr 就是它已修好的證據。**cron 本身是好的。**）

**修法**：擴充既有的 `ch_law_version`（不新增第 16 個檢查，檢查數維持 15），
用 `synced_at` 判陳舊度，超過 6 小時報 `warn`，並把同步 log 最後一行帶進訊息
（離線／其他原因一眼可分）。

| 驗收 | 結果 |
|---|---|
| 現況觸發 | ✅ 報「21 小時沒成功更新…同步 log 最後一行說來源離線」 |
| 分支：5 分鐘前 | ✅ `ok` |
| 分支：正好 6 小時（邊界，`>=`） | ✅ `warn` |
| 分支：5.9 小時（門檻內） | ✅ `ok` |
| 分支：帶 offset 的 ISO（模擬日後改格式） | ✅ 算得出 30 小時，**不靜默跳過** |
| 分支：無 `synced_at` 欄位 | ✅ 退回原本 `ok`，不誤報 |
| 分支：**無 sync log（模擬 source 機）** | ✅ `ok`，不誤報 —— 這是 x570 的情況，離線無法實測，所以用檔案存在與否分流，不猜 |
| 檢查數 | ✅ 仍 15 項、去重 15 項 |

**寫這個時自己踩到兩個坑，都記下來：**

1. **時區算錯 8 小時。** `synced_at` 由 `date '+%F %T'` 產生，是**無時區標記的
   本機時間**（msi 是 CST +0800）。第一版把 naive 當 UTC，實測報「13 小時」而
   實際已 21.9 小時 —— 門檻 6 小時實際變成 14 小時。改成**兩邊都用本機時間**
   同框相減，不猜時區；帶 offset 的字串另走一條路，不讓 `TypeError` 被吞成
   「靜默跳過檢查」。

2. **`.env.example` 多出一個 `why=`。** 我在 `case` 分支寫 `*offline*) why="…"`，
   開頭是 `*offline*) ` 不是 `why=`，而 `env-audit.py` 的 `SH_ASSIGN` 是**行首
   錨定**的 `match` → 抓不到那個賦值 → `why` 不算已宣告 → 它對 `${why}` 的讀取
   被誤判成「讀環境變數」→ CI 的「`.env.example` == `--template`」紅燈。
   修法是把它宣告成 `local … why=""`（**不是**去改稽核器）。

   **這是同一個假陽性家族的第三例**，前兩例（`while read` 目標、`for` 迴圈變數）
   都記在 `env-audit.py` 的註解裡。已知的根因是 `SH_ASSIGN` 只認行首賦值，
   而 `case` 分支前綴、`&&`/`||` 之後的賦值同樣抓不到。
   **沒有在這個 scope 修稽核器**（屬 M1，而且把 `match` 放寬成邊界搜尋會讓
   「被本機賦值覆蓋的真環境變數」不再被回報，是行為變更，要自己的驗證）。
   但這已經是第三次，第四次還是會踩 —— 建議獨立成一個 scope 收掉。

### scope `K` 驗收結果（全部實跑，非推論）

| # | 項目 | 結果 |
|---|---|---|
| 1 | 三容器啟動 | ✅ `docker compose up -d` rc=0，三者 `running`（原為 21 小時前 `SIGTERM` 正常關停，非崩潰） |
| 2 | `/health` | ✅ `ok:true`、`llm:qwen3:8b`、`llm_src` **第一次就命中本機** ollama（無 2×2.5s 死端點等待） |
| 3 | registry 自給自足 | ✅ `POSTGRES_DSN` 改指本機 pg 後，`/hosts` 回 1 筆（msi，含 4 個本機模型、`ok:true`） |
| 4 | 端到端 `/query` | ✅ 7.7s 出答案，`src` 顯示 qdrant 與 llm 皆本機；`竊盜罪` 回完整三項法條（`JEV:0.82 keep`） |
| 5 | `host-doctor.sh` 實測 | ✅ **scope `F` 那條從未驗證的路徑首次跑通**：15 項不重複、容器三項 `ok`、`registry` 檢查從 `skip` 變成有證據 |
| 6 | 總表回寫 | ✅ `env-sync.sh render` 展開 `${POSTGRES_PASSWORD}`；`--check` rc=0「per-host 值與總表一致（9 鍵，該機 msi）」 |
| 7 | 前端 dev | ✅ `vite dev` 起得了，首頁 HTTP 200（`node` 24.20.0、vite 8.3.1） |
| 8 | `hosts.shared.env` 陷阱註解 | ✅ msi 的 DSN 寫法**刻意與 x570/mbp 相反**且寫明原因，避免被誤讀成踩到已記錄的坑 |
| 9 | 執行期狀態 | ✅ 只啟動容器，**未改任何憑證值**；pg 密碼對齊是 `ALTER USER` 改**本機 pg 內的 role**（該 pg 原為零張表，無資料可毀） |
| 10 | CI 全關卡（`verify.sh`） | ✅ 13/13（pytest 165 passed 含下列第 11 項） |
| 11 | **改寫 `tests/test_env_sync.py` 的一個測試** | ✅ 見下〈一個測試擋住了正確的寫法〉 |

### 一個測試擋住了正確的寫法（`tests/test_env_sync.py`）

`test_shared_table_dsn_never_expands_own_pg_password` 斷言「總表一律不准出現
`${POSTGRES_PASSWORD}`」，理由是當時三台的 DSN 都指向 x570，而 `POSTGRES_PASSWORD`
是**本機** pg 的密碼。msi 改成指向**自己**的 pg（`postgres:5432`）之後，
`${POSTGRES_PASSWORD}` 從「踩坑」變成「唯一正確寫法」——但測試照舊擋下。

**沒有放寬它，而是改寫不變式。** 新版斷言的是「**密碼要跟 DSN 指向的那台對得上**」：
主機名是本機 compose 服務名 `postgres` 就必須用 `${POSTGRES_PASSWORD}`，否則必須用
對端的 `POSTGRES_PEER_PASSWORD`。理由寫在 docstring 裡（兩種寫法各自為什麼對）。

這樣改的好處是它**自我維護**：第 4 台若指自己就自動通過，若指跨機就自動被要求用
對端密碼。原版則會讓下一個指自己的主機再次紅燈，而修法只能退回去忍受離線依賴。

**改完有反向驗證**（不只看它變綠）：

| 注入的錯誤 | 預期 | 實測 |
|---|---|---|
| msi 指 x570 卻用 `${POSTGRES_PASSWORD}`（原陷阱） | 紅 | ✅ 紅 |
| mbp 指 `postgres` 卻用 `${POSTGRES_PEER_PASSWORD}`（反向錯） | 紅 | ✅ 紅 |
| 還原 | 綠 | ✅ 綠 |

順帶記錄：`--fingerprints` 對 8 把憑證輸出 `len=` 與 `sha12=`、**不印值**，
所以「比對兩台憑證是否同值」這件事在 repo 內已經可做，不需要人眼比對。

### 方法教訓：一支壞的驗證工具差點產出錯誤架構結論

查「主機清單是否單一來源」時用了臨時 `awk -F= '/^[A-Za-z_]+=/'` 印鍵名，
得出「`hosts.shared.env` 只有 `mbp_*`／`msi_*` 兩欄，x570 的值躲在 `compose.yaml`
預設值裡，因此是兩個真相」——並據此把「解除 x570 特權」列為本 scope 的阻塞項。

**該結論是錯的。** `[A-Za-z_]+` 不含數字，而 `x570_OLLAMA_URLS` 有 `570`，
於是**整列 x570 被正則靜默漏掉**，看起來像「x570 沒有欄位」。重查後三台欄位
完全對稱（9 鍵 × 3 機，無任何缺漏），`x570` 沒有特權，該阻塞項作廢。

寫成條目是因為它有兩層教訓：

1. **驗證工具本身要先自我檢驗。** 靜默漏掉資料的過濾器比沒有過濾器更危險——
   沒有過濾器時你會看到全部資料從而被懷疑，會漏掉時你會以為那就是真相。
2. **結論要能指出「是誰在讀」才敢拿來改架構。** 這個結論我是從「鍵名清單的
   缺口」推出來的，沒先確認誰在讀那個缺口，所以它其實不該成立。

`scripts/env-sync.sh` 與 `env-audit.py` 用的正則都是 `[A-Za-z_][A-Za-z_0-9]*`
（含數字），**不受影響**；受影響的只有我這次臨時寫的那支。

### 沒做而記錄下來（下一個 scope 的輸入，不在 `K`）

「依 msi 的版本部署到**任何**有 Docker 的主機」這個方向，本次只做了清點，
**沒動任何一處**。剩下三個真實阻塞項（已剔除先前誤判的第四項）：

| # | 位置 | 症狀 | 最小改法 | 會動到 |
|---|---|---|---|---|
| A | `compose.yaml:64-66` + `rag.py:167-171` | `HOST_API_X570/MBP/MSI` 三個變數名寫死（compose 註解自承「沒有依 `HOST_ID` 動態選前綴的能力」）。第 4 台要改 compose **並重新 build image** | 改成單一 `HOST_API_URLS=x570=https://…,new1=https://…`，`rag.py` 解析成 dict；保留三個舊變數當 fallback | `backend/app/rag.py` + `compose.yaml` |
| B | `frontend/src/routes/+page.svelte:289,290,305,320` | `HOST_IPS`／`HOST_NAMES` 與兩處 `['x570','mbp','msi']` 寫死，第 4 台要改前端再重新部署 | 改吃 `GET /hosts`（**後端已回動態清單，含 `host_id`／`ts_ip`／`models`／`ok`，前端目前沒在用**） | frontend |
| C | `scripts/env-sync.sh:63` | `HOSTS="x570 mbp msi"` 是 per-host render 的選擇器 | 從 `hosts.shared.env` 的前綴推導 | `scripts/env-sync.sh` |

A／B／C 互不相干，可各自獨立成 scope。**B 的資料源已經存在**（`/hosts`），
所以 B 大概是三個裡最划算的。

### 查到的真 bug（不在本 scope，僅回報）

`backend/app/rag.py` 的「法名＋條號」路徑（`rag.py:1711` 附近）對 LLM 答案跑
`_strip_article_refs()`，**把提到「第X條」的句子刪掉**——而那正是使用者問的東西。
剩下的文本餵給 JEV 自然低於 `JEV_VERIFY_MIN=0.4`，於是整段被規則卡取代。

實測（同一台、同一次啟動，僅換問法）：

| 問法 | 判定 | 答案 |
|---|---|---|
| `竊盜罪要如何處罰？` | `JEV:0.82 keep` | 完整三項法條 ✅ |
| `什麼情況下可以請求損害賠償？` | `JEV:0.17 keep` | 完整 ✅（非法名分支不套 `_strip_article_refs`） |
| `民法第184條` | `JEV:0.27 退回規則卡` | `《民法》（共1374條）` ❌ |
| `民法第184條的構成要件是什麼？` | `JEV:0.22 退回規則卡` | `《民法》（共1374條）` ❌ |

**這不是本 scope 造成的**（只動了 env，沒動程式），是既有行為。改它屬 M3，
需要自己的 scope 與測試。

### scope `J` 驗收結果（全部實跑，非推論）

| # | 項目 | 結果 |
|---|---|---|
| 1 | 每個刪除都有引用者已更新 | ✅ 刪 2 份 `docs/` 交接文件，唯一引用者 `ingest/laws/DESIGN.md` 已改寫成獨立陷阱條目 |
| 2 | `grep` 全 repo 無指向被刪內容的連結 | ✅ 兩份檔名 0 命中 |
| 3 | 失效引用掃描（自寫檢查器） | ✅ 剩下的 27 筆逐一分類：`ChLaw.json`／`.law_sync.json` 是**執行期或資料檔非版控**、`host-onboard.sh` 是 **scope `F2` 刻意還沒做**、`rules.py` 是**真 bug 已修**（見下） |
| 4 | `pytest -q` | ✅ 165 passed（與瘦身前同） |
| 5 | CI 全關卡本機重跑 | ✅ 語法／`LAN_IP=`／`compose config -q`／單檔 <10MB／憑證格式 全部過；`pnpm run build` ✓ built in 4.13s |
| 6 | `.env.example` == `env-audit --template` | ✅ 一致 |
| 7 | `env-audit.py --quiet` | ✅ 0 項需處理 |
| 8 | `bash -n` 全部腳本 | ✅ 5 支全過 |
| 9 | xtrace 拒絕 | ✅ 兩支新腳本 `bash -x` → rc=1 |
| 10 | `host-sync.sh --dry-run` | ✅ **實際攔下本次未 commit 的改動**並 exit 3 路徑 —— 護欄實測有效 |
| 11 | 死碼 | ✅ 零。backend 7 模組全有引用，frontend 5 route。**體積全在文件** |

### 行數對照

| 檔案 | 前 | 後 | 差 |
|---|---:|---:|---:|
| `docs/OPENCODE_HANDOFF_CHECKLIST.md` | 311 | 0 | **−311** |
| `docs/MODULAR_ARCHITECTURE_2026-09-27.md` | 378 | 0 | **−378** |
| `HOST-UPGRADE.md` | 351 | 259 | **−92** |
| `X570-HANDOFF.md` | 310 | 242 | **−68** |
| `ROADMAP.md` | 1183 | 1187 | +4（只修過期標題） |
| **全部 `.md`** | **5129** | **4292** | **−837（−16.3%）** |

刪的 849 行裡，**沒有一行是只有該處記載的資訊** —— 刪前逐條確認結論都存活在
`ARCHITECTURE.md`／`settings/env/README.md`／新的 `host-sync.sh`／`scripts/DESIGN.md`。

### 越界說明（`J` 動了 `.sh`，原訂只動 `.md`）

smoke test 抓到 `scripts/host-doctor.sh`（M6）的**「跑全部檢查」整段被貼了兩份**，
連帶一個亂碼字。症狀：每個檢查跑兩次、報告整段印兩遍、`--json` 條目數 double
（28 應為 15）、warn 計數翻倍。

- 為什麼是這個 bug：它是 scope `F` **自己**的產物，且 doctor 是三台唯一的診斷入口，
  報告重複會直接讓「讀報告的人」誤判狀態。
- 為什麼上次沒抓到：當時只驗「`--json` 是不是合法 JSON」—— 重複條目**仍然是合法
  JSON**。要驗的是**條目數**。已寫成該處的註解。
- 同理修掉 `ARCHITECTURE.md:19`／`:303` 殘留的 `rules.py`（真實檔名 `rules_store.py`）——
  scope `I` 宣稱修過這個錯，但這兩處漏了。

兩者都是「自己前一個 scope 留下的、且會誤導診斷」的問題，不修就是明知有錯還推給三台。

**已完成 scope `F`**（`scripts/host-sync.sh` ＋ `scripts/host-doctor.sh` ＋ `scripts/DESIGN.md`），
驗收結果見下。三台的套用方式在〈待套用〉。**還沒在真實部署路徑上跑過** —— 本機
（msi）刻意不啟動容器，所以 `compose up` 與 `verify` 兩步只做了靜態檢查。

**為什麼做這個**：`HOST-UPGRADE.md`(351 行)＋`X570-HANDOFF.md`(310 行)＝601 行散文（**2026-09-30 已把兩份 handoff 精簡到只剩真待辦**）
runbook，`scripts/` 下 0 個部署腳本，全 repo 沒有任何 `git pull`（升級完全靠人）。
這是**降低複雜度最多的單一改動**，而且是純新增檔案 —— 跑不動就刪，沒有後果。

**`--ref` 為什麼是重點**：它讓「部署特定版本」變成一個指令，
從此 replica 只吃 tag、不吃半成品 → 消失「一次只能開一個 scope」這條稅。
已查證切 ref 安全：`.env`／`data/laws/*`／`frontend/.env` 全 gitignored，
只有 `data/rules/rules.json` 是追蹤內容檔（應跟著版本走，正確）。

**驗收（2026-09-27 實測，非推論）**：

| 條款 | 結果 |
|---|---|
| ① 冪等 | 連跑兩次 `--dry-run --ref HEAD`，兩次都輸出「已在該 ref（ae0a912），略過切換」 |
| ② `--dry-run` 不寫檔 | 前後 `git status --porcelain` 完全相同 |
| ③ 未提交改動時拒絕 | 髒樹 → `exit 3` 且列出 3 個檔名；**未提供** `--force`／`--discard`／stash |
| ④ xtrace 拒絕 | `bash -x` 於兩支腳本皆拒絕執行 |
| ⑤ 輸出無憑證值 | 拿 `.env` 15 個真值（長度≥6）逐一比對人模式＋`--json` 輸出 → **命中 0** |
| ⑥ `bash -n` | 兩支都過 |
| ⑦ 三台行為不變 | `git status` 只有 3 個新檔，`compose.yaml`／`backend/`／`frontend/` 未觸碰 |
| ⑧ exit code 契約 | 髒樹→3、錯引數→2、ref 解析不到→4、doctor 無 fail→0，全部符合契約 |
| ⑨ `--json` | `python3 -m json.tool` 解析成功，15 項 checks |

**審查中抓到並修掉的一個真 bug**（subagent 自稱已實測、實際未生效）：
`host-doctor.sh` 的容器檢查**完全失效**。`docker compose ps --format json` 吐的是
**JSON Lines**（每行一個物件）不是 JSON 陣列，而 parser 先試 `json.load(sys.stdin)`
失敗後在 `except` 裡又呼叫 `sys.stdin.read()` —— **第一次 `json.load` 已把 stdin 吃空**，
fallback 永遠拿到空字串。症狀是每次都顯示「解析不了（版本差異？）」並標 `skip`，
於是「醫生自己壞掉」看起來像「這台沒資料」，真的壞機也只會顯示 skip。
修法：先一次讀完再判斷格式（兩種格式都真的存在，兩種都要能吃），
並把 parser 失效從 `skip` 改成 **`fail`** —— 這是本專案「拿不到的證據要報成拿不到，
不要報成沒問題」的原則。修完三個容器正確顯示 `exited Exited (0) 21 hours ago`。

**跨模組的一處，必須明寫（`scripts/env-audit.py` 屬 M1，`env-ops` agent）**

新腳本一進 repo 就把 M1 的 `env-audit.py --template` 打歪了 13 個假變數進 `.env.example`
（`SHELLOPTS`／`SVC`／`ST`／`HUMAN`／`MINS`／`HID`／`MISSING`／`OK`／`EMPTY`／`LINE`／
`FP`／`T`），`tests/test_env_audit.py` 的 `test_template_has_no_lan_ip_assignment`
（斷言 `.env.example == --template`）**紅燈並擋下 push**。三個成因，全在 scanner 的
「這是 shell 區域變數還是環境變數」判斷：

1. `while read -r A B` 的目標**是賦值**，但 `SH_ASSIGN` 只認 `NAME=` 開頭 → 抓不到。
2. `for T in ...` 的迴圈變數同理沒有 `=`。
3. `local A="" B=""` 一行宣告多個時，`SH_ASSIGN` 的 group(1) **只抓到第一個**，
   第二個之後漏掉。

修法是加三組比對（`SH_READ_ASSIGN`／`SH_FOR_ASSIGN`／`SH_DECL`），**只加寬過濾、
不新增任何回報** —— 也就是只會少報假變數、不會多報。`SHELLOPTS` 另加進 `TOOL_ENV`。
淨效果：`.env.example` 只多 1 個真變數（`HOST_API_LOCAL`，兩支腳本的 api 位置覆寫點）。

**為什麼在 F 裡改 M1 的檔案**：這個 bug 是 F 的產物造成的，而 M1 的工具在 M6 的新腳本前
**證明是壞的**（不修就是 CI 紅燈、push 擋住）。放著等下一個 M1 scope，repo 會停在
「CI 紅」的狀態。已經有回歸防護：上面那條 `test_template_has_no_lan_ip_assignment`
就是。**沒有**為此改 `tests/`（那是 M1 的序列化共享檔），要加針對性測試請另開 M1 scope。

## 待套用（程式改完了，但還沒在機器上生效）

這一節是「repo 與三台現況的落差」清單。**這些沒做，repo 就不是真相。**

| 項目 | 機器 | 指令 | 備註 |
|---|---|---|---|
| 套用 15 個容器環境變數 ＋ scope `F` | `msi` | `bash scripts/host-sync.sh` | **本機三個容器是 `exited`（21 小時前停的），不是「從沒建立過」**。`host-doctor.sh` 已能分辨（用 `--all`）。啟動與否是獨立決定，`host-sync.sh --skip-verify` 可略過驗收（exit 6，不會假裝通過） |
| 首次在真實路徑上跑 `host-sync.sh` | `x570`、`mbp` | `bash scripts/host-sync.sh`（不帶 `--ref`，先 `pull --ff-only`） | **這是 `F` 唯一沒被驗證過的部分**：`compose up -d --build` 與 `/health`＋`/status?probe=0` 驗收。建議在 x570 跑（它是 source 機，出事影響最大所以先試） |
| ~~套用 15 個容器環境變數~~ | `x570`、`mbp` | `bash scripts/host-sync.sh` | 已併入上一列。`host-sync.sh` 第 4 步就是 `env-sync.sh pull`，不必分開跑 |
| 換 `QDRANT_PEER_API_KEY` | 三台 | 改 `secrets.common.enc.env` → commit → 各台 `env-sync.sh pull` | 值已外洩（見 `ARCHITECTURE.md`〈密鑰管理〉）。**不需要逐台手動改**，`pull` 會覆蓋 |
| ~~加 `POSTGRES_PEER_PASSWORD`~~ | — | — | **2026-09-30 取消**。沒有任何程式讀它（全 repo 只剩註解、`.example` 說明文字、測試 docstring）。當初的跨機 pg 需求已隨「各台 `/hosts` 改讀自己的 pg」消失。**不要加幽靈鍵** |
| 收 age 公鑰 | `x570`、`mbp` | 見 `X570-HANDOFF.md` 事項 5／`MBP-HANDOFF.md` 事項 5 | **這是三機復原唯一還會擋住事的一項**：`.sops.yaml` recipients 只有 msi 一把，另兩台 `env-sync.sh pull` 會解不開 6 把共用憑證。公鑰到齊 → 加 recipients → `sops updatekeys` |
| 修 git 認證 | `x570` | `gh auth refresh -h github.com -s repo` | repo 轉 private 後舊 token 失效。**需使用者在該機互動執行** |

### msi 重灌前置（2026-09-27 查證，**重灌前必讀**）

已逐項確認「重灌後能不能只靠 clone 這份 repo 復原」：

| 會消失的東西 | 在 repo 裡嗎 | 結論 |
|---|---|---|
| `QDRANT_API_KEY`（per-host 機密） | ❌ 不在 repo、不加密、不同步 | **可重選**。只有 msi 自己的 api 讀它，本機 qdrant 是全新的 |
| `POSTGRES_PASSWORD`（per-host 機密） | ❌ 同上 | **可重選**。msi 的 `POSTGRES_DSN` 指向 x570，本機 pg 容器**沒有任何程式在用** |
| 法規快照（`data/laws/*`、`.law_version`） | ❌ gitignored | **會自己回來**，`sync-snapshot.sh` 從 x570 拉（10 分鐘內） |
| opencode 設定 | ✅ `settings/opencode/{global,project}/wsl/`（2026-10-01 由 `msi/` 改名） | **已備份**，含 `{file:...}` token 參照的路徑 |
| 憑證（6 把共用） | ✅ `secrets.common.enc.env`（sops+age） | `env-sync.sh pull` 會解密合併 |

**結論：沒有會永久遺失的東西。** 但重灌後要走完這條路徑（**目前沒有腳本自動化**，
就是 scope `F2` `host-onboard.sh` 要做的事）：

1. 裝 Docker（msi 是 Docker Desktop + WSL2，`TS_IP` 填 **Windows host** 的 `100.65.68.106`）
2. `git clone` ＋ 設 `HOST_ID=msi`
3. **`git config core.hooksPath .githooks`**（`HOST-UPGRADE.md` §0）—— 這是
   per-clone 的 git 設定，不在版控裡，`host-sync.sh` 管不到。**漏了不會報錯，
   只會讓 pre-push 的三項檢查（commit 前綴／語法＋`LAN_IP=`／pytest）靜默消失**。
   2026-09-29 實測：msi 重灌後這項不見了，連續 11 個 commit 在無任何檢查下產生。
4. **加自己進 docker 群組並重開終端**：`sudo usermod -aG docker $USER`。
   漏了的症狀是 `permission denied ... /var/run/docker.sock`（daemon 活著、
   群組沒加）。同樣是靜默失效：症狀要等到第一次 `docker compose up` 才出現。
5. `cp .env.example .env` → 填 `TS_IP`／`HOST_NAME`／`HOST_MACHINE_ID`（`HOST-UPGRADE.md` §0、§1）
6. `env-sync.sh pull`（解密 6 把共用憑證）＋ **重選** `QDRANT_API_KEY`／`POSTGRES_PASSWORD`
7. 加 `SRC_API_URL=https://api-x570.ragdemo.win`（`HOST-UPGRADE.md` §3.1，否則法規版本欄永遠是 `—`）
8. 設 crontab（快照同步 `*/10`）
9. **裝 cloudflared 並起 tunnel**（`api-<主機>.ragdemo.win` 是 530 的話）。
   備份有 `config.yml` 與 tunnel 憑證（`~/.cloudflared/`），但**二進位不會跟著備份**。
   2026-09-29：msi 重灌後三台全 530，前端完全連不上。
   開機自起用 `systemd --user` ＋ `loginctl enable-linger`（沒有 linger，
   沒有任何 Linux 進程時 WSL 會停，tunnel 跟著死，systemd 的 enabled 等於沒設）
10. `bash scripts/host-sync.sh` ＋ `bash scripts/host-doctor.sh` 驗收

⚠️ 步驟 4 的兩把 per-host 機密**要自己生**，repo 幫不上忙 —— 這是重灌唯一需要
「人決定」的一步。

## 佇列

依優先序。開一個就把它從這裡移走。

| 代號 | 模組 | 一句話目標 | 為什麼是它 |
|---|---|---|---|
| `D` | M1 env | `settings/env/manifest.tsv` ＋ 4 個雙向檢查 ＋ 接進 CI | 67 個變數裡 41 個（61%）沒有歸屬，`ci.yml` 完全沒跑 env 稽核 —— **目前沒有任何自動證據顯示三台一致** |
| `E` | M1 env | `common.env` 填真值（現在 7 個鍵值全空，等於沒有分發作用）＋ `.env` 內 provenance 標記 | 「共用非敏感設定」目前是個空殼，最後 7 個共用鍵還是靠人手複製；`pull` 只增不覆寫手改的鍵，手改與被 sync 的鍵外觀相同 |
| ~~`F`~~ | M6 ops | 進行中（見〈進行中〉）。原含三支腳本，本輪只做前兩支 | — |
| `F2` | M6 ops | `host-onboard.sh`（從零到能跑：一台新機裝 docker → 簽出 repo → `env-sync pull` → 進 registry） | 從 `F` 拆出。**必須排在 `F` 之後**：沒有 `host-doctor.sh` 當診斷基準就寫 onboard，等於再造一份散文 runbook。價值也較低——三台都不需要新增機器 |
| `G` | M1 ＋ M2 | 顯式化 `HOST_ROLE=source｜replica`，取代「有沒有 `.law_sync.json`」的隱式角色判定 | 三台常駐且 registry 顯示心跳（2026-09-27 決定），角色不該靠檔案存在與否推斷 |
| `H` | M2 backend | `EMBED_MODEL`／`RERANK_MODEL` 從 compose 寫死改成可設定 | 這 2 個是 `env-audit` 剩下的唯一真問題。修它會動到 embedding 模型＝動檢索行為，必須獨立一個 scope 並跑 eval |
| `S1` ✅ 已關閉（E4 複測仍無重現，條件齊備） | M2 backend（法條路徑限定） | `_strip_article_refs` 退回調查 | E4（LLM gemma 在線、JEV 在線以探測呼叫驗得 0.65）：兩題皆走條號精準分支逐字引用（conf=rule），`_strip` 未觸發（只作用無條號分支）；JEV 只評生成答案，本路徑 bypass LLM 故無 JEV 分數可記。原 claim 例證不成立，無 bug 可修，關閉 |
| `M1` | M1 env＋ingest | M1 司法選樣：spec 以此二 task 開頭——(1) UnRAR binary 安裝授權（pinned 7.13，decoder.py 已備）；(2) 補 T007 `scope.md`（法院／日期／筆數，maintainer 決策） | 2026-10-09 使用者裁決：M1 spec 的前兩個 task 固定為此二項 |

## 已完成

| 日期 | 代號 | 模組 | 結果 |
|---|---|---|---|
| 2026-09-26 | `env-converge` | M1 | `.env` 收斂為單一真相（刪 `backend/.env`）、`env-audit.py` 改為從程式碼反查 |
| 2026-09-26 | `sops-dist` | M1 | 共用憑證改走 sops+age 加密分發、`env-sync.sh` 誕生 |
| 2026-09-27 | `per-host-prefix` | M1 | per-host 值加機台前綴集中在被追蹤的總表、`render` 步驟、總表 schema 檢查 |
| 2026-09-27 | `secret-boundary`（A） | M1 | 憑證分界線改正：`QDRANT_API_KEY`／`POSTGRES_PASSWORD` 由「9 把共用」改成 **7 把共用 ＋ 2 把 per-host 機密**。新增 `secrets.host.env.example`、`PER_HOST_SECRETS`、合併引擎的 per-host 防線（apply 剔除／check 硬失敗）、`--check` 納入 per-host 層、**不解密就能驗**的「per-host 不得進加密檔」檢查（sops dotenv 讓鍵名保持明文，CI 沒有 age 私鑰也跑得到）、`.gitignore` 擋明文檔、測試 34 條。**另外兩台不需要做任何事**（有測試證明 pull 後 `.env` 那兩個鍵值原封不動）。副作用：作廢了 `HOST-UPGRADE.md` §0 與 `X570-HANDOFF.md` 事項 1 兩份「去另外兩台輪換 qdrant key 到同一把」的指示 |
| 2026-09-27 | `container-env`（B） | M2 | `compose.yaml` 的 api `environment:` 補上 15 個「程式讀得到但容器拿不到」的旋鈕，預設值**逐字抄** `rag.py`／`registry.py` 的 `os.getenv` 第二個參數（有測試連原始碼那一行一起鎖）。修掉 MSI「永遠降級成自己的 8b、從沒用過 x570 的 14b」這個**不報錯**的 bug。另 3 個刻意不傳（`QDRANT_URLS`／兩個 `*_FILE`）並在 `backend/DESIGN.md` 記錄理由避免被當 bug 修回去；`OLLAMA_MODELS` 用巢狀 `${OLLAMA_MODELS:-${LLM_MODEL:-qwen3:14b}}` 而非字面值，抄成字面值會讓 MSI 三台 ollama 全滅；`.env.example` 重新產生；`env-audit.py` 修掉 `HOST_API_*` 因主機名含機台代號而來的假警告（`NAME_SCOPED_HOST`，不放寬 `TS_IP`／`HOST_ID` 的保護）；測試 19→39 條 |
| 2026-09-27 | `arch-table`（C） | 跨模組 | `ARCHITECTURE.md`〈架構表〉6 模組 × 目標／擁有者 agent／架構書／邊界 ＋ 模組契約 5 題 ＋ 三段升級路徑；本檔；`.opencode/agents/ops.md`（第 6 個 agent）；5 個既有 agent 補上契約／陷阱／升級路徑；`backend/DESIGN.md`、`frontend/DESIGN.md`、`scripts/DESIGN.md` 三份骨架 |
| 2026-09-27 | `F` | M6 ops | `host-sync.sh`（冪等部署單一入口，`--ref`／`--dry-run`／`--skip-verify`）＋ `host-doctor.sh`（7 段診斷、人類＋`--json` 雙輸出）。**全 repo 第一支含 `git pull` 的腳本** —— 601 行散文 runbook 從此有一個可執行的入口。刻意不提供 force flag（髒樹就 exit 3 並列檔名）、xtrace 拒絕執行、exit code 契約 0/1/2/3/4/5/6 且「略過驗收」＝6 不＝0。`--ref` 讓 replica 只吃 tag → 這條規則是解除「一次只能開一個 scope」的前提。**審查抓到 subagent 的假實測**：容器檢查因 `json.load` 吃掉 stdin 而 fallback 恆空，整段失效卻標 `skip`（醫生壞掉看起來像沒事），已修並改判 `fail`。詳見〈進行中〉的驗收表 |
| 2026-09-27 | `I` | 跨 M1／M3／M5（**文件限定**） | 補齊剩下 3 個模組的契約 5 題 → **6/6 架構書全部合約**。`settings/env/README.md`（原本 richest 但零契約章節）、`ingest/laws/DESIGN.md`（寫成管線設計、無邊界/不變量/驗收）、`ingest/cases/DESIGN.md`（草案，加契約並明示**未實作**）、`evals/README.md`（原本只有 4 行）。`files/` 三份文件收進 `docs/` 納入版控、刪掉 Windows `:Zone.Identifier` ADS 髒檔、`.gitignore` 補 ADS 規則。**逐項查證並修正 6 處事實錯誤**：`rules.py`→`rules_store.py`、`pytest backend/tests/`→根 `tests/`、PG `laws_keywords`→`article`、~~rerank() 是 stub~~（已實作，真正缺的是接 cross-encoder）、M1/M6 對 `env-sync.sh` 的**雙重所有權**劃給 M1、`__init__.py` 慣例未落實。另修正 M5 README 對 `/eval` 計分邏輯的錯誤描述（原說只算 `expect_case`，實際 19 題裡 `expect_case` 是 0 題、計分的全是 `expect_law`）。**零程式碼**：`.py`/`.env`/`compose.yaml` 全未觸碰，`pytest -q` 165 條不 regression |
| 2026-10-09 | `FR-007` | M3 ingest（spec 002 FR-007/FR-020，獨立小 scope，與 M0 檔位無交集） | 三層條文稽核工具：`ingest/laws/layer_check.py`（純比對：段數＋內容，缺層／不一致指到條／層／差異）＋`scripts/check-law-layers.py`（取樣確定性、缺層 exit 2、憑證只讀 env）＋`tests/test_law_layer_check.py`（6 條 fake-layers，守 tests 不碰外部服務慣例）。驗收：單元 6/6、live 50/50 exit 0、全套 1723 passed。未動舊檔一行；SC-002/005 仍待工具外的抽樣 harness／查詢日誌 |
