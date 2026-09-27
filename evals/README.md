# eval 模組架構書（M5）

> **擁有者：`eval` agent。** 本檔骨架由 `ARCHITECTURE.md`〈架構表〉建立，
> 內容由 owner 逐題補齊 —— 補不完的題目代表該處有沒被記錄下來的知識，
> 應該在下次動到時補上，而不是先留空。

## 1. 目標

用**可重現的引註命中率**回答一個問題：這個 RAG 到底會不會給對法條。
做到什麼算完成：`POST /eval` 回 `hit_rate ≥ 0.8`，且 `questions.json` 的**計分題**
≥ 50 題（`ARCHITECTURE.md`〈M5 門檻〉、`files/modules-matrix.json` 的
`acceptance_threshold.question_count_min`）。

不變的是**計分題**要 ≥ 50；總題數可以更多（負面題不計入 hit_rate，見 §6）。

## 2. 邊界（不負責）

- **不改任何程式碼**（`ARCHITECTURE.md`〈架構表〉M5 邊界欄＝「只讀不寫碼」）。
  發現引擎有 bug → 回報給 `backend`／`ingest` agent，附上重現的題目與 payload，
  不要自己去改 `rag.py`。
- 不動 `backend/app/`、`ingest/`、`frontend/`
- 不調門檻。`hit_rate ≥ 0.8` 是**產品決策**，不是本模組可以自己放寬的參數。
  題目太難導致不過 → 加題或回報「該修切分」（`ingest`），不是把門檻改低。
- **本模組擁有 `evals/questions.json` 這份資料**（加題、改 `expect_*` 欄位），
  「不寫碼」指的是不寫執行程式碼 —— 否則「擴到 50 題」這個待辦無人可做。

## 3. 不變量

- **壞了測試不一定抓得到的三條**：
  - `hit_rate` 與 `neg_rate` 必須**同時**看。只看 `hit_rate` 會漏掉
    「引擎都回 `no_match`」這個退化 —— 那種情況 `neg_rate=1.0` 很好看，
    但 `hit_rate` 會是 0，兩者互為護欄。
  - 每題的 `expect_law` 必須是**該題真正該引的那條**，不能填「同法其他條」。
    比對是子字串比對（§7），填寬了會讓 `hit_rate` 虛高。
  - 題庫要能代表**法規覆蓋度**，不是集中在單一法。現況 14 題裡 6 題民法、
    6 題刑法、7 題勞動基準法（§6），擴題時要補其他法（行政／刑事訴訟／民法親編…）。
- `expect_law` 對應 `laws` collection，`expect_case` 對應 `cases` collection。
  `cases` 尚未實作（`ingest/cases/DESIGN.md` 是草案），所以現階段
  **`expect_case` 一題都不能有** —— 有也只會被跳過，不會算分（§7）。
- 題庫是**被追蹤**檔（`evals/questions.json` 在 git 內），三台共用同一份，
  所以改題等於三台同時換基準；改完要 commit（帶 `msi:`／`mbp:`／`x570:` 前綴）。

## 4. 陷阱（實測踩過）

- ⚠️ **本檔舊版開頭寫錯了，已於 2026-09-27 更正**：原文說「`POST /eval` 只計算
  有填 `expect_case` 的題目命中率」。**實際相反** —— 現有 19 題裡
  `expect_case` 是 **0 題**，`expect_law` 才是 14 題。照原文理解會得出
  「hit_rate 分子分母都是 0」的荒謬結論。計分邏輯以 `backend/app/main.py`
  與 §7 為準。
- **`neg_rate = 1.0` 不代表引擎健康**，它只證明 5 個無關問題（天氣、煮咖哩、
  訂機票…）都沒亂答。這 5 題擋的是**幻覺**，不是擋**檢索力**。
- **子字串比對有假陽性空間**：`expect_law` 是拿 `expect_law in f"{law_name} {article_no}"`
  比的，沒有正規化。所以 `"勞動基準法 第 22 條"` 對上 payload
  `law_name="勞動基準法" article_no="22"` 就過；但若兩題的 `expect_law`
  寫成其中一個是另一個的子字串（例：`"民法 第 12 條"` vs `"民法 第 120 條"`
  不會互含，但 `"刑法 第 20 條"` vs `"刑法 第 200 條"` 的**空白差異**要小心），
  就要靠 `recall=50` 的 top_k 裡是否剛好有那一條。**寧可把 `expect_law`
  寫完整（含法名＋完整條號），不要只寫條號。**
- **擴題時最容易造出假高的 `hit_rate`**：從既有的 14 題裡抄改問法，
  等於同一個預期答案被問兩次。加新法條時要**先自己讀法條確認**，
  不要從題庫反推。
- `/eval` 每次都 `recall=50, top_k=5` 真的打一次 `rag.answer()`（含 LLM），
  19 題就是 19 次完整檢索。**不是離線純比對**，跑一次要等實際延遲。
- `RERANK_MODEL`（`rag.py:42`）**是宣告了但沒接線的旋鈕** —— 全 backend 只出現
  在那一行定義，`rerank()` 沒有用它。見 §7 末。

## 5. 驗收

```bash
# 題庫結構自檢（不需要起服務、不需要 LLM）
python3 -c "import json;d=json.load(open('evals/questions.json'));\
print('總數',len(d));\
print('expect_law',sum(1 for q in d if q.get('expect_law')));\
print('expect_case',sum(1 for q in d if q.get('expect_case')));\
print('expect_none',sum(1 for q in d if q.get('expect_none')))"

# 完整評測（需服務在跑，會實際呼叫 LLM）
curl -s -X POST http://localhost:8000/eval | python3 -m json.tool

pytest -q tests/          # 165 條，動到 backend 時不得 regression
```

判讀：`hit_rate ≥ 0.8`（`ARCHITECTURE.md`〈M5 門檻〉）**且** `neg_rate` 沒有異常下跌。
`tested` 或 `neg_tested` 為 0 → 題庫欄位名寫錯了，先對照 §7 再談門檻。

## 6. 題庫現況（2026-09-27 實測，非推測）

| 類型 | 欄位 | 題數 | 計入 |
|---|---|---|---|
| 法條命中題 | `expect_law` | 14 | `hit_rate` |
| 負面題（不該答） | `expect_none: true` | 5 | `neg_rate` |
| 判決命中題 | `expect_case` | **0** | `hit_rate`（等 `cases` 實作後才有意義） |
| **總計** | | **19** | |

- 14 題的法別分布（`python3 -c` 實測，非目測）：民法 **5**、中華民國刑法 **5**
  （含 1 題 `185-4` 這種帶連字符的條號）、勞動基準法 **4**。
  **只有 3 部法**，這是擴到 50 題時要補的第一個缺口。
- 最近一次量測：`hit_rate = 1.0`（14/14）、`neg_rate = 1.0`（5/5），
  2026-09-24，`ingest/laws/DESIGN.md` §11。
- ⚠️ **14/14 這個數字不要外傳成「引擎很準」** —— n=14，而且是**同一個切分策略
  調出來的**同一批資料。0 失敗／n=14 時，單邊 95% 成功率下界（rule of three）
  只有 `1−3/14 ≈ 0.79`；雙邊 95% 精確區間（Clopper–Pearson）下界更只有 `≈ 0.23`。
  換句話說 **14/14 幾乎沒有證明「穩定 ≥ 0.8」**。這是「不能只靠它進 UI 階段」的
  理由，不是「已經夠了」。

## 7. `/eval` 怎麼算分（讀 `backend/app/main.py` 得出，勿憑記憶）

`POST /eval`（`main.py:234-274`）對每題依序：

1. 有 `expect_none` → 走**負面分支**：呼叫 `rag.answer()`，
   結果有 `no_match` 才算 `neg_hit`（`neg_tested`/`neg_hit`/`neg_rate`）
2. `expect_law` 或 `expect_case` 皆無 → **`continue` 跳過，不算分**
   （這是「佔位題」的機制，題庫裡若留白欄位不會被算成失敗）
3. 否則計入 `tested`，用 `recall=50, top_k=5` 真的檢索一次，
   遍歷 `res["hits"]`：
   - `expect_law` 比 `payload` 的 `f"{law_name} {article_no}"` 子字串
   - `expect_case` 比 `payload` 的 `case_no` 子字串
   - 任一命中即 `hit += 1`

回傳欄位：`tested` / `hit` / `hit_rate` / `neg_tested` / `neg_hit` / `neg_rate`。
`tested == 0` 時 `hit_rate` 回 `0.0`（不是 NaN、不報錯）。

題庫檔路徑（`main.py:238-241`）：先找容器內 `/app/evals/questions.json`，
不存在才回退 repo 根的 `evals/questions.json`。
→ **改題庫要改 repo 根那份**；容器那份是 bind mount 進去的同一個檔。

### 附帶更正：`rerank()` 不是 stub

`files/` 兩份交接文件把「`M3 rerank() 實作（目前 stub）」列為高優先待辦，
並說「reranker 還是 stub（`rag.py rerank()` 待接真正實作）」。
**2026-09-27 查證：這是過期資訊。**

- `rerank()`（`rag.py:1044-1057`）**已實作且是真正的重排**：條號精準分支
  （`_exact_rank`）領先，其餘依「真實 dense 語意相似度」降序，
  sparse 僅主導的噪音自然沉底。
- 真正沒做的是**接上 cross-encoder 模型**：常數 `RERANK_MODEL`
  （`rag.py:42`，預設 `qllama/bge-reranker-v2-m3:latest`）**全 backend 只被讀取
  這一次，就是它自己的定義行**，之後沒有任何地方使用它。
  換句話說 rerank 是「本地重排」，不是「模型重排」。
- 要接線屬於 `backend`（M2）＋ `ingest`（M3）的範圍，不是 M5。
  已記在 `SCOPE.md` 佇列代號 `H`（`EMBED_MODEL`／`RERANK_MODEL` 可設定化）。
- ⚠️ 連帶注意：`RERANK_MODEL` 目前被 `scripts/env-sync.sh` 當成
  `SHARED_CONFIG` 分發、被 `env-audit.py` 當成「程式有讀」豁免
  （`tests/test_env_audit.py:120` 鎖住這個行為）。
  它其實**沒有消費者**。在真的接上線之前，別把「它能被分發」誤讀成
  「它在生效」—— 這個落差是 M1 與 M2 的問題，記在此處供對照。

## 8. 門檻（`ARCHITECTURE.md` 定案）

| 指標 | 門檻 | 現況 |
|---|---|---|
| `hit_rate` | ≥ 0.8 | 1.0（14/14，n=14 見 §6 警告） |
| 計分題數 | ≥ 50 | 14 |
| `neg_rate` | 無門檻，但**異常下跌要查** | 1.0（5/5） |

`hit_rate < 0.8` → **不准進 UI 階段**（`ARCHITECTURE.md`〈M5 目標〉）。
常見根因順序：切分（`ingest`）→ 召回（`rag.py` hybrid／條號分支）→ 重排（`backend`）。

## 待補（owner）

- 題庫補到 50 題，**優先補別的法**（行政法、刑事訴訟、民法親編／物權／債編各章），
  不要在既有 3 法內加變體
- 每題加 `note` 欄位記「這題在測什麼」（條號召回？法名召回？跨章推理？），
  這樣 hit_rate 掉下來時才知道是哪一類退化
- 加一組「**同法不同條**」的對抗題（例如問 259 條、預期不能只回 260 條），
  目前 14 題沒有任何一題能區分「真的檢索到」與「撈到同一章」
- `cases` 上線後把 `expect_case` 題目加進來（先 0 → 有）
- 決定 `neg_rate` 要不要也設門檻（目前只報不設）
