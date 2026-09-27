# settings/env — 三機共用憑證與 per-host 值的分層與分發

## 為什麼有這層

兩個問題，兩種病：

1. **共用憑證會漂移**：`QDRANT_API_KEY` 等 9 把必須三台一致，過去靠人工複製，
   2026-09-26 已造成兩次不對稱故障（本機 200、遠端 401；registry 心跳失敗）。
2. **per-host 值會寫錯地方**：`HOST_ID`、`TS_IP`、`LLM_MODEL`（msi 是 8b）、
   `OLLAMA_URLS`、`POSTGRES_DSN` 每台不同，整份 `.env` 同步會直接寫壞機器。

## 分層

| 檔案 | 追蹤 | 內容 |
|---|---|---|
| `common.env` | 是（明文） | 共用**非敏感**鍵。值空＝用 compose 預設 |
| `hosts.shared.env` | 是（明文） | **per-host 值的唯一真相**：`<機台>_<鍵>=<值>`，三台同檔 |
| `secrets.common.env.example` | 是（明文） | 9 把共用憑證的鍵名，值一律空 |
| `secrets.common.enc.env` | 是（**加密**） | 9 把共用憑證真值，sops+age 加密 |
| `.sops.yaml`（repo 根） | 是 | age recipient 公鑰清單 |
| `.env`（repo 根） | 否（600） | 執行期唯一真相，**不帶前綴** |

合併順序（低 → 高）：`common.env` → 解密後的 secrets → render 出的 per-host 值。
本機 `.env` 裡不在任何分層的鍵（例如 `HOST_ID`）永遠保留。

## 為什麼前綴不在 `.env` 裡

`docker compose` 只認 `${VAR}` 插值，沒有「依 `HOST_ID` 動態選 `msi_`／`x570_`」
的能力。所以若把 `.env` 裡的 `LLM_MODEL` 改名成 `msi_LLM_MODEL`：

* compose 插到空值 → 回退 `rag.py` 的原始碼預設 `qwen3:14b`，MSI 的 8b 設定
  **被靜默吃掉**（沒有任何錯誤訊息）
* `TS_IP` 更嚴重：`ports: ["${TS_IP}:6333"]` 會去 bind 預設值那台機器的 IP，
  docker 直接啟動失敗

因此前綴活在**被追蹤的總表**裡，`render` 才挑列寫成不帶前綴的鍵。
好處是三台的值第一次變成同一個檔裡可比對、可 review、受 code review 保護。

另一種把機台名放進變數名的寫法（同一台機器**同時**要有多組設定時用）：
`HOST_API_X570` / `HOST_API_MBP` / `HOST_API_MSI`（`rag.py:167-171`）。

## 總表規則（`hosts.shared.env`）

* 格式 `<機台前綴>_<鍵名>=<值>`，前綴只允許 `x570` / `mbp` / `msi`
* **每個鍵三台都要有列**（缺列＝漏改，render 直接報錯）；值可以空
* **空值＝該機沿用自己 `.env` 現值**，render 不覆蓋
  —— 我不知道那台的值時就留空，不要猜：猜值寫進被追蹤的檔等於把謊言版本化
* `${VAR}` 在 render 時用該機 `.env` 現值展開；引用的變數為空就**硬失敗**
  （寫出空密碼的 DSN 比不寫更糟）
* `OLLAMA_MODELS` 與 `OLLAMA_URLS` 位置對應，長度不一致 render 會擋
  （`rag.py:215` 用 index 取值，長度不符只會「某台 ollama 拿到別台的模型」）
* `HOST_ID` **不在表內**：它是 render 的選擇器，要先知道本機是誰才挑得到列

## 各機一次性設定

```bash
# 1. 裝工具（三台都要）
#    Ubuntu/WSL：下載 age v1.3.2 + sops v3.13.3 到 ~/.local/bin（免 sudo）
#    macOS：brew install age sops

# 2. 生自己的 key（已有則跳過，絕不重建）
mkdir -p ~/.config/sops/age && chmod 700 ~/.config/sops ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

# 3. 在 .env 設 HOST_ID（render 的選擇器）
#    HOST_ID=msi   # 或 x570 / mbp

# 4. 回報公鑰（age1... 那行；私鑰絕對不要貼）
grep -oE 'age1[0-9a-z]+' ~/.config/sops/age/keys.txt
```

公鑰進 `.sops.yaml` 後，有私鑰的人跑一次：

```bash
sops updatekeys settings/env/secrets.common.enc.env
git add .sops.yaml settings/env/secrets.common.enc.env
```

## 日常操作

```bash
scripts/env-sync.sh pull            # 解密＋合併＋render（一次同步所有共用層）
scripts/env-sync.sh render          # 只做 per-host（不需 sops）
scripts/env-sync.sh render --dry-run  # 只印「會動哪幾個鍵」，不寫檔、不印值
scripts/env-sync.sh --check         # 鍵覆蓋率＋總表 schema＋漂移＋版控衛生
scripts/env-sync.sh --fingerprints  # 9 把憑證的長度＋sha12，三台比對用
```

輪換憑證：任一台 `sops settings/env/secrets.common.enc.env` 改值存檔，
commit＋push；另兩台 `pull`＋重建容器（key 是啟動參數，不重啟不生效）。
**不要重跑 `--init-secrets`** —— 它是「第一台建立加密檔」用的，會覆蓋整份。

per-host 值要改：改 `hosts.shared.env` 那一列 → commit → 各機 `render`。
`--check` 會在 `.env` 與總表不同時失敗並列出鍵名（不印值）。

## 絕對不要做的事

* 不要 `cat .env`、不要 `env | grep KEY`、不要對本腳本跑 `bash -x`
  （腳本偵測到 xtrace 直接拒絕執行；2026-09-26 三次外洩都是這類）。
* 不要 `docker compose config` 後貼輸出（它展開所有憑證）。
* 回報只給「鍵名＋長度＋sha256 前 12 碼」，格式見 `--fingerprints`。
* `secrets.common.env` 明文檔、`.decrypted.*` 暫存檔絕不進版控
  （`--check` 與 CI 會擋）。
* 不要在總表裡寫死任何密碼。特別是 `POSTGRES_DSN`：它內嵌的是 **x570 的**
  pg 密碼，不是本機的 `POSTGRES_PASSWORD`（2026-09-27 MSI 實測兩者指紋不同）。
  正解是新增 `POSTGRES_PEER_PASSWORD`（照 `QDRANT_PEER_API_KEY` 慣例）並以
  `${POSTGRES_PEER_PASSWORD}` 引用；該值待 x570 查證後納管（`X570-HANDOFF.md`
  事項 2），在此之前三列 `POSTGRES_DSN` 保持空值。
