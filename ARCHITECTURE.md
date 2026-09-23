# RagDemo 架構（維護用）

```
compose.yaml      # qdrant + postgres + api（x570 用）
backend/          # FastAPI：/health /ingest /query /eval
  app/            #   main.py / rag.py / registry.py
  start-msi.sh    #   MSI WSL 開機自動啟動（api＋本機 qdrant 備援）
  .env            #   各機一份，不進版控
frontend/         # SvelteKit：只打 /api/*
evals/            # 評測題庫，目標 50 題
scripts/
  sync-snapshot.sh #   x570→本機 qdrant 快照自動同步（備援核心）
.opencode/agents/ # ingest / backend / frontend / eval 四個子代理
```

## 設計目標（2026-09-22 定案）
**x570 不在線時，mbp 與 MSI 都能獨立作業。**
除硬體與 LLM 模型差異外，三台的**資料與檢索能力盡量一致、各自可獨立運作**。

- 依賴關係：各機自備 ollama（LLM/embedding，硬體差異故模型不同）；
  資料層（qdrant）以「快照同步」維持一致；pg 只有 registry 心跳用（非 query 必需）。
- 降級邏輯走 `QDRANT_URLS`/`OLLAMA_URLS` 候選清單：x570 優先，離線自動切本機。

## 主機命名（2026-09-22 定案，三台嚴格執行）
**三台一律用硬體代號 `x570` / `mbp` / `msi`；`linux` 是作業系統名，禁止拿來當主機名。**

| 代號 | 機器 | OS | tailscale IP |
|---|---|---|---|
| `x570` | solo-X570-I-AORUS-PRO-WIFI | Linux | 100.119.83.111 |
| `mbp` | Lees-MacBook-Pro | macOS | 100.64.121.9 |
| `msi` | MSI（WSL2 Ubuntu） | Windows | 100.65.68.106 |

- 適用範圍：`.env` 的 `HOST_ID`、前端切換按鈕 id、commit 前綴（`msi:`/`mbp:`/`x570:`）、
  `sync-snapshot.sh` 註解、文件（本檔／ROADMAP）——一律寫 `x570`，不寫 `linux`。
- 指作業系統／WSL 技術本身時才寫 Linux（例：「WSL/Linux 跑 opencode」「x570 跑 Linux」）。
- 沿革：早期以 OS 名 `linux` 代稱主力機；2026-09-22 定案改用硬體代號
  （mbp/msi 本來就是硬體名，獨 linux 是 OS 名，故統一為 x570）。

## IP 準則（2026-09-22 定案，三台嚴格執行）
**連線與登錄一律只留 tailscale IP（100.64.0.0/10），杜絕位址污染。**

- `TS_IP` 必填；**不使用 LAN_IP**（192.168.x 一律不寫入 .env、不進 registry）。
- `OLLAMA_URLS`/`QDRANT_URLS`/`POSTGRES_DSN` 全部用 tailscale 位址；
  唯一例外是**本機服務**可寫 `127.0.0.1`（如本機 qdrant 備援、x570 localhost）。
- 強制執行點：`registry.py _ips()` 已過濾，非 100.64.0.0/10 的 IP
  （LAN/容器/loopback）不會寫入 registry 的 `ips`，`/hosts` 只會看到 tailscale。
- 實作前有污染（eg mbp ips 混入 `192.168.0.2`）＝某台 .env 殘留 LAN_IP/
  OLLAMA_URLS 打到別台——各台 pull 後**核對 .env、移除 LAN_IP**，心跳 30s 自動清乾淨。

## 提交準則（2026-09-22 定案，三台嚴格執行）
**commit message 首行必須以 tailscale 機器名前綴開頭：`msi:` / `mbp:` / `x570:`。**

- 三台 git 身份相同（Solomon Lee/solo4study@gmail.com→solo0920），單看 git log 無法分辨
  主機；改以主機前綴標記，`git log --oneline` 一眼可讀。
- 前綴對應：`msi`=MSI（100.65.68.106）、`mbp`=Lees-MacBook-Pro（100.64.121.9）、
  `x570`=solo-X570-I-AORUS-PRO-WIFI（100.119.83.111，tailscale 名統一寫 x570）。
- **強制執行**：repo 內 `.githooks/commit-msg`（違反首行前綴直接拒絕；Merge/Revert 自動放行）。
  各機啟用一次：`git config core.hooksPath .githooks`。
- 正文格式不拘，範例：`msi: 修正 registry 過濾邏輯`。

## Git hooks（.githooks/，2026-09-22）
repo 內 hook 隨版控走、各機 pull 即得；**各機啟用一次**：`git config core.hooksPath .githooks`
（寫在 repo 的 `.git/config`，不進版控、不影響別台）。`git config core.hooksPath` 回 `.githooks`
表示生效；hook 檔案更新後已啟用機器自動用新版。

| hook | 時機 | 作用 |
|---|---|---|
| `commit-msg` | `git commit` | 強制首行 `msi:`/`mbp:`/`x570:`（提交準則）；Merge/Revert 放行 |
| `pre-push` | `git push` | **強制**：backend Python 語法＋追蹤檔無 `LAN_IP=`（IP 準則）；**可選**：完整 HTTP smoke |

`pre-push` 行為：
- 強制項不過 → 擋下 push（語法錯／發現 `LAN_IP=` 殘留）
- 本機 api 在線 → 自動回報 `/health` 狀態
- 加 `RAGDEMO_SMOKE=1 git push` → 追加跑 `/query`（驗證答案非空），較慢但完整
- 本機 api 不在線 → HTTP 部分只警告不擋 push（離線也可推，語法/IP 檢查照跑）
- 想繞過（緊急）：`git push --no-verify`

## 三機分工（連線一律 tailscale，見 IP 準則）
* x570 100.119.83.111：主力，`OLLAMA_URLS=http://127.0.0.1:11434,http://100.119.83.111:11434`，
  `LLM_MODEL=qwen3:14b`，api/qdrant/pg 全跑（docker compose），資料唯一來源
* mbp 100.64.121.9：加速，`LLM_MODEL=qwen3:14b`，
  api（uvicorn@8000，launchd 自動啟動）＋**本機 qdrant 備援已完成**（QDRANT/POSTGRES 指 x570，離線降級本機）
* MSI 100.65.68.106（demo）：api 在 WSL2 裡（`uvicorn --env-file .env`，開機自動啟動），
  `OLLAMA_URLS=http://100.65.68.106:11434`（tailscale 直連 Windows 本機 ollama），`LLM_MODEL=qwen3:4b`，
  資料層（Qdrant/pg）指 x570（tailscale）＋ **本機 qdrant 1.19.1 備援（已完成）**

## 備援機制（x570 離線時各機獨立作業）

```
x570 qdrant（資料唯一來源）
   │  資料變更（points_count 變化）
   ▼
scripts/sync-snapshot.sh（crontab 每 10 分鐘，MSI 已掛；mbp 用 launchd 同頻率）
   │  POST /collections/laws/snapshots → 建新快照
   │  下載 → 本機刪舊 collection → 重建 → upload?priority=snapshot 還原
   │  驗證點數與 x570 一致 → 更新 state；順手清 x570 舊快照
   ▼
各機本機 qdrant（MSI: 127.0.0.1:6333 ✅ / mbp: 127.0.0.1:6333 ✅）
```

- **資料一致性**：備援資料等同 x570 快照當下；快照很小（3 筆≈174KB、500~1000 筆≈10–60MB，
  zstd 壓縮），同步成本低。
- **脆弱點**：x570 離線期間新增的資料不會自動出現在備援（下一個快照週期才補上）——
  可接受，檢索能力仍一致。

### QDRANT_URLS 降級（MSI 現況，全 tailscale＋本機）
```
QDRANT_URLS=http://100.119.83.111:6333,http://127.0.0.1:6333
```
`rag.py _pick()`：依序試候選，首個通連者快取；連線錯誤自動降級下一個。
→ x570 在線用 x570（最新）；離線自動切本機（快照資料），query 不中斷。

### pg / registry 的定位
- `POSTGRES_DSN` 只指 x570：心跳寫 `backends` 表、`GET /hosts` 讀表。
- 心跳時自動清掉超過 `REGISTRY_STALE_MIN`（預設 3 分鐘）未報到的 host row
  （離線/改名殘留自動消失）；PG 離線時此清理不跑、無害。
- x570 離線時：心跳失敗只是 warning（`main.py` try/except），**不影響 /query**；
  `/hosts` 回空清單（可接受）。
- 若需離線 `/hosts`，得在 mbp/msi 上另建 pg 副本（低優先，非 query 必需）。

## 模型清單（2026-09-21 實測後）
* x570：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3:14b`、`qwen3-coder:latest`
* mbp：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3-coder:latest`、`qwen3-coder-next:latest`、`qwen3:14b`
* MSI：`bge-m3`、`qllama/bge-reranker-v2-m3`、`qwen3:4b`、`qwen2.5-coder:7b`（qwen3.5:4b 已棄用）

## 實測速度（eval tok/s，同 prompt num_predict=200）
* x570 coder 94.8 ＞ mbp coder 73 ＞ MSI 4b 79 ＞ x570 14b 熱機 82（冷機 19，待再驗）＞ mbp 14b 25
* 結論：重推理放 x570，日常寫碼可用 mbp coder，MSI 只跑輕量

## 服務埠
* 8000 api（x570 docker compose＋mbp＋MSI WSL） / 6333 qdrant（x570＋MSI 本機備援） /
  5432 postgres（x570 only） / 5173 前端 dev / 11434 ollama（各機 native）

## 公網接手（Cloudflare tunnel keepalive 規範，2026-09-23 定案）

三台各自一條 local tunnel（`ragdemo-x570`/`ragdemo-mbp`/`ragdemo-msi`），
公網 hostname `api-x570`/`api-mbp`/`api-msi.ragdemo.win` → 各機 `http://localhost:8000`。
tunnel 是 demo 的命脈，**三台必須統一 keepalive 設定**，否則斷線不會自己回來。

### keepalive 三層（缺一不可）

1. **進程層（tunnel 崩潰自動重啟）**——最關鍵
   - mbp：launchd `com.ragdemo.tunnel`（`KeepAlive=true`，崩潰自動拉起）✅ 已達標
   - msi / x570：目前只有 crontab `@reboot`，**只開機起一次，進程崩潰不會自動復活** ❌
   - 目標：crontab 改用 `restart loop` 包裝（見下方範本），或改用 systemd（x570，需 sudo）
2. **edge 連線層（cloudflared↔Cloudflare edge 長連線）**
   - 統一用 `--protocol http2`（http2 為長連線＋內建 keepalive，WSL2 上 QUIC/UDP 全 timeout，見 §http2 說明）
   - msi ✅、mbp ✅ 已加；**x570 待確認/補上**
3. **origin 連線層（cloudflared↔本機 :8000 的 HTTP 連線池）**
   - `config.yml` 的 `ingress` 可用 `originRequest` 覆寫 keepalive 參數：
     `proxyTCPKeepAlive`（預設 30s）、`proxyKeepAliveConnections`（預設 100）、
     `proxyKeepAliveTimeout`（預設 1m30s）
   - 三台目前都用「預設值」＝已達標，**不需改 config**（除非之後想調）

### 三台目標狀態（驗收標準）

| 主機 | 進程自動重啟 | `--protocol http2` | originRequest keepalive |
|---|---|---|---|
| x570 | crontab restart loop（或 systemd） | 加入 | 預設（不用改）|
| mbp | launchd KeepAlive ✅ | ✅ | 預設 |
| msi | crontab restart loop | ✅ | 預設 |

### crontab restart loop 範本（msi / x570）

```bash
# 取代原本 @reboot 單次啟動：崩潰後每 5 秒自動重拉
@reboot /bin/bash -lc 'while true; do /home/solo/.local/bin/cloudflared tunnel --protocol http2 --config /home/solo/.cloudflared/config.yml run ragdemo-<host> >> /tmp/cfd.log 2>&1; echo "[tunnel] exit $? at $(date)" >> /tmp/cfd.log; sleep 5; done' &
```

> 注意：crontab 不展開 `~`，一律用絕對路徑；`setsid` 讓它脫離終端，`&` 避免 crontab 等待。

### 驗證 keepalive 是否生效

```bash
# 1) 進程在跑（三台各自）
ps aux | grep [c]loudflared
# 2) edge 連線數＋錯誤（已達標應看到 4 條連線、errors=0）
curl -s http://127.0.0.1:20241/metrics | grep -E 'cloudflared_tunnel_(ha_connections|request_errors|server_locations)'
# 3) 公網 health（三台各自）
curl -s https://api-<host>.ragdemo.win/health
# 4) 崩潰自動復活測試：kill 掉 cloudflared，5 秒內 restart loop / launchd 應自動拉回
```

### http2 說明（為何 keepalive 必須靠 http2）

- cloudflared 連 edge 有兩種協議：**QUIC**（UDP 443）與 **http2**（TCP 443）。
- **WSL2（msi）的 UDP 連 Cloudflare edge 全部 timeout**（登入/連線 1033 錯誤）→ 必須 `--protocol http2`，
  這是 msi 實戰心得（2026-09-23，ROADMAP §4.3）。
- http2 是「單一長連線＋內建 keepalive ping」，斷線自動重連；QUIC 雖也有 keepalive，
  但跨 NAT/容器/虛擬網路（WSL2、docker bridge）常因 UDP 被擋而失去保活。
- 為一致性與穩定性，**三台統一 `--protocol http2`**（macOS／原生 Linux 也可用，不虧）。

## 環境變數（backend/.env，各機一份不進版控）
`HOST_ID`、`TS_IP`（必填，tailscale）、`OLLAMA_URLS`（候選清單）、`LLM_MODEL`、
`EMBED_MODEL=bge-m3:latest`、`RERANK_MODEL=qllama/bge-reranker-v2-m3:latest`、
`COLLECTION=laws`、`QDRANT_URLS`、`POSTGRES_DSN`。
IP 準則：全部 tailscale 位址，本機服務才允許 127.0.0.1，不用 LAN_IP（見上方準則）。

## 評測門檻
`POST /eval` hit_rate 未達 0.8 不進 UI，先修切分/召回。

## Google 登入（frontend，OAuth2 PKCE）
- Server routes：`/auth/login`（PKCE＋state cookie → 302 Google）、`/auth/callback`（code 換 token、
  JWKS RS256 驗證、HMAC session cookie 12h）、`/auth/logout`、`/auth/me`。
- env：`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `SESSION_SECRET`（dev 填 `frontend/.env`，上線填 Pages 變數）。
- Google Cloud 端重導 URI 需登記：`http://localhost:5173/auth/callback`、`https://ragdemo.win/auth/callback`。
- 目前為「身份辨識」（登入才看得到是誰）；尚未擋查詢/寫入，下一步接寫入端點保護。

## TODO
* `rag.py rerank()` 還是 stub，待接真正 reranker 打分
* `evals/questions.json` 佔位 3 題，待擴 50 題
* 判決注意個資去識別化，回答僅供參考非法律意見
* 精簡包擴到 500~1000 筆（目前 3 筆，同步機制已就位）
* 需離線 `/hosts` → mbp/msi 另建 pg 副本（低優先）

## 啟動
**x570（docker compose）**：
```bash
cp backend/.env.example backend/.env  # 再改 OLLAMA_BASE_URL
docker compose up -d --build  # 首次建 api 映像，之後改碼重跑加 --build
curl localhost:8000/health
cd frontend && npm install && npm run dev
```
**MSI（WSL2，吃 Windows 本機 ollama）**：
```bash
bash backend/start-msi.sh        # 冪等：api＋本機 qdrant 一起拉起（開機自動啟動見 ROADMAP §2.5）
curl localhost:8000/health       # 回 host_id=msi, llm=qwen3:4b
```
MSI 資料層：x570 優先（最新），本機 qdrant 備援（x570 離線自動接手）。
**mbp（launchd，登入自動跑）**：
```bash
launchctl load ~/Library/LaunchAgents/com.ragdemo.qdrant.plist    # qdrant @127.0.0.1:6333
launchctl load ~/Library/LaunchAgents/com.ragdemo.api.plist       # uvicorn @8000（--env-file .env）
launchctl load ~/Library/LaunchAgents/com.ragdemo.sync-snapshot.plist  # 每10分鐘快照同步
curl localhost:8000/health       # 回 host_id=mbp
cd frontend && npm run dev       # 前端 dev（proxy → localhost:8000，需 Node≥22）
```
mbp 資料層：x570 優先（最新），本機 qdrant 備援（x570 離線自動接手）。

## 備援同步操作
```bash
bash scripts/sync-snapshot.sh [source_url] [dest_url] [collection]
# 例（MSI）：  bash scripts/sync-snapshot.sh            # x570→本機，預設
# 例（mbp）：  bash scripts/sync-snapshot.sh http://100.119.83.111:6333 http://127.0.0.1:6333
# 排程：MSI 用 crontab（*/10，已掛）；mbp 用 launchd com.ragdemo.sync-snapshot（每 10 分鐘＋登入）
```
log `~/qdrant/sync.log`、state `~/qdrant/.sync-state`（點數＋快照名，未變化即 skip）。

## Demo 精簡包
x570 全量 → 精選 500~1000 筆 → 靠 `sync-snapshot.sh` 快照機制同步到各機本機 qdrant，
x570 離線時各機照常 `/query`（檢索能力一致，LLM 各機自備）。