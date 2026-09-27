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

*（無）*

> scope `I`（文件限定、跨 M1／M3／M5）已於 2026-09-27 完成，結果見〈已完成〉。
> 當時的跨模組理由：產出**全部是 Markdown**，沒有任何可執行檔被改動，
> 因此不存在「跨模組半成品被另外兩台 `env-sync pull` 自動分發」的風險 ——
> 那個風險只來自程式碼與環境變數值。驗收條款刻意加上
> 「`git diff --stat` 不得出現 `.py`／`.env`／`compose.yaml`」，
> 一旦有人順手改了程式碼就會被自己抓到。

## 待套用（程式改完了，但還沒在機器上生效）

這一節是「repo 與三台現況的落差」清單。**這些沒做，repo 就不是真相。**

| 項目 | 機器 | 指令 | 備註 |
|---|---|---|---|
| 套用 15 個容器環境變數 | `msi` | `docker compose up -d` | **本機目前沒有容器在跑**（`docker compose ps` 為空），所以 scope B 的執行期行為無法本機驗證。套用後 MSI 才會真的去要 x570 的 `qwen3:14b` |
| 同上 | `x570`、`mbp` | `git pull` → `env-sync.sh pull` → `docker compose up -d` | 這兩台的 `.env` 若已有 `OLLAMA_MODELS`，會從「被丟棄」變成「生效」，建議同批套用 |
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
| `F` | M6 ops | `host-sync.sh`（冪等部署）／`host-doctor.sh`（診斷，永不印值）／`host-onboard.sh`（從零到能跑） | `HOST-UPGRADE.md` ＋ `X570-HANDOFF.md` ＝ 601 行散文 runbook，`scripts/` 下 0 個部署腳本，全 repo 沒有任何 `git pull` |
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
| 2026-09-27 | `I` | 跨 M1／M3／M5（**文件限定**） | 補齊剩下 3 個模組的契約 5 題 → **6/6 架構書全部合約**。`settings/env/README.md`（原本 richest 但零契約章節）、`ingest/laws/DESIGN.md`（寫成管線設計、無邊界/不變量/驗收）、`ingest/cases/DESIGN.md`（草案，加契約並明示**未實作**）、`evals/README.md`（原本只有 4 行）。`files/` 三份文件收進 `docs/` 納入版控、刪掉 Windows `:Zone.Identifier` ADS 髒檔、`.gitignore` 補 ADS 規則。**逐項查證並修正 6 處事實錯誤**：`rules.py`→`rules_store.py`、`pytest backend/tests/`→根 `tests/`、PG `laws_keywords`→`article`、~~rerank() 是 stub~~（已實作，真正缺的是接 cross-encoder）、M1/M6 對 `env-sync.sh` 的**雙重所有權**劃給 M1、`__init__.py` 慣例未落實。另修正 M5 README 對 `/eval` 計分邏輯的錯誤描述（原說只算 `expect_case`，實際 19 題裡 `expect_case` 是 0 題、計分的全是 `expect_law`）。**零程式碼**：`.py`/`.env`/`compose.yaml` 全未觸碰，`pytest -q` 165 條不 regression |
