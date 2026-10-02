# 環境變數清單（三機並排）

由 `scripts/env-inventory.py` 產生 —— **全部欄位從程式碼反查，不含任何值**。

```bash
python3 scripts/env-inventory.py                    # 重新產生本檔
python3 scripts/env-inventory.py --emit-column     # 匯入本機那一欄
```

## 怎麼讀這張表

| 欄 | 意義 |
|---|---|
| `SET` | 該機的 `.env` 裡**有值** |
| `EMPTY` | 有這一行但值是空的（會吃預設值）|
| `ABSENT` | `.env` 裡沒有這一行 |
| `誰讀` | 檔名:行號。**從程式碼反查**，不是人工填的 |
| `決策` | 已由 `default_decision()` 依證據填好（可推翻，但請說理由）|

> ⚠️ 這份清單**不證明值一致**。值的一致性是 `scripts/env-sync.sh --fingerprints` 的工作（它比 sha12，不印值）。

## 共用憑證（sops 分發，三台必須同值）

這些的值只存在 `settings/env/secrets.common.enc.env`（sops+age 加密）與各機 `.env`。**清單裡沒有值，也不該有。**

| 變數 | 誰讀 | 用途 | x570 | mbp | wsl | 決策 |
|---|---|---|---|---|---|---|
| `ADMIN_TOKEN` | compose.yaml:183, backend/app/main.py:282 | 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `CF_ACCESS_CLIENT_ID` | compose.yaml:143, backend/app/gateway.py:133 | 必填 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `CF_ACCESS_CLIENT_SECRET` | compose.yaml:144, backend/app/gateway.py:134 | 必填；憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `CF_AIG_TOKEN` | compose.yaml:136, backend/app/gateway.py:80 | 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `HF_TOKEN` | compose.yaml:163, backend/app/rag.py:112 | 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `NVIDIA_API_KEY` | compose.yaml:154, backend/app/rag.py:71 | 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `QDRANT_PEER_API_KEY` | compose.yaml:30, scripts/sync-snapshot.sh:65 | 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `TYPESAFE_API_KEY` | compose.yaml:167, backend/app/rag.py:131 | 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |

## per-host 機密（**不分發**，各機自己的值）

刻意不在 sops 裡（`env-sync.sh` 的 `PER_HOST_SECRETS` 註解說明為什麼）。**丟了沒有任何來源能重建** —— 這兩把的備份是 `scripts/backup-env.sh`。

| 變數 | 誰讀 | 用途 | x570 | mbp | wsl | 決策 |
|---|---|---|---|---|---|---|
| `POSTGRES_PASSWORD` | compose.yaml:51, compose.yaml:74 | 必填；憑證 | 待填 | 待填 | SET | **留** — per-host 機密，只存在這台，刪了永久損失 |
| `QDRANT_API_KEY` | compose.yaml:15, compose.yaml:71 | 憑證 | 待填 | 待填 | SET | **留** — per-host 機密，只存在這台，刪了永久損失 |

## per-host 設定（值來自 `settings/env/hosts.shared.env` 的 `<機台>_<鍵>` 列）

| 變數 | 誰讀 | 用途 | x570 | mbp | wsl | 決策 |
|---|---|---|---|---|---|---|
| `HOST_API_URLS` | compose.yaml:116, backend/app/gateway.py:113 | 選填 | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |
| `HOST_ID` | compose.yaml:98, backend/app/gateway.py:85 | 必填 | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |
| `HOST_MACHINE_ID` | compose.yaml:105, backend/app/registry.py:27 | 選填 | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |
| `HOST_NAME` | compose.yaml:104, backend/app/registry.py:15 | 選填 | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |
| `LLM_MODEL` | compose.yaml:93, compose.yaml:119 | 必填；預設 qwen3:14b} | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |
| `OLLAMA` | ingest/laws/qdrant_load.py:29 | 只有 host 端的腳本／ingest 讀得到，容器拿不到 | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |
| `OLLAMA_URLS` | compose.yaml:84, backend/app/gateway.py:72 | 預設 http://host.docker.internal:11434 | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |
| `POSTGRES_DSN` | compose.yaml:74, backend/app/common/pg.py:24 | 必填 | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |
| `QDRANT_URLS` | backend/app/gateway.py:74 | compose 沒列 environment → 容器讀不到，只吃原始碼預設 | 待填 | 待填 | 待填 | **待確認** — compose 沒傳入容器 → 現在設了無效；若日後要傳入，值要重新填 |
| `SRC_API_URL` | scripts/sync-snapshot.sh:146, scripts/sync-snapshot.sh:147 | 只有 host 端的腳本／ingest 讀得到，容器拿不到 | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |
| `TS_IP` | compose.yaml:12, compose.yaml:48 | 預設 127.0.0.1 | 待填 | 待填 | SET | **留** — per-host 設定，render 依 HOST_ID 挑列 |

## 其餘變數（共用非敏感設定）

| 變數 | 誰讀 | 分類／用途 | x570 | mbp | wsl | 決策 |
|---|---|---|---|---|---|---|
| `ACCESS_HOSTS` | scripts/access-check.sh:20 | **host 端** — 只有 host 端的腳本／ingest 讀得到，容器拿不到 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `ADMIN_TOKEN` | compose.yaml:183, backend/app/main.py:282 | **compose／backend** — 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `CF_ACCESS_CLIENT_ID` | compose.yaml:143, backend/app/gateway.py:133 | **compose／backend／scripts** — 必填 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `CF_ACCESS_CLIENT_SECRET` | compose.yaml:144, backend/app/gateway.py:134 | **compose／backend／scripts** — 必填；憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `CF_AIG_GATEWAY_ID` | compose.yaml:147, backend/app/rag.py:81 | **compose／backend** — 必填；預設 cloudflaregateway | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 cloudflaregateway） |
| `CF_AIG_TOKEN` | compose.yaml:136, backend/app/gateway.py:80 | **compose／backend** — 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `CF_AIG_TOKEN_FILE` | compose.yaml:148, backend/app/gateway.py:81 | **compose／backend** — 憑證 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `COHERE_GATEWAY_URL` | compose.yaml:160, backend/app/rag.py:100 | **compose／backend** — 選填 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `COHERE_MODELS` | compose.yaml:161 | **compose** — 預設 command-a-plus-05-2026,command-a-03-2025 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 command-a-plus-05-2026,command-a-03-） |
| `COLLECTION` | compose.yaml:126, backend/app/retrieve.py:23 | **compose／backend** — 必填；預設 laws | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 laws） |
| `EMBED_MODEL` | compose.yaml:124, backend/app/gateway.py:76 | **compose 寫死** — compose 以字面值覆寫 → **.env 設了對容器無效** | 待填 | 待填 | 待填 | **刪** — compose 以字面值覆寫 → .env 設了對容器無效；要生效得先改 compose.yaml |
| `GEMINI_GATEWAY_URL` | compose.yaml:156, backend/app/rag.py:82 | **compose／backend** — 選填 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `GEMINI_MODELS` | compose.yaml:157 | **compose** — 預設 gemini-3.8-flash,gemini-3.5-flash,gemini | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 gemini-3.8-flash,gemini-3.5-flash,ge） |
| `GROQ_GATEWAY_URL` | compose.yaml:158, backend/app/rag.py:90 | **compose／backend** — 選填 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `GROQ_MODELS` | compose.yaml:159 | **compose** — 預設 openai/gpt-oss-120b,openai/gpt-oss-20b,q | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 openai/gpt-oss-120b,openai/gpt-oss-2） |
| `HF_BASE_URL` | compose.yaml:162, backend/app/rag.py:111 | **compose／backend** — 必填；預設 https://router.huggingface.co/v1 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 https://router.huggingface.co/v1） |
| `HF_MODELS` | compose.yaml:164 | **compose** — 預設 deepseek-ai/DeepSeek-V4.1-Flash,Qwen/Qwe | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 deepseek-ai/DeepSeek-V4.1-Flash,Qwen） |
| `HF_TOKEN` | compose.yaml:163, backend/app/rag.py:112 | **compose／backend** — 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `HOST_API_LOCAL` | scripts/host-doctor.sh:38, scripts/host-sync.sh:47 | **host 端** — 只有 host 端的腳本／ingest 讀得到，容器拿不到 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `HOST_HOSTNAME_FILE` | backend/app/registry.py:21 | **未傳入容器** — compose 沒列 environment → 容器讀不到，只吃原始碼預設 | 待填 | 待填 | 待填 | **待確認** — compose 沒傳入容器 → 現在設了無效；若日後要傳入，值要重新填 |
| `HOST_MACHINE_ID_FILE` | backend/app/registry.py:20 | **未傳入容器** — compose 沒列 environment → 容器讀不到，只吃原始碼預設 | 待填 | 待填 | 待填 | **待確認** — compose 沒傳入容器 → 現在設了無效；若日後要傳入，值要重新填 |
| `JEV_BANK_MIN` | compose.yaml:182, backend/app/rag.py:140 | **compose／backend** — 必填；預設 0.6 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 0.6） |
| `JEV_DISABLED` | compose.yaml:180, backend/app/rag.py:134 | **compose／backend** — 選填 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `JEV_MODEL` | compose.yaml:174, backend/app/rag.py:133 | **compose／backend** — 必填；預設 jev-latest | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 jev-latest） |
| `JEV_VERIFY_MIN` | compose.yaml:181, backend/app/rag.py:136 | **compose／backend** — 必填；預設 0.4 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 0.4） |
| `KEEP_ALIVE` | compose.yaml:120, backend/app/gateway.py:89 | **compose／backend** — 必填；預設 -1 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 -1） |
| `LAW_SYNC_SOURCE` | scripts/law-update-worker.sh:120, scripts/sync-snapshot.sh:121 | **host 端** — 只有 host 端的腳本／ingest 讀得到，容器拿不到 | 待填 | 待填 | SET | **留** — 有值且被讀 |
| `LIMIT` | ingest/laws/qdrant_load.py:293 | **host 端** — 只有 host 端的腳本／ingest 讀得到，容器拿不到 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `MISTRAL_GATEWAY_URL` | compose.yaml:165, backend/app/rag.py:121 | **compose／backend** — 選填 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `MISTRAL_MODELS` | compose.yaml:166 | **compose** — 預設 ministral-8b-latest,codestral-latest | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 ministral-8b-latest,codestral-latest） |
| `NVIDIA_API_KEY` | compose.yaml:154, backend/app/rag.py:71 | **compose／backend** — 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `NVIDIA_BASE_URL` | compose.yaml:153, backend/app/rag.py:70 | **compose／backend** — 必填；預設 https://integrate.api.nvidia.com/v1 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 https://integrate.api.nvidia.com/v1） |
| `NVIDIA_MODELS` | compose.yaml:155 | **compose** — 預設 nvidia/nemotron-3.5-lightning-30b-a3b,nv | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 nvidia/nemotron-3.5-lightning-30b-a3） |
| `OLLAMA_BASE_URL` | compose.yaml:83, backend/app/gateway.py:31 | **低優先** — 高優先的 OLLAMA_URLS 設了有效值時用不到它 | 待填 | 待填 | 待填 | **清空** — 低優先：有更高優先的來源（OLLAMA_URLS） |
| `OLLAMA_MODELS` | compose.yaml:93, backend/app/gateway.py:79 | **compose／backend** — 選填 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `OPENROUTER_GATEWAY_URL` | compose.yaml:135, backend/app/rag.py:42 | **compose／backend** — 選填 | 待填 | 待填 | SET | **留** — 有值且被讀 |
| `OPENROUTER_MODELS` | compose.yaml:149 | **compose** — 預設 cohere/north-mini-code:free,dots-studio/ | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 cohere/north-mini-code:free,dots-stu） |
| `PG_CONNECT_TIMEOUT` | compose.yaml:80, backend/app/common/pg.py:37 | **compose／backend** — 必填；預設 3 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 3） |
| `PICK_TTL` | compose.yaml:123, backend/app/gateway.py:87 | **compose／backend** — 必填；預設 30 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 30） |
| `POSTGRES_DB` | compose.yaml:52 | **compose 寫死** — compose 以字面值覆寫 → **.env 設了對容器無效** | 待填 | 待填 | 待填 | **刪** — compose 以字面值覆寫 → .env 設了對容器無效；要生效得先改 compose.yaml |
| `POSTGRES_USER` | compose.yaml:50 | **compose 寫死** — compose 以字面值覆寫 → **.env 設了對容器無效** | 待填 | 待填 | 待填 | **刪** — compose 以字面值覆寫 → .env 設了對容器無效；要生效得先改 compose.yaml |
| `PROBE_TIMEOUT` | compose.yaml:134, backend/app/gateway.py:114 | **compose／backend** — 必填；預設 2.5 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 2.5） |
| `QDRANT` | ingest/laws/qdrant_load.py:30 | **host 端** — 只有 host 端的腳本／ingest 讀得到，容器拿不到 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `QDRANT_PEER_API_KEY` | compose.yaml:30, scripts/sync-snapshot.sh:65 | **compose／scripts** — 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `QDRANT_URL` | compose.yaml:70, backend/app/gateway.py:32 | **compose 寫死** — compose 以字面值覆寫 → **.env 設了對容器無效** | 待填 | 待填 | 待填 | **刪** — compose 以字面值覆寫 → .env 設了對容器無效；要生效得先改 compose.yaml |
| `RAG_HIGH_DENSE` | compose.yaml:131, backend/app/retrieve.py:31 | **compose／backend** — 必填；預設 0.70 | 待填 | 待填 | EMPTY | **留** — 空值但有預設值 —— 留著是文件（預設 0.70） |
| `RAG_MID_DENSE` | compose.yaml:130, backend/app/retrieve.py:30 | **compose／backend** — 必填；預設 0.62 | 待填 | 待填 | EMPTY | **留** — 空值但有預設值 —— 留著是文件（預設 0.62） |
| `RAG_MIN_DENSE` | compose.yaml:129, backend/app/retrieve.py:29 | **compose／backend** — 必填；預設 0.58 | 待填 | 待填 | EMPTY | **留** — 空值但有預設值 —— 留著是文件（預設 0.58） |
| `REGISTRY_HEARTBEAT` | compose.yaml:110, backend/app/registry.py:18 | **compose／backend** — 必填；預設 30 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 30） |
| `REGISTRY_STALE_MIN` | compose.yaml:111, backend/app/registry.py:19 | **compose／backend** — 必填；預設 3 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 3） |
| `RERANK_MODEL` | compose.yaml:125, backend/app/retrieve.py:22 | **compose 寫死** — compose 以字面值覆寫 → **.env 設了對容器無效** | 待填 | 待填 | 待填 | **刪** — compose 以字面值覆寫 → .env 設了對容器無效；要生效得先改 compose.yaml |
| `TYPESAFE_API_KEY` | compose.yaml:167, backend/app/rag.py:131 | **compose／backend** — 憑證 | 待填 | 待填 | SET | **留** — 共用憑證，sops 分發，三台必須同值 |
| `TYPESAFE_URL` | compose.yaml:171, backend/app/rag.py:132 | **compose／backend** — 必填；預設 https://api.typesafe.ai/v1/systemone | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 https://api.typesafe.ai/v1/systemone） |
| `ZEN_API_KEY` | compose.yaml:151, backend/app/rag.py:60 | **compose／backend** — 憑證 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |
| `ZEN_BASE_URL` | compose.yaml:150, backend/app/rag.py:59 | **compose／backend** — 必填；預設 https://opencode.ai/zen/v1 | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 https://opencode.ai/zen/v1） |
| `ZEN_FREE_MODELS` | compose.yaml:152 | **compose** — 預設 deepseek-v4-flash-free,muse-spark-1.3-co | 待填 | 待填 | 待填 | **留** — 空值但有預設值 —— 留著是文件（預設 deepseek-v4-flash-free,muse-spark-1.） |
| `layout_fp` | scripts/env-sync.sh:626 | **host 端** — 只有 host 端的腳本／ingest 讀得到，容器拿不到 | 待填 | 待填 | 待填 | **留** — 空值、無預設值 —— 這是給人填的槽位；刪了之後要加回來，得先知道它存在過 |

## ⚠️ 幽靈鍵（`.env` 裡有，但**程式碼沒讀它**）

（無 —— 已回報的機器都沒有幽靈鍵。**未回報的機器仍可能有**，所以這一欄要等三台都匯入才算數。）

## 政策上停用（**刻意不列入決策候選**）

- `LAN_IP` —— IP 準則（2026-09-22 定案）：一律 tailscale IP（100.64.0.0/10），停用 LAN_IP。192.168.x 一律不寫入 .env、不進 registry。追蹤檔出現 LAN_IP= 會被 pre-push hook 與 CI 擋下。見 ARCHITECTURE.md。

  這裡不列決策欄，因為 `.env.example` 刻意不印 `NAME=` 行，而 pre-push 與 CI 會擋 `^LAN_IP=`。若把它放進決策表，下游任何「從清單產生 .env」的工具都會產出一行被擋掉的賦值。

## 決策欄（已依證據填好；要推翻請寫理由）

`決策` 欄判斷的是**取捨**，不是「有沒有人讀」—— 後者已經在 `誰讀` 欄了。

> 這裡的預設是 `default_decision()` 的輸出，判準全是機械可查的（registry 的欄位 ＋ 本機 `.env` 的 SET/EMPTY）。**不是**「我覺得它沒用」。
> 推翻某一列時請在理由欄後面加一句 why —— 否則下次重跑這個檔會被蓋掉，
> 而那份理由正是最值得留下的東西。

| 決策 | 什麼時候 | 刪掉之後 |
|---|---|---|
| **留** | 有值且被讀；或雖然被讀但它是文件的骨架（留著才知道有這個鍵）| — |
| **清空** | `EMPTY` ＋ 有預設值 ＋ 從沒人設過 | 值不變（吃預設）|
| **刪** | 幽靈鍵（沒人讀）；或「compose 寫死」而 `.env` 有值的（設了無效，留著只誤導）| 日後要生效得先改 compose |
| **待確認** | `誰讀` 只指向 host 端腳本，而那支腳本可能只在某些情況跑 | 先查讀取點 |

⚠️ **刪之前一定要有 `.env` 的備份。** 這不是形式：
`QDRANT_API_KEY`／`POSTGRES_PASSWORD` 兩把只存在那一台，刪錯就是永久損失。

