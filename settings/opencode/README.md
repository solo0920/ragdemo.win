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
│   └── x570/opencode.json       # x570（✅ 已完成）
└── project/              ← 專案 config（ollama → x570），按機器分
    ├── msi/opencode.json        # MSI（✅ 已完成）
    ├── mbp/opencode.json        # MBP（✅ 已完成）
    └── x570/opencode.json       # x570（✅ 已完成）
```

> **global 與 project 刻意分層**：global 是 `~/.config/opencode/opencode.json`
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
cp ~/.config/opencode/opencode.json settings/opencode/global/mbp/opencode.json

# 備份專案 config（來源：各專案根目錄；若該機器也有 ragdemo.win 或其他專案）
cp <專案路徑>/opencode.json settings/opencode/project/mbp/opencode.json
```

### 步驟 3：確認 token 在該機器上可用
- 若用 `{file:}`：把 token 寫到 `~/.config/opencode/cf-aig-token`（chmod 600），並把 config 的
  `{env:CF_AIG_TOKEN}` 改為 `{file:/home/<user>/.config/opencode/cf-aig-token}`（路徑隨機器調整）
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

### 專案 config（各專案根目錄的 `opencode.json`，已含 ollama→x570 與 openrouter 模型覆寫）
```json
{
  "provider": {
    "ollama": { "options": { "baseURL": "http://x570:11434/v1" }, "models": { ... } },
    "openrouter": { "models": { "<model>:free": {} } }
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

## 🔄 更新規則（建議）

- **每次調整 global / 專案 config 或 token 設定**後，同步更新對應機器的備份
- token 輪換（重新產生）後，更新 token 檔／環境變數，並在 commit 訊息註記輪換時間