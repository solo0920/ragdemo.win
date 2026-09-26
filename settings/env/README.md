# settings/env — 三機共用憑證的分層與分發

## 為什麼有這層

`QDRANT_API_KEY` 等 9 把憑證必須三台一致。過去靠人工複製，
2026-09-26 已造成兩次不對稱故障（本機 200、遠端 401；registry 心跳失敗）。
整份 `.env` 不能直接同步 —— `HOST_ID`、`TS_IP`、`LLM_MODEL`（msi 是 8b）、
`OLLAMA_URLS`、`POSTGRES_DSN` 等每台都不同，全量覆蓋會寫壞機器。

## 分層

| 檔案 | 追蹤 | 內容 |
|---|---|---|
| `common.env` | 是（明文） | 共用**非敏感**鍵。值空＝用 compose 預設 |
| `hosts/<id>.env.example` | 是（明文） | per-machine 範本，只有鍵名＋佔位 |
| `hosts/<id>.env` | 否 | 各機真值，不存在於 repo，只活在本機 `.env` |
| `secrets.common.env.example` | 是（明文） | 9 把共用憑證的鍵名，值一律空 |
| `secrets.common.enc.env` | 是（**加密**） | 9 把憑證真值，sops+age 加密 |
| `.sops.yaml`（repo 根） | 是 | age recipient 公鑰清單 |

執行期真相仍是 repo 根 `.env`（gitignored，600），compose 與腳本照舊讀它。
本層只改變「共用憑證怎麼進 `.env`」。

## 各機一次性設定

```bash
# 1. 裝工具（MSI 已裝到 ~/.local/bin；三台都要）
#    Ubuntu/WSL：下載 age v1.3.2 + sops v3.13.3 到 ~/.local/bin（免 sudo）
#    macOS：brew install age sops

# 2. 生自己的 key（每台一次；已有則跳過，絕不重建）
mkdir -p ~/.config/sops/age && chmod 700 ~/.config/sops ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

# 3. 把公鑰（age1... 那行）回報給的人，不要貼私鑰
grep -oE 'age1[0-9a-z]+' ~/.config/sops/age/keys.txt
```

公鑰進 `.sops.yaml` 後，有私鑰的人跑一次：

```bash
sops updatekeys settings/env/secrets.common.enc.env
git add .sops.yaml settings/env/secrets.common.enc.env
```

## 日常操作（每台）

```bash
scripts/env-sync.sh pull            # 解密＋合併進 .env（per-machine 鍵不動）
scripts/env-sync.sh --check         # 鍵覆蓋率＋版控衛生，不需解密
scripts/env-sync.sh --fingerprints  # 9 把憑證的長度＋sha12，三台比對用
```

輪換憑證：任一台 `sops settings/env/secrets.common.enc.env` 改值存檔，
commit＋push；另兩台 `pull`＋`env-sync.sh pull`＋重建容器（key 是啟動參數）。
空值語意：加密檔裡 `KEY=`（空）表示「未設定」，合併時會跳過、
不會清空別台的真值；9 個鍵永遠齊全只是為了 schema 穩定。

## 絕對不要做的事

* 不要 `cat .env`、不要 `env | grep KEY`、不要對本腳本跑 `bash -x`
  （腳本偵測到 xtrace 直接拒絕執行；2026-09-26 三次外洩都是這類）。
* 不要 `docker compose config` 後貼輸出（它展開所有憑證）。
* 回報只給「鍵名＋長度＋sha256 前 12 碼」，格式見 `--fingerprints`。
* `secrets.common.env` 明文檔、`.decrypted.*` 暫存檔絕不進版控
  （`--check` 與 CI 會擋）。
