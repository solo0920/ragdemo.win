# OpenCode 設定備份 — 總說明

> 本目錄統一備份各台機器的 opencode 設定（global config + 專案 config + 設定方法文件）。
> 目的：換機 / 重灌 / 除錯時可快速還原，並確保三台機器（MSI、MBP、x570）使用一致的
> Cloudflare AI Gateway 認證方式與 Ollama 端點設定。

---

## 📁 目錄結構（每台機器一個子目錄）

```
settings/opencode/
├── README.md            ← 本文件（先讀這個）
├── msi/                 ← MSI（已完成備份）
│   ├── global.opencode.json      # global config（openrouter → CF gateway）
│   ├── project.opencode.json     # 專案 config（ollama → x570）
│   └── CF-AIG-TOKEN-ENV.md       # token 環境變數設定方法＋疑難排解
├── mbp/                 ← MBP（待備份）
│   └── （照 msi/ 同樣建立）
└── x570/                ← x570（待備份）
    └── （照 msi/ 同樣建立）
```

---

## ✅ 給 MBP、x570 的備份任務（照做）

### 步驟 1：先讀完這份文件
- `settings/opencode/msi/CF-AIG-TOKEN-ENV.md`
  - 了解 token 用「環境變數」或「檔案引用」`{file:...}` 的方式（建議統一用 `{file:}`，最穩健、不依賴 shell）
  - 了解 401 / 429 / Model unavailable 的排解法

### 步驟 2：建立自己的備份目錄並複製設定
```bash
# 在各自的機器上執行
mkdir -p settings/opencode/mbp      # MBP 用
mkdir -p settings/opencode/x570     # x570 用

# 備份 global config
cp ~/.config/opencode/opencode.json settings/opencode/mbp/global.opencode.json

# 備份專案 config（若該機器也有 ragdemo.win 專案）
cp <專案路徑>/opencode.json settings/opencode/mbp/project.opencode.json

# 把 token 設定方法文件複製到自己的目錄
cp settings/opencode/msi/CF-AIG-TOKEN-ENV.md settings/opencode/mbp/
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
git add settings/opencode/<你的機器名>/
git commit -m "opencode: 備份 <機器名> 設定"
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
| **MSI** | `msi` | opencode 主要開發機（Windows + WSL） | ✅ 已完成（`msi/`） |
| **MBP** | `mbp` | macOS 開發機 | ⏳ 待備份（`mbp/`） |
| **x570** | `x570.tailfe3f3d.ts.net` | Ollama 模型伺服器（qwen3-coder 等）+ 開發機 | ⏳ 待備份（`x570/`） |

> x570 同時是 Ollama server（`http://x570:11434/v1`，跑 `qwen3-coder:latest`、
> `qwen2.5-coder:14b`、`qwen3:14b`、embedding/reranker models），各機器的專案 config
> 都指向它，因此 x570 的設定也必須留檔。

---

## 🔄 更新規則（建議）

- **每次調整 global / 專案 config 或 token 設定**後，同步更新對應機器的備份
- token 輪換（重新產生）後，更新 token 檔／環境變數，並在 commit 訊息註記輪換時間