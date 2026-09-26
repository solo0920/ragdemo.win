# OpenCode 設定備份 — 總說明

> 本目錄統一備份各台機器的 opencode 設定（global config + 專案 config + 設定方法文件）。
> 目的：換機 / 重灌 / 除錯時可快速還原，並確保三台機器（MSI、MBP、x570）使用一致的
> Cloudflare AI Gateway 認證方式與 Ollama 端點設定。

---

## 📁 目錄結構（每台機器一個子目錄）

```
settings/opencode/
├── README.md              ← 本文件（先讀這個）
├── CF-AIG-TOKEN-ENV.md    ← token 環境變數設定方法＋疑難排解（三台共用）
├── global/               ← global config（openrouter → CF gateway），按機器分
│   ├── msi/opencode.json        # MSI（✅ 已完成）
│   ├── mbp/opencode.json        # MBP（✅ 已完成）
│   └── x570/opencode.jsonc      # x570（✅ 已完成，實際檔名 .jsonc）
└── project/              ← 專案 config（ollama → x570），按機器分
    ├── msi/opencode.json        # MSI（✅ 已完成）
    ├── mbp/opencode.json        # MBP（✅ 已完成）
    └── x570/opencode.json       # x570（✅ 已完成）
```

> **global 與 project 刻意分層**：global 是 `~/.config/opencode/opencode.json`（或 `.jsonc`，
> 三台以 `.json` 為主，x570 用 `.jsonc`）
> （使用者層級，含 provider/token 設定）；project 是各專案根目錄的 `opencode.json`
> （專案層級，含 model/provider 覆寫）。兩類設定位置不同、作用範圍不同，分層存放較好對照。

---

## ✅ 給 MBP、x570 的備份任務（照做）

### 步驟 1：先讀完這份文件
- `settings/opencode/CF-AIG-TOKEN-ENV.md`
  - 了解 token 用「環境變數」或「檔案引用」`{file:...}` 的方式（建議統一用 `{file:}`，最穩健、不依賴 shell）
  - 了解 401 / 429 / Model unavailable 的排解法

### 步驟 2：建立自己的備份目錄並複製設定
```bash
# 在各自的機器上執行（MBP 為例；x570 把 mbp 換成 x570）
mkdir -p settings/opencode/global/mbp settings/opencode/project/mbp

# 備份 global config（來源：使用者層級設定）
cp ~/.config/opencode/opencode.jsonc settings/opencode/global/mbp/opencode.jsonc

# 備份專案 config（來源：各專案根目錄；若該機器也有 ragdemo.win 或其他專案）
cp <專案路徑>/opencode.json settings/opencode/project/mbp/opencode.json
```

### 步驟 3：確認 token 在該機器上可用
- 若用 `{file:}`：把 token 寫到 `~/.config/opencode/cf-aig-token`（chmod 600，內容 `cfut_...`），
  config 寫 `{file:<路徑>/cf-aig-token}`（路徑隨機器調整，例如 `/home/<user>/.config/opencode/cf-aig-token`）
- 用文件 §3 的方法 A（curl）驗證 gateway 通不通

### 步驟 4：驗證 opencode 端到端
```bash
cd <專案目錄>   # 一定要在 git repo 內
opencode run "回覆:OK" --model openrouter/nvidia/nemotron-3-super-120b-a12b:free
```

### 步驟 5：commit 備份
```bash
git add settings/opencode/global/<你的機器名>/ settings/opencode/project/<你的機器名>/
git commit -m "<機器前綴>: 備份 opencode global/專案設定"
```

---

## 🔒 敏感性檔案注意事項（每個人都要遵守）

以下檔案**禁止 commit 進 git**（尤其 repo 會被推上 GitHub 或分享時）：

| 檔案 | 內容 | 處理 |
|---|---|---|
| `~/.config/opencode/cf-aig-token` | Cloudflare gateway token | ❌ 不備份；或備份但加入 `.gitignore` |
| `~/.config/opencode/service.json` | opencode service 密碼 | ❌ 不備份 |
| `.zshrc` / `.bashrc` 中的 `CF_AIG_TOKEN` | 若用環境變數方案 | ❌ 內容不明碼進入 repo |

---

## 🖥️ 三台機器的角色

| 機器 | Tailscale hostname | 角色 | 備份狀態 |
|---|---|---|---|
| **MSI** | `msi` | opencode 主要開發機（Windows + WSL） | ✅ 已完成（`global/msi/` + `project/msi/`） |
| **MBP** | `mbp` | macOS 開發機 | ✅ 已完成（`global/mbp/` + `project/mbp/`） |
| **x570** | `x570.tailfe3f3d.ts.net` | Ollama 模型伺服器（qwen3-coder 等）+ 開發機 | ✅ 已完成（`global/x570/` + `project/x570/`） |

> x570 同時是 Ollama server（`http://x570:11434/v1`，跑 `qwen3-coder:latest`、
> `qwen2.5-coder:14b`、`qwen3:14b`、embedding/reranker models），各機器的專案 config
> 都指向它，因此 x570 的設定也必須留檔。

---

## ⚠️ 重大注意：頂層 key 是 `provider`（單數），不是 `providers`！

> 這是三台機器最容易踩的坑（自我驗證 + 官方 schema 確認）：
> 官方 config 頂層只有 **`provider`**；寫成複數 `providers` 會被 opencode **靜默忽略**，
> 設定檔看起來有寫、實際上完全沒生效（會以內建 provider ＋環境變數直接連上游，不走 gateway）。

- **正確**：`"provider": { "openrouter": { ... } }`
- **錯誤**：`"providers": { "openrouter": { ... } }` ← 會被忽略
- **同理**：`baseURL` 與 header 必須放在 **`options`** 內
  （`settings`/`headers` 直接掛在 provider 下也不是合法 key，會被忽略）。
  正確：`"openrouter": { "options": { "baseURL": "...", "headers": { "cf-aig-authorization": "Bearer {file:...}" } } }`

---

## 🖥️ MSI、MBP、x570 比照辦理（2026-09-25 定案）

三台統一採 **override 內建 `openrouter`** 的寫法（全球設定走 CF gateway）＋ `{file:}` token：

> ✅ **MSI 已完成**（2026-09-25）：實際 config、`global/msi` 備份、方法文件皆已修正為
> `provider` 單數＋`options.headers`，service restart 後端到端驗證通過。
> **MBP、x570 請檢查各自實際 config，確認已比照下列寫法（不可殘留 `providers` 複數）**。

### global config（每台各自的 `~/.config/opencode/opencode.json`）
```json
{
  "provider": {
    "openrouter": {
      "options": {
        "baseURL": "https://gateway.ai.cloudflare.com/v1/<ACCOUNT_ID>/<GATEWAY_ID>/openrouter",
        "apiKey": "{file:~/.config/opencode/cf-aig-token}",
        "headers": {
          "cf-aig-authorization": "Bearer {file:~/.config/opencode/cf-aig-token}"
        }
      }
    }
  }
}
```
- token 寫到各台的 `~/.config/opencode/cf-aig-token`（`chmod 600`，內容 `cfut_...`）
- **不可改寫成 `providers` 複數**（見上節）
- ⚠️ **`apiKey` 必填**：內建 `openrouter` provider 需 apiKey 才肯發請求；填 CF token
  （`cfut_...`，file 引用）即可——gateway 只認 CF token，不接受真 OpenRouter key
  （後者回 `401 / 2009 Unauthorized`）

### 專案 config（各專案根目錄的 `opencode.json`，已含 ollama→x570 與 openrouter 模型覆寫）
```json
{
  "provider": {
    "ollama": { "options": { "baseURL": "http://x570:11434/v1" }, "models": { "qwen3:14b": {}, "qwen2.5-coder:14b": {} } },
    "openrouter": { "models": { "inclusionai/ling-3.0-flash-fin:free": {}, "qwen/qwen3.8-27b:free": {} } }
  }
}
```
- 專案 config 直接取用 repo 的 `opencode.json` 即可（無密鑰，可 commit）

### 驗證
```bash
opencode run "回覆:OK" --model openrouter/<model>:free   # 或 ollama/qwen3:14b
```
- 回 401 → 檢查 token 檔與 `{file:}` 路徑、config 是否誤寫 `providers`
- 回 429 → 免費額度用盡（非設定問題），換模型或待 reset

### 備份
- 建 `settings/opencode/global/<machinename>/opencode.json` 存 global（token 用 `{file:}` 引用，不明碼進 git）
- 專案 config 放 `settings/opencode/project/<machinename>/opencode.json`
- commit 訊息：`<機器前綴>: 備份 opencode global/專案設定`

---

## 🔌 MCP：GitHub MCP Server（2026-09-26，MSI 已接好並實測）

本 repo 在 GitHub 上是 **PUBLIC** 且已有 Dependabot / Actions / ruleset，agent 可自己查
Dependabot alerts、secret scanning alerts、Actions 執行結果、PR 與 ruleset 狀態，
不必再靠人工轉述。

```jsonc
// ~/.config/opencode/opencode.json（global，MSI 現行實測可用）
{
  "mcp": {
    "servers": {
      "github": {
        "type": "remote",
        "url": "https://api.githubcopilot.com/mcp/",
        "oauth": false,                                   // ← 關掉 OAuth，強制走 header
        "headers": {
          "Authorization": "Bearer {file:/home/solo/.config/opencode/gh-token}",
          "X-MCP-Toolsets": "context,repos,issues,pull_requests,actions,code_security,dependabot,secret_protection,governance"
        }
      }
    }
  }
}
```

- **這是 GitHub 官方託管的遠端 server**（`github/github-mcp-server`），不需要本地裝 Docker 或執行檔。
- **token 放檔案、config 只放路徑**：`~/.config/opencode/gh-token`（`chmod 600`），
  與既有的 `cf-aig-token` 同一套做法。這份備份因此不含任何明碼密鑰，可安全 commit
  （repo 是公開的，這點很重要）。`{file:...}` 在 `headers` 裡會正常展開（已實測）。
- **不要**從 shell 跑 `opencode mcp auth`：互動流程的授權連結會被背景行程輸出吃掉。

### 🔑 憑證分工：opencode 的 token ≠ `gh` 的 token（2026-09-26 定案）

**兩者必須是兩份不同的憑證**，這是硬規則。原因見 `ROADMAP.md` §4.13 的記錄：一開始
圖省事直接沿用 `gh` CLI 的憑證，結果 opencode 和 `gh` 共用同一份命脈，且 `gh` 的 scope
遠超需要（`repo` 涵蓋帳號下所有 repo、`workflow`、`gist`）。

| | opencode（`~/.config/opencode/gh-token`） | `gh` CLI（`~/.config/gh/hosts.yml`） |
|---|---|---|
| 類型 | **classic PAT** | OAuth token |
| scope | **只有 `public_repo`** | `gist, read:org, repo, workflow` |
| 為什麼 | 這個 repo 是 public、帳號下 0 個 private repo，`public_repo` 已足夠 | `workflow` 是必要的 —— 沒有它 **push 改動 `.github/workflows/ci.yml` 的 commit 會被 GitHub 拒絕** |
| 能碰 | 只有 public repo 的讀寫 | 帳號下所有 repo（新增 private 也自動涵蓋） |

實測 `x-oauth-scopes` 回應標頭：PAT = `public_repo`；`gh` = `gist, read:org, repo, workflow`。

**`public_repo` 夠用的官方依據**（逐個端點查過，不是推測）：

| 端點 | 官方說明 |
|---|---|
| `GET .../dependabot/alerts` | 「**若只用於 public repository，token 可用 `public_repo` scope**」 |
| `GET .../actions/runs` | 「Anyone with read access to the repository can use this endpoint」 |
| `GET .../rulesets` | 「僅請求 public 資源時可無認證使用」 |

**已知取捨**：這枚 PAT 沒有 `workflow` scope，所以**改寫 workflow 檔的 PR 不能用它的 token 合併**。
實務上的答案很簡單 —— **`gh` 那枚有 `workflow` scope，合併 action 類 Dependabot PR 用 `gh`**：

```sh
gh pr merge <n> --squash --subject "msi: bump <action>"
```

**為什麼用 classic 而不是 fine-grained**：Dependabot alerts 端點的文件完全沒有提到
fine-grained PAT（2026-09-26 查證），無法在建立前確認相容性；`public_repo` 則有明文依據。
若日後想改成 fine-grained，改完務必用下面的檢查腳本重驗一遍。

### 🛠 輪換與檢查：`~/bin/gh-token-check`

```sh
gh-token-check --set    # 隱藏輸入 token → 寫檔（chmod 600）→ 立刻列出能力矩陣
gh-token-check          # 只檢查現有 token 檔
```

- **per-machine 工具，故意不放進 repo**（repo 是公開的，而且這是各機自己的環境事實）。
  要用就自己建立，或從 `settings/opencode/README.md` 這段抄一份。
- 輸出會列出七個端點的 HTTP 狀態碼，以及「解耦狀態」判定（拿 token 檔跟 `gh auth token`
  逐一比對，相同就代表又耦合了）。**只有全 `200` ＋ `✅ 解耦` 才算通過。**
- 腳本會擋下明顯不是 token 的輸入（例如把 `R=https://…` 這種變數賦值誤餵進去），
  避免寫出一份看似有效、實際全 `401` 的檔案。

> **踩過的坑**：`curl` 一律回 `000` 不是權限問題，是 **URL 沒組出來**（`$R` 未賦值拿到相對路徑）。
> `000` = 連線都沒建立；`401` = 認證失敗；`403` = 認得你但缺 scope。分清楚再查。

### ⚠️ OAuth 走不通，只能用 PAT（2026-09-26 實測）

照直覺在 `/mcps` 按 sign in 會得到：

```
Incompatible auth server: does not support dynamic client registration
```

opencode 的 OAuth 流程需要 **dynamic client registration（RFC 7591）**——讓 client 自己去
向授權伺服器註冊以取得 `client_id`。**GitHub 遠端 MCP 不支援這套**，它要求事先建好
GitHub App / OAuth App。GitHub 自己的 README 也註明「各 MCP host 需自行設定 GitHub App
或 OAuth App 才能用 OAuth」，並建議 host 支援 PAT 認證。

所以解法是 **`"oauth": false` ＋ `Authorization` header**（V1／V2 都有這個欄位）。
V2 官方文件的說法是：`oauth: false` 只在「server 完全只用 API key 或其他 header 憑證」時使用 ——
正是本例。

### ⚠️ 預設 toolset 缺了本 repo 真正要用的東西

不設 `X-MCP-Toolsets` 時，server 只給 **預設 5 個 toolset**
（`context, repos, issues, pull_requests, users`）＝ **45 個工具**。實測結果：

| 想做的事 | 預設有嗎 |
|---|---|
| 讀寫 PR / issue、檔案、commit | ✅ |
| **Dependabot alerts**（`list_dependabot_alerts`） | ❌ |
| **secret scanning alerts**（`list_secret_scanning_alerts`） | ❌ |
| **code scanning alerts** | ❌ |
| **Actions 執行結果**（`actions_list` / `get_job_logs`） | ❌ |
| **ruleset 管理**（`repository_ruleset_read`） | ❌ |

也就是說**光接上等於白接** —— 這個 repo 缺的正是上面那幾個。
加上 `X-MCP-Toolsets` 後從 45 → **57 個工具**，缺的全都補回來。

可用的 toolset 名稱（官方清單）：`context` `repos` `issues` `pull_requests` `users` `orgs`
`actions` `code_security` `code_quality` `dependabot` `secret_protection` `security_advisories`
`governance` `labels` `notifications` `projects` `discussions` `gists` `git` `stargazers`
`copilot` `copilot_issue_intents` `copilot_spaces` `github_support_docs_search`，
另有特殊值 `all`（全開）與 `default`（等於那 5 個）。
**無效名稱會被靜默忽略、不會報錯**，所以打錯字會以為有開其實沒有 —— 改完要實際驗工具數。

順帶一提：預設 toolset 裡有個 `run_secret_scanning`，它會**推一個 commit 去觸發掃描**。
加上 `secret_protection` 後該工具消失，反倒少一個地雷。

### 兩個 V1/V2 寫法差異（照 V1 寫會被靜默忽略）

1. V2 的 server 要放 **`mcp.servers.<name>`**，不能直接掛 `mcp.<name>`（V1 寫法）。
   症狀是 `opencode mcp list` 完全看不到這個 server。
2. V2 用 **`disabled`** 停用，不是 `enabled` 啟用。

### 🚨 撤銷 token：四個必須知道的事（2026-09-26 實測）

1. **`gh auth logout` 不會撤銷 token。** 官方 help 原文：*"This command does not invalidate
   authentication tokens."* 它只刪本地設定檔。**別以為 logout 就等於撤銷了。**
2. **沒有 API 可以列出或撤銷自己的 grant。** 兩個曾經存在的端點都已被 GitHub 移除
   （實測 `GET /user/applications` → `404`、`GET /applications/{client_id}/token` → `404`）。
   **網頁 UI 是唯一途徑。**
3. **路徑是 Settings → 側邊欄「Integrations」→ Applications →「Authorized OAuth Apps」分頁。**
   ⚠️ 不是 Developer settings → OAuth Apps —— 那頁是**註冊** OAuth App 的（開發者用），
   `GitHub CLI` 不會出現在那裡，所以那頁永遠顯示 "No OAuth apps"，很容易誤判成沒授權。
4. **`Revoke` 會一次撤掉該 app 的所有 token**，包含剛拿到的那枚。撤銷前要先準備好替代品，
   否則 `gh` 和 opencode 的 MCP 會同時失效。撤銷後唯一能驗證的方式是打 API 看是否變 `401`。

順帶一提：那次檢查讓我們發現帳號上還有 `Visual Studio Code` 的 OAuth 授權 —— 這就是
「定期檢查已授權 app」的實際價值。

### mbp / x570 若要加

1. 各自申請一枚 **classic PAT，只勾 `public_repo`**：
   <https://github.com/settings/tokens/new>
   （同一個帳號的 token 共用同一組 scope，所以三台機器**共用一枚 token 就行** ——
   token 檔不會跨機同步，但每台機器的 `~/.config/opencode/gh-token` 內容要一樣。）
2. 建立 `~/bin/gh-token-check`（或直接手動寫檔），把 token 寫進
   `~/.config/opencode/gh-token`、`chmod 600`，跑一次確認**全 200 ＋ `✅ 解耦`**
   （該機的 `gh` 也必須是另一枚 token）
3. 把上面整個 `mcp` 區塊併入該機的 `~/.config/opencode/opencode.json`
4. `opencode mcp list` 應顯示 `✓ github  connected`
5. 同步更新 `settings/opencode/global/<你的機器名>/opencode.json`

**這是 PAT 認證、不是 OAuth**，所以沒有「per-machine 授權瀏覽器流程」那一步。

### 換 token / 換機器時的檢查清單

- [ ] 新 token 跑 `gh-token-check`，**全 200 ＋ `✅ 解耦`**
- [ ] 有換 `gh` 的 token 嗎？有的話**必須帶 `-s workflow`**，否則之後推 `ci.yml` 會被拒
- [ ] `gh-token` 檔是否已手動更新？（`gh auth login` **不會**動它 —— config 存的是路徑不是值）
- [ ] `opencode mcp list` 是否仍 `✓ connected`？報錯就重開 opencode，
      因為 `{file:...}` 是**連線時**讀檔，撤銷／換檔後既有連線仍握著舊值
- [ ] 舊 token 真的死了嗎？（`gh` 那枚：打 API 看是否 `401`；PAT 那枚：Settings → Tokens 手動砍）

---

## 🔄 更新規則（建議）

- **每次調整 global / 專案 config 或 token 設定**後，同步更新對應機器的備份
- token 輪換（重新產生）後，更新 token 檔／環境變數，並在 commit 訊息註記輪換時間