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
**已完成 scope `F`**（`scripts/host-sync.sh` ＋ `scripts/host-doctor.sh` ＋ `scripts/DESIGN.md`），
驗收結果見下。三台的套用方式在〈待套用〉。**還沒在真實部署路徑上跑過** —— 本機
（msi）刻意不啟動容器，所以 `compose up` 與 `verify` 兩步只做了靜態檢查。

**為什麼做這個**：`HOST-UPGRADE.md`(351 行)＋`X570-HANDOFF.md`(310 行)＝601 行散文
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
| 加 `POSTGRES_PEER_PASSWORD` | 先 `x570` | 見 `X570-HANDOFF.md` 事項 2 | 被「x570 的 pg role 密碼未知」阻塞。值到齊後才加，**不要提前加沒人讀的幽靈鍵** |
| 收 age 公鑰 | `x570`、`mbp` | 見 `X570-HANDOFF.md` 事項 5 | 公鑰到齊 → `.sops.yaml` 加 recipients → `sops updatekeys` |
| 修 git 認證 | `x570` | `gh auth refresh -h github.com -s repo` | repo 轉 private 後舊 token 失效。**需使用者在該機互動執行** |

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
