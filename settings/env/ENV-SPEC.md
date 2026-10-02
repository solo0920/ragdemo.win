# 三機 `.env` 標準規格（2026-10-02 定案）

由 `scripts/env-diff-hosts.py` 比對三台回報的欄位產生。**不含任何值** ——
三台的回報本來就只有 `SET`／`EMPTY`／`ABSENT`。

## 這份規格是什麼、不是什麼

**是**：三台的 `.env` 應該有**相同的鍵集合與排列**。值可以不同（那是本機的事）。

**不是**：三台的 `.env` 長得一模一樣。`HOST_NAME` 三台必然不同值 ——
那正是 per-host 欄位存在的理由。

⚠️ **驗收只有一個數字**：`bash scripts/env-sync.sh --check | head -1` 的版面指紋，
三台必須相同。指紋只涵蓋**鍵的序列**，不涵蓋值（值用 `--fingerprints` 比）。

## 為什麼「提案」不由現況決定

若照抄現況，某台共用憑證是 `ABSENT` 時提案就會寫「ABSENT」—— 等於**把故障
認可成規格**。所以每個鍵的目標狀態由它的**角色**決定：共用憑證一律 SET，
不管現在誰缺。`env-diff-hosts.py` 有測試守著這點。

---

## 一、必須立刻知道的四件事（不是格式問題；C、D 已解決）

### A. `OLLAMA_URLS` 在 mbp 是**沒人管**的值 ⚠️

| 機台 | `.env` | 總表 `hosts.shared.env` 的對應列 |
|---|---|---|
| x570 | ABSENT | **空** |
| mbp | **SET** | **空** ← |
| wsl | SET | 有值 |

`render` 只會寫入總表裡**該機有值**的列。mbp 的總表列是空的，所以
**mbp 那個 `OLLAMA_URLS` 不是 render 來的** —— 要嘛是手改的，要嘛是
render 之後總表被清掉留下的殘留。

`env-sync.sh --check` 應該會報它不一致。**mbp 請先跑 `--check` 確認** ——
若是紅的，那就是真實漂移，處分方式（保留並補總表列／刪掉 `.env` 那行）
取決於 mbp 到底要不要用本機 ollama。

### B. `POSTGRES_DSN` 在 x570 走的是**另一條路** ⚠️ → **已解決（2026-10-02 19:40）：是假警報，我說反了**

**原本的說法（錯的）**：x570 的 `.env` 沒有 `POSTGRES_DSN` → 它吃的是
compose 預設；mbp／wsl 走的是總表 → **兩種機制、兩份可能漂移的設定**，
「改了 `POSTGRES_PASSWORD` 而忘了改總表的 DSN，就會有台連不上」。

**x570 的回報推翻了它（對的）**，而實測也支持它：

```
總表（被追蹤檔）：
  x570_POSTGRES_DSN=                                                   ← 空
  mbp_POSTGRES_DSN=postgresql://rag:${POSTGRES_PASSWORD}@postgres:5432/ragdemo
  wsl_POSTGRES_DSN=postgresql://rag:${POSTGRES_PASSWORD}@postgres:5432/ragdemo

compose 預設：${POSTGRES_DSN:-postgresql://rag:${POSTGRES_PASSWORD:?…}@postgres:5432/ragdemo}
```

**兩邊都是 `${POSTGRES_PASSWORD}` 佔位符。** `render --expand` 在每次 render
時用該機自己的 `POSTGRES_PASSWORD` 展開，compose 預設則在每次啟動時插值 ——
**兩種來源產生完全相同的結果，而且都沒有靜態憑證**。

實測（wsl，只比 sha12）：

```
postgres 的 POSTGRES_PASSWORD  sha12 = e328bd31728a
api 的 DSN 內嵌密碼            sha12 = e328bd31728a   ★相同
api → postgres:5432            TCP 可達 ✓
```

**風險方向是反的，而且沒有風險**：三台的 DSN 都**每次從
`POSTGRES_PASSWORD` 重新推導**，所以不存在「總表裡的靜態密碼過期」這件事。
被追蹤的總表裡也**沒有任何明文密碼**（那三列都是佔位符或空值）。

⚠️ **我為什麼會搞錯**：我從「`.env` 裡有沒有那一行」去推斷行為，沒有去看
那一行的**值是什麼**。`${POSTGRES_PASSWORD}` 與一個靜態密碼在畫面上只差
`${}` 三個字元，兩種機制的行為卻完全一樣 —— 而我看到「兩種機制」就下了
「兩份可能漂移的設定」的結論。

**這是本專案反覆出現的同一類錯誤**：從**檔案的形狀**推論**行為**，
而正確做法是去看那個值怎麼被解析。`env-sync.sh --check` 的值檢查、
`env-audit.py` 的讀取點，都是為了讓「值怎麼被解析」變成可查的東西。

### C. `TS_IP` 只有 wsl 缺 → **已解決（2026-10-02 18:50）**

`TS_IP` 是 `LOCAL_ONLY`（刻意不在總表裡，三台各自填自己的 tailscale IP）。
mbp SET、x570 SET、wsl 原本 EMPTY。

**wsl 已填。** 原因：wsl 當初留空是因為它是 msi 的 WSL、只能用 `127.0.0.1`；
現在 **wsl 本身已是 tailscale node**（`wsl.tailfe3f3d.ts.net`），所以有自己的
tailscale IP 可填。

實測：`postgres` 從綁 `127.0.0.1:5432` 改成綁 `100.122.78.7:5432`，
且該位址 TCP 可達 ✓。**這是刻意的行為變更** —— 從此 mbp／x570 可以連進
wsl 的 postgres。

### D. 只有 wsl 有 ollama → **已解決（2026-10-02 18:50），但只刪一半**

`OLLAMA_MODELS=qwen2.5-coder:latest` 那個「因為 msi 的 ollama 只跑得動
qwen2.5-coder 所以標註預設用它」的註記**已刪**，總表的 `*_OLLAMA_MODELS`
三列也一併移除。

**刪除是行為中立的，已實測**：`gateway.py:79` 是
`OLLAMA_MODELS = os.getenv("OLLAMA_MODELS", LLM_MODEL)`，而本機
`LLM_MODEL` 恰好就是 `qwen2.5-coder:latest` → fallback 給出**同一個模型**。

⚠️ **`OLLAMA`（位址，不是模型）保留。** 實測：

```
http://127.0.0.1:11434            → HTTP 000（WSL 裡連不上）
http://msi.tailfe3f3d.ts.net:11434 → HTTP 200
```

`qdrant_load.py:29` 是 `os.getenv("OLLAMA") or "http://127.0.0.1:11434"` ——
刪掉 `OLLAMA` 會讓 wsl 的 ingest 嵌入**全斷**，因為 WSL 裡的 localhost
到不了 msi 的 ollama。

⚠️ **而且 per-host 鍵只要有一台有值，三台的鍵集合就不同 → 指紋永遠對不上。**
所以規格要求：**`OLLAMA` 這一行三台都要有**（wsl 有值，mbp／x570 是空行）。
那不是冗余，是規格的一部分。prune 不得刪那個空行（見 §三）。

### E. `OLLAMA_MODELS` 現在由誰決定

已不是 per-host 鍵 → 變回一般鍵，compose 預設是空字串 → `env-prune.py`
的「值＝預設」規則會自動刪掉空行。**不需要把它寫進任何手寫清單。**


`env-sync.sh --check` 報：

```
⚠️ 1 個 per-host 鍵在總表的 x570 列是空的，該機沿用自己的值…: HOST_MACHINE_ID
```

`registry.py:65` 的取得順序是 `HOST_MACHINE_ID` 環境變數 → 檔案
`/run/secrets/host-machine-id`（**容器裡沒有那個 mount**）→ `/etc/machine-id`
→ 都拿不到才退化成 MAC 並告警。

⚠️ **`/etc/machine-id` 是弱識別，而且容器每次重建都會變。** 若 x570 落到
那一層，它在 registry 的識別會**隨容器重建而改變** —— 而 registry 是三機互相
發現彼此的依據。症狀是「某台的 registry 條目無故換掉」。

**處置**：在總表補上 `x570_HOST_MACHINE_ID=` 的值（該機自己的 machine-id
雜湊）。那不是憑證（mbp／wsl 的值本來就在被追蹤的總表裡），所以由 x570 自己
填那一行即可。



---

### F. x570 的 `HOST_MACHINE_ID` 沒人管（新，2026-10-02 19:40）

`env-sync.sh --check` 報：

```
⚠️ 1 個 per-host 鍵在總表的 x570 列是空的，該機沿用自己的值…: HOST_MACHINE_ID
```

`registry.py:65` 的取得順序是 `HOST_MACHINE_ID` 環境變數 → 檔案
`/run/secrets/host-machine-id`（**容器裡沒有那個 mount**）→ `/etc/machine-id`
→ 都拿不到才退化成 MAC 並告警。

⚠️ **`/etc/machine-id` 是弱識別，而且容器每次重建都會變。** 若 x570 落到
那一層，它在 registry 的識別會**隨容器重建而改變** —— 而 registry 是三機互相
發現彼此的依據。症狀是「某台的 registry 條目無故換掉」。

**處置**：在總表補上 `x570_HOST_MACHINE_ID=` 的值（該機自己的 machine-id
雜湊）。那不是憑證（mbp／wsl 的值本來就在被追蹤的總表裡），所以由 x570 自己
填那一行即可。

## 二、機械性的統一（15 鍵，零行為變更，已在本機驗證）

### 2a. 值與 compose 預設逐位元組相同 → 三台都刪（5 鍵）

`COLLECTION`、`JEV_BANK_MIN`、`JEV_VERIFY_MIN`、`PG_CONNECT_TIMEOUT`、`ZEN_BASE_URL`

這 5 個的值**與 compose 預設完全一樣**。刪掉之後 compose 給的值一模一樣。

> **為什麼這值得做**：留著它們的真正代價不是多一行，是**多一份可能過期的副本**。
> 哪天 compose 的預設改了，`.env` 裡那個舊值會**靜默地贏** —— 而沒有任何人
> 會注意到，因為「我明明在 `.env` 設過」。

**已在本機（wsl）實測**：刪除後容器實際拿到的值 sha12 與刪除前**完全一致**
（`COLLECTION`／`JEV_*`／`PG_CONNECT_TIMEOUT`／`ZEN_BASE_URL` 五個逐一比對）。

新增的規則是**自動偵測**（`env-prune.py` 比對值是否等於 compose 預設），
不是寫死 5 個鍵名 —— 寫死只是把「會漂移的手寫清單」換個位置放。

### 2b. 沒人設值的鍵 → 三台都沒有這行（12 鍵）

`ACCESS_HOSTS`、`JEV_DISABLED`、`JEV_MODEL`、`KEEP_ALIVE`、`LIMIT`、`PICK_TTL`、
`PROBE_TIMEOUT`、`RAG_HIGH_DENSE`、`RAG_MID_DENSE`、`RAG_MIN_DENSE`、
`REGISTRY_HEARTBEAT`、`REGISTRY_STALE_MIN`

原本 wsl 是 `EMPTY`、另兩台是 `ABSENT` —— **同一件事的兩種寫法**。
統一成 `ABSENT`。空行與沒有這行的行為完全相同（都是吃預設值／未設定）。

---

## 三、prune 的兩條自動規則（不寫死任何鍵名）

`scripts/env-prune.py` 會刪行的規則**全部自動判斷**，沒有手寫鍵名 ——
手寫清單就是「會漂移」的東西。

| 規則 | 判準 | 為什麼 |
|---|---|---|
| **值＝compose 預設 → 刪** | 逐位元組相等 | 留著的代價不是多一行，是**多一份可能過期的副本**：哪天預設改了，`.env` 裡那個舊值會**靜默地贏** |
| **per-host 鍵的空行 → 保留** | 鍵在 `per_host_keys()` 裡 | per-host 鍵的**存在**由規格／總表決定，prune 無權判斷 |

⚠️ **第二條是 2026-10-02 修的 bug，別還原。** 原版只擋 `PER_HOST_SECRETS`，
於是新規則會刪掉 `OLLAMA` 的空行。後果有兩個：

1. wsl 的 ingest 拿不到 ollama 位址 → **嵌入全斷**
2. 三台鍵集合不同 → **版面指紋永遠對不上** → 規格無法驗收

守衛在 `tests/test_env_prune_rules.py::test_empty_per_host_lines_are_kept`
與 `::test_per_host_keys_survive_the_cli_path`（後者特別重要 —— 單元測試只測
`in_place`，而 bug 恰好是「修好 `in_place` 卻忘了 `_registry()` 仍回傳
`PER_HOST_SECRETS`」的形狀，**兩處都要對才會生效**）。

---

## 四、本機已完成的變更（wsl，2026-10-02）


```
.env        422 → 337 → 329 → 328 行
有值鍵      27 → 22
TS_IP       （空）→ 100.122.78.7   ← wsl 自成 tailscale node
OLLAMA_MODELS  已刪（行為中立，見 §一 D）
OLLAMA        保留（刪了 ingest 嵌入全斷，見 §一 D）
registry    71 → 70 個變數（去掉幽靈 VAR，見下）
版面指紋    71 keys c2accd53873f
            42 keys ada1db689ed3
            34 keys 00a9eae29228
            27 keys eab0ab8f9d4e   ← 現況，三台的目標
```

* **幽靈變數 `VAR` 已移除。** 它的唯一「讀取點」是 `env-sync.sh` 裡
  `python3 - "$TABLE" <<'PY'` heredoc 內的 `print("…寫 ${VAR} 佔位…")` ——
  分隔符**帶引號**所以不展開，那是純粹的文件字串。`scan_shell()` 只認
  「行首不是 `#`」，於是把它記成一個變數。整份清單的存在理由是「從程式碼
  反查」，一個幽靈會讓它的可信度打折。

---

## 五、各台執行的步驟

```bash
git pull

# 1. 重新產生範本（registry 變了：VAR 消失）
python3 scripts/env-audit.py --template > .env.example

# 2. 版面遷移（若這台已經遷移過，這步會是 no-op）
python3 scripts/env-relayout.py --dry-run     # 先看數字
python3 scripts/env-relayout.py

# 3. prune：會自動刪掉「值＝compose 預設」與「空值＋有預設值」
python3 scripts/env-prune.py --dry-run
python3 scripts/env-prune.py

# 4. 同步共用憑證
bash scripts/env-sync.sh pull && bash scripts/env-sync.sh render

# 5. 驗收 —— 這是唯一的驗收
bash scripts/env-sync.sh --check | head -1     # 指紋必須 = eab0ab8f9d4e
```

⚠️ **第 5 步是指紋不同就規格沒達成。** 若不同，把該台的差異回報上來 ——
多半是某個鍵在該台有值而這台沒有，那是需要決定的差異，不是 bug。

## 六、指紋 bug：三台的指紋**本來就永遠不可能相同**（2026-10-02 19:20 解決）

這是整個三機規格卡了兩天的**最後一個**原因，而且它讓前面每一次「指紋不同」
的結論都不可信。

### 症狀

三台都遷移完版之後：

```
wsl  27 keys, layout sha12=cce81000280f
mbp  27 keys, layout sha12=8afdb3d155b6
x570 27 keys, layout sha12=030dbad4334d
```

**鍵數一模一樣**，而且 mbp 的 `8afdb3d155b6` 與 wsl 的鍵集合**完全相同**
（逐鍵比對過）。只有 12 個字元不同。

### 根因

指紋的計算式把**區段標題**也算進去，而其中一行是

```
# ══ HOST: wsl ══
```

`render` 會把本機的 `HOST_ID` 填進那個標題 —— 所以三台的這一行**本來就該
不同**，而指紋把它原樣算進哈希。

**於是「三台的結構必須相同」這句話不可能成立**，而它正是這個指紋唯一的用途、
也是我派給另外兩台的驗收標準。也就是說：這個專案裡沒有任何一道檢查**曾經**
能證明三機同規格。

### 修法

把主機名從那個標題裡正規化掉再算哈希：

```python
seq.append(re.sub(r"^# ══ HOST: .*? ══", "# ══ HOST: <id> ══", line.rstrip()))
```

要驗的是「HOST 區塊**有沒有**、**在哪個位置**」，不是「這台叫什麼」——
後者是三台**本來就該不同**的東西，而那是 `HOST_ID=` 的**值**比較該管的事
（`--fingerprints`），不是版面。

**新指紋（三台的目標）：`27 keys, layout sha12=eab0ab8f9d4e`**

守衛在 `tests/test_env_sync_heredoc.py::test_layout_fingerprint_is_invariant_to_the_host_id`
—— 用三份只差 `HOST_ID` 的 `.env` 驗證指紋相同。沒有它，正規化那行會在某次
「簡化」時被刪掉，而症狀是**所有驗收都失敗但沒有人知道為什麼**。

---

## 七、還在等回覆的兩件事

| # | 問題 | 誰能答 | 在哪一節 |
|---|---|---|---|
| 1 | mbp 的 `OLLAMA_URLS` 要保留嗎（要保留就補總表的 `mbp_OLLAMA_URLS` 列）| mbp | §一 A |
| 2 | `POSTGRES_DSN` 要不要三台統一走總表（現況是 x570 走 compose 預設）| x570 | §一 B |

§一 C（`TS_IP`）與 §一 D（ollama）已由使用者於 2026-10-02 18:50 拍板並執行。
