# OpenCode Agent 交接清單

> ⚠️ **本檔是 2026-09-27 的「交接／外傳副本」，權威版是 `ARCHITECTURE.md`〈架構表〉
> ＋ `SCOPE.md`。** 兩份內容重疊但**不是同一個真相**：本檔沒有被 repo 內任何檔案引用，
> 原本放在未追蹤的 `files/`（從 Windows 拖進來，帶 `:Zone.Identifier` ADS 檔）。
> 2026-09-27 收進 `docs/` 納入版控，已修正與 repo 現況不符之處
> （完整清單見 `docs/MODULAR_ARCHITECTURE_2026-09-27.md`〈與 repo 現況的差異〉）。
>
> **不要在這裡新增規則** —— 新規則寫進 `ARCHITECTURE.md`。

**日期**：2026-09-27  
**專案**：ragdemo.win（RAG 檢索增強生成系統）  
**交接方式**：模組化、逐個 scope 推進  

---

## 📋 六大模組概覽

| # | 模組 | 負責人 Agent | 優先級 | 狀態 | 架構書 |
|---|------|--------------|--------|------|--------|
| **M1** | env（環變）| `env-ops` | 🔴 高 | 進行中 | `settings/env/README.md` |
| **M2** | backend（檢索引擎）| `backend` | 🔴 高 | 進行中 | `backend/DESIGN.md` |
| **M3** | ingest（資料攝入）| `ingest` | 🔴 高 | 進行中 | `ingest/laws/DESIGN.md` |
| **M4** | frontend（前端 UI）| `frontend` | 🟡 中 | 進行中 | `frontend/DESIGN.md` |
| **M5** | eval（評測）| `eval` | 🟡 中 | 進行中 | `evals/README.md` |
| **M6** | ops（運維部署）| `ops` | 🟡 中 | 進行中 | `scripts/DESIGN.md` |

---

## 🎯 開發工作流（標準流程）

```
選模組 (SCOPE.md) 
   ↓
讀架構書 (5 點契約)
   ↓
実裝 + 單元測試
   ↓
驗收指令 (✓ 通過)
   ↓
Commit with prefix (msi:/mbp:/x570:)
   ↓
git push (觸發 CI)
   ↓
三機 docker compose up -d (同步)
```

**⚠️ 核心規則**：
- **同時最多一個 scope**（三台共用 repo，半成品會被自動分發）
- **卡住就問，不猜**（猜出來的設定幾天後才發現崩潰）
- **模組邊界嚴格**（不動別人的檔案，避免「第二真相」）

---

## 📌 每份架構書必答 5 題

### 1️⃣ **目標** — 一句話驗收標準
```
例：「環變系統是三台的單一真相，per-host 機密永不進共用層」
```

### 2️⃣ **邊界** — 我**不**負責什麼
```
例（M1）：「不動 compose.yaml、不動 backend 程式碼」
例（M3）：「不改檢索邏輯與 UI」
```

### 3️⃣ **不變量** — 一旦破壞就出事（測試可能抓不到）
```
例：「HOST_ID 絕不能讀錯」「Tailscale IP 必須正確」
例：「快照同步是唯一真相」
```

### 4️⃣ **陷阱** — 實測踩過的坑
```
例：「QDRANT_URLS 在容器內用不到（compose 只傳 QDRANT_URL）」
例：「Gemini 2.5 對新 key 回 404」
```

### 5️⃣ **驗收** — 改完後跑什麼指令算過
```
例：「curl localhost:8000/health ✓」
例：「pytest -q ✓」（測試在 repo 根 `tests/`，不是 `backend/tests/`）
```

---

## 🚨 高優先待辦（卡 UI 驗收）

### M3: 精簡包完成
- **現況**：離線可用資料量遠低於目標
- **需求**：500~1000 筆（demo 可離線用）
- **架構**：x570 全量 → 精選 → `sync-snapshot.sh` 快照到 mbp/msi

### M5: questions.json 擴充
- **現況**：19 題（**14 題計分** `expect_law` ＋ 5 題 `expect_none` ＋ 0 題 `expect_case`）
- **需求**：50+ 題以代表法規覆蓋度（現況只有 3 部法：民法 5／刑法 5／勞基法 4）
- **驗收**：`curl -X POST /eval` hit_rate ≥ 0.8
- ⚠️ 現況 hit_rate=1.0（14/14）是 **n=14**，統計上不足以宣稱穩定達標
  （單邊 95% 下界 ≈ 0.79）。見 `evals/README.md` §6。

### M2: 雲端路由完整驗證
- **已實作**：8 個 provider 群組 + CF AI Gateway + 直連
- **待驗**：全 provider 實測通過

### ~~M3: rerank() 實作~~ → **已過期，2026-09-27 刪除**
- 原寫「rerank() 是 stub，待接真正實作」。**查證後為誤導**：
  `rerank()`（`backend/app/rag.py:1044-1057`）**已實作**且是真正的本地重排
  （條號精準分支領先 ＋ dense 語意相似度降序，sparse 噪音自然沉底）。
- **真正未做**的是接上 cross-encoder 模型：`RERANK_MODEL`（`rag.py:42`）
  已宣告但**全 backend 只出現在它自己那一行定義、沒有任何消費者**。
  歸 M2／M3 交界，記入 `SCOPE.md` 佇列代號 `H`。
- 連帶提醒：`RERANK_MODEL` 目前被 `scripts/env-sync.sh` 當 `SHARED_CONFIG` 分發，
  被 `env-audit.py` 當「程式有讀」豁免 —— **「能被分發」不等於「在生效」**。

---

## 🔧 三機狀況速查

| 機器 | IP | 角色 | LLM | 容器化 | 關鍵配置 |
|------|-----|------|-----|--------|---------|
| **x570** | 100.119.83.111 | 主力（資料源） | qwen3:14b | ✅ | 每日 ingest / qdrant key / pg 心跳 |
| **mbp** | 100.64.121.9 | 加速 | qwen3:14b | ✅ | 快照同步 / launchd tunnel |
| **msi** | 100.65.68.106 | demo | qwen3:8b | ✅ | WSL2 + Docker Desktop / 快照同步 |

**工作原理**：
1. x570 是資料唯一來源（LLM 邏輯 + qdrant）
2. `sync-snapshot.sh` 每 10 分鐘 x570 → mbp/msi 同步快照
3. x570 離線時，mbp/msi 用本機快照獨立作業（檢索能力一致）

---

## 📝 Commit 提交規則

### ✅ 正確格式
```bash
git commit -m "msi: backend rag.py 修復 gemini 2.5 key 判斷"
git commit -m "x570: env-ops 補齊 POSTGRES_PEER_PASSWORD"
git commit -m "mbp: ingest sync_daily.py 版本取值修正"
```

### ❌ 會被擋住
```bash
git commit -m "fixed bug"                      # ✗ 缺前綴（.githooks/commit-msg 擋）
git commit -m "backend: add LAN_IP to .env"   # ✗ 帶 LAN_IP（pre-push hook 擋）
git commit -m "refactor 500 lines"            # ✗ 超過 10MB（CI 只回報）
```

### 首次設定（每機一次）
```bash
git config core.hooksPath .githooks
```

---

## 🐳 Docker 快速操作

```bash
# 首次或改 api 碼
docker compose up -d --build

# 只改環境變數或佈置文件
docker compose up -d

# 查看狀態
docker compose ps
docker compose logs -f api

# 進容器除錯
docker compose exec api bash
```

---

## 🔄 環境變數檢查

```bash
# 掃描幽靈變數 + 消費者確認
python3 scripts/env-audit.py

# 驗 schema 完整性（per-host 必填等）
scripts/env-sync.sh --check

# 預覽本機展開（不實際修改 .env）
scripts/env-sync.sh render
```

---

## 💾 快照同步操作

```bash
# 手動同步（用於外出 demo 補最新版本）
bash scripts/sync-snapshot.sh

# 檢查日誌
tail -f ~/qdrant/sync.log

# 查看同步狀態（點數、快照名）
cat ~/qdrant/.sync-state
```

---

## 🚫 千萬不能做

| 禁止事項 | 理由 | 後果 |
|---------|------|------|
| 寫 LAN_IP 到 .env | registry 會濾掉 | 三台互找不到，無人發現 |
| Per-host 機密進 sops | 兩台會拿錯密碼 | 某台 401，只能猜 |
| 改別人模組的檔案 | 跨模組污染 | 責任不清，難排查 |
| 猜環境變數值 | 三台自動分發 | 幾天後該機器莫名崩潰 |
| 同時開多個 scope | 半成品版本化 | 另外兩台自動拉進來 |

---

## 📚 文件索引

### 必讀文件
- **`ARCHITECTURE.md`**（完整架構 + 歷史決策）
- **`SCOPE.md`**（當前 scope 追蹤）
- **`HOST-UPGRADE.md`**（x570/mbp 升級前必讀）

### 模組架構書
- `settings/env/README.md`（M1）
- `backend/DESIGN.md`（M2）
- `ingest/laws/DESIGN.md`（M3）
- `frontend/DESIGN.md`（M4）
- `evals/README.md`（M5）
- `scripts/DESIGN.md`（M6）

### 待確認清單
- **`X570-HANDOFF.md`**（x570 還有 4 項未做）
- `ROADMAP.md`（長期規劃：跨機遷移等）

---

## 🆘 不知道怎麼辦？（三段升級路徑）

### 第一段：讀架構書
進入該模組的 `DESIGN.md`，按 5 題逐點對照。

### 第二段：讀本檔 + SCOPE.md
本檔對應章節 + `SCOPE.md` 當前 scope 的驗收條款。

### 第三段：停下來問
**不要猜、不要頂替。** 三台機器會自動分發錯誤設定，症狀最貴時是幾天後才發現。

---

## 📊 驗收 checklist（範例）

### M2 後端改完後
```
□ curl localhost:8000/health          # host_id 正確
□ 前端 /query 測試                    # confidence/trace 欄位完整
□ pytest -q 全過（165 條）             # 無 regression
□ 8 個 provider 都測過                 # gemini/mistral/zen/nv 等
□ commit 帶 msi: 前綴                 # git log 一眼可讀
□ docker compose up -d 三台成功        # 容器化無誤
□ SCOPE.md 標記完成                   # 進度可見
```

### M3 ingest 改完後
```
□ Qdrant laws collection 點數檢查      # 數字對上 x570
□ data/laws/.law_version 有版本號     # 更新日期正確
□ PG article 表行數 > 100             # 切分成功
□ sync-snapshot.sh 一輪成功           # 快照到達各機
□ commit 帶 msi: 前綴                 # git log 清楚
□ SCOPE.md 標記完成                   # 進度可見
```

---

## 🎬 快速開始（首次 agent 入場）

1. **選一個高優先模組**（M1～M3 任選一個，看 SCOPE.md 目前誰沒人做）
2. **讀對應的架構書** + 本檔「高優先待辦」段
3. **跑驗收指令** 確保環境 OK
4. **在 SCOPE.md 登錄** `[M#] 模組名 簡述 | agent_name | 預計日期`
5. **開發 → 測試 → Commit with prefix → git push**
6. **三機跑 `docker compose up -d` 驗證**
7. **SCOPE.md 標記完成**

---

## 📞 聯絡對象

- **env 問題**：看 `settings/env/README.md`，掃 `python3 scripts/env-audit.py`
- **後端 bug**：`backend/DESIGN.md` + 跑 `pytest`
- **資料進不去**：`ingest/laws/DESIGN.md` + 檢查 Qdrant 點數
- **前端連不上**：`frontend/DESIGN.md` + 檢查 worker 轉發
- **三台同步失敗**：`ARCHITECTURE.md` 備援機制 + `sync-snapshot.sh` 日誌
- **超困**：停下來問使用者（不要猜）

---

## 最後提醒

> 三台機器共用同一份 repo。你改的每一行，都會被另外兩台的 `env-sync pull` 自動分發。
> 
> **猜 ≠ 驗。驗 ≠ 通過。通過 ≠ 一周後還好。**
> 
> 所以：**寧可卡住。** 卡著問，比半夜該台機器莫名其妙崩潰好多了。

---

**文件版本**：2026-09-27  
**適用範圍**：opencode agent + 三機開發團隊  
**下次更新**：2026-10-04（定期檢視 SCOPE.md 進度）
