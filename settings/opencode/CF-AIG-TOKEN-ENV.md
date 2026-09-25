# Cloudflare AI Gateway Token — 環境變數設定方法

> 適用機器：MSI（本機）
> 目的：讓 opencode global config 透過「環境變數」取得 Cloudflare AI Gateway token，
> 取代目前的 `{file:...}` 檔案引用方式（兩種方式擇一）。

---

## 1. 兩種取得 token 的方式比較

| 方式 | 設定寫法 | 優點 | 缺點 |
|---|---|---|---|
| **環境變數** | `"cf-aig-authorization": "Bearer {env:CF_AIG_TOKEN}"` | 不用存 token 檔 | **必須**在啟動 opencode server 的 shell 中看得到該變數，否則展開為空 → 401 |
| **檔案引用**（目前使用） | `"cf-aig-authorization": "Bearer {file:/home/solo/.config/opencode/cf-aig-token}"` | 不依賴 shell 環境，最穩健 | 需管理一個明碼 token 檔（建議 chmod 600） |

> ⚠️ 重要教訓（先前實際踩過）：
> 原本用 `{env:CF_AIG_TOKEN}` 時，`CF_AIG_TOKEN` 只 export 在 `~/.zshrc`，
> 但 opencode service 是由 **bash** 終端啟動的（bash 不讀 .zshrc），
> 導致 server 環境沒有該變數 → header 展開成 `Bearer`（空）→ 401。
> 所以若要用環境變數方案，**必須確保任何啟動方式都有該變數**（見 §3）。

---

## 2. 設定步驟（環境變數方案）

### 2.1 取得 token
1. 登入 [Cloudflare Dashboard](https://dash.cloudflare.com/)
2. **Workers & Pages → AI Gateway → 你的 gateway**（slug：`cloudflaregateway`）
3. **Settings → Authenticated Gateway → Manage Dynamically** 建立 / 複製 gateway token
4. token 格式類似：`cfut_...`（53 字元）

### 2.2 設定環境變數（三個位置擇一即可）

一起設定（不同 shell 都保險）：

**~/.zshrc**（zsh 互動 shell）
```bash
export CF_AIG_TOKEN="cfut_你的_token_這裡"
```

**~/.bashrc**（bash 互動 shell）
```bash
export CF_AIG_TOKEN="cfut_你的_token_這裡"
```

**/etc/environment**（全系統，需 sudo；WSL 啟動即載入）
```bash
CF_AIG_TOKEN="cfut_你的_token_這裡"
```

### 2.3 修改 opencode global config
檔案：`~/.config/opencode/opencode.json`
> ⚠️ **寫法注意**：
> - top-level key 必須是**單數** `provider`（不是 `providers`！`providers` 會被靜默忽略，官方 schema 驗證）
> - `baseURL` 與 `headers` 都放在 `options` 內（headers 寫在 `options.headers`）
```json
{
  "provider": {
    "openrouter": {
      "options": {
        "baseURL": "https://gateway.ai.cloudflare.com/v1/e91bd59a03f8647cc73d2fb87e07014d/cloudflaregateway/openrouter",
        "headers": {
          "cf-aig-authorization": "Bearer {env:CF_AIG_TOKEN}"
        }
      }
    }
  }
}
```

### 2.4 重新載入環境並驗證
```bash
source ~/.zshrc            # 或重新開啟終端
echo ${#CF_AIG_TOKEN}      # 應顯示 53（變數有值）
```

### 2.5 重啟 opencode service（重要！新設定才會生效）
```bash
opencode service restart
```
> restart 會中斷目前所有 opencode 連線（含正在跑的工作階段），重開 `opencode` 即可恢復。

---

## 3. 驗證是否真的走通了

### 方法 A：直接 curl 測 gateway（最快）
```bash
curl -s https://gateway.ai.cloudflare.com/v1/e91bd59a03f8647cc73d2fb87e07014d/cloudflaregateway/openrouter/chat/completions \
  -H "Content-Type: application/json" \
  -H "cf-aig-authorization: Bearer $CF_AIG_TOKEN" \
  -d '{"model":"nvidia/nemotron-3-super-120b-a12b:free","messages":[{"role":"user","content":"回覆:OK"}],"max_tokens":10}'
```
- 有 `choices[].message.content` → ✅ 成功
- `code:401` → ❌ token 沒送對（檢查 §2.2 / §2.3）
- `code:429` → ⚠️ free model 暫時被 OpenRouter rate-limit，換個 model 或稍後再試
- `code:2009 Unauthorized` → ❌ gateway 端認證失敗

### 方法 B：opencode 端到端測試（在專案目錄內執行！）
```bash
cd /home/solo/projects/ragdemo.win
opencode run "回覆:OK" --model openrouter/nvidia/nemotron-3-super-120b-a12b:free
```
> ⚠️ 一定要在 git repo（專案）目錄下執行，否則讀不到專案/全域完整設定。

### 方法 C：確認 server 環境真的有變數
```bash
# 先找到 opencode service 的 pid
ps -eo pid,cmd | grep "opencode serve" | grep -v grep
# 檢查該 pid 環境中是否有變數
tr '\0' '\n' < /proc/<PID>/environ | grep CF_AIG_TOKEN
```
有輸出 → server 環境正確；無 → 回到 §2.2，確認啟動 opencode 的 shell 有 export。

---

## 4. 疑難排解

| 症狀 | 原因 | 解決 |
|---|---|---|
| 401、`Bearer` 空 | server 環境沒有 `CF_AIG_TOKEN` | §2.2 三個位置都設；已啟動的 server 要 restart |
| 401 `code:2009` token 電子郵件無效 | **刻意遮蔽過的 token**（敏感資料，非完整真實值） | 使用 Dashboard 產出的完整真實 token；遮蔽值勿取代真實 token |
| `/api/info` 401 每 100ms 重複 | client/server 認證握手失敗 | `opencode service restart` |
| `provider request failed HTTP 401` | header 沒送到 | 確認 config 的 headers 寫法（§2.3） |
| 429 rate limited | free model 共享額度池 | 到 CF Dashboard → Provider Keys 加 OpenRouter key（`is_byok: true`） |
| `Model unavailable` | 在非專案目錄執行 run | 到 git repo 目錄內執行 |

---

## 5. 安全性注意事項

1. **不要把 token 寫進會 commit 的檔案**（本備份目錄也一樣）
2. token 若出現在對話紀錄 / 錯誤輸出中，建議到 CF Dashboard **重新產生**並同步更新
3. `~/.config/opencode/service.json`（service 密碼）也屬敏感，勿進 git
4. 若改用「檔案引用」方案：token 檔設 `chmod 600`、勿放 repo

---

## 附：目前（2026-09-25）本機實際使用的方案

- 🔑 **token 管理**：`{file:}` 檔案引用
  - token 檔：`~/.config/opencode/cf-aig-token`（chmod 600）
  - 理由：`{env:}` 曾因 bash/zsh 環境差異導致 server 端展開失敗，`{file:}` 不依賴 shell 最穩健
- 🌐 **可用的 free models**（需經 CF gateway）：
  - `nvidia/nemotron-3-super-120b-a12b:free` ✅ 實測可用
  - `google/gemma-4-31b-it:free` ⚠️ 偶發 429
- 💻 **專案本機模型**：ollama（`http://x570:11434/v1`）`qwen3-coder:latest` 等