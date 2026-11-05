# ragdemo.win

`ragdemo.win` 的入口網站：以 Apple 官網的留白、白灰黑三段交替區塊、SF Pro 字體節奏與單一藍色行動色為靈感，介紹兩個本機優先的數位工具，並導向各自的子網站。

- **Prompter** — 區塊式 Markdown 提示詞編輯器：[prompter.ragdemo.win](https://prompter.ragdemo.win/)
- **分析師備考站** — 證券投資分析人員考試備考網站：[analysis.ragdemo.win](https://analysis.ragdemo.win/)

兩個工具都是純靜態、零後端、無追蹤的專案。這個頁面同樣不使用框架、不載入外部字型或 CDN、不執行追蹤腳本。

## 專案結構

```text
.
├── public/                    # Cloudflare Pages 的輸出目錄
│   ├── index.html             # 首頁
│   ├── 404.html               # 自訂 404
│   ├── favicon.svg
│   ├── apple-touch-icon.png
│   ├── og-image.png           # 1200 × 630 社群分享圖
│   ├── robots.txt
│   ├── sitemap.xml
│   ├── _headers               # 安全性標頭與快取規則
│   ├── _redirects             # /prompter、/analysis、GitHub 捷徑
│   └── assets/
│       ├── css/main.css
│       ├── js/main.js
│       └── images/            # 產品截圖與 App 圖示
├── wrangler.toml              # Pages 專案與輸出目錄設定
└── README.md
```

## 本機預覽

不需要安裝任何套件，兩種方式都可以：

**直接開啟**：雙擊 `public/index.html`，或將它拖進瀏覽器。首頁使用相對路徑，CSS、JavaScript 與圖片會一併載入；`_headers`、`_redirects` 與自訂 404 則需要透過伺服器才會生效。

**靜態伺服器**（可完整測試標頭、導轉與 404）：

```bash
python3 -m http.server 8788 --directory public
```

開啟 <http://localhost:8788/>。

## 部署到 Cloudflare Pages

正式網站：<https://ragdemo.win/>（Cloudflare Pages 專案 `ragdemo-win`）

### 方式 A：Git 整合（推薦）

1. 將此資料夾推到 GitHub 或 GitLab。
2. Cloudflare Dashboard → **Workers & Pages** → **Create** → **Pages** → **Connect to Git**。
3. 選擇 repository，設定：
   - **Framework preset**：`None`
   - **Build command**：留空
   - **Build output directory**：`public`
   - **Root directory**：`/`（預設）
4. 部署後到 **Custom domains** 加入 `ragdemo.win` 與 `www.ragdemo.win`。若兩者都要保留，建議在 Cloudflare 建立 Redirect Rule，將 `www.ragdemo.win/*` 以 301 導向 `https://ragdemo.win/:splat`，避免重複內容。

因為沒有建置步驟，Cloudflare 會直接發佈 `public/` 內容。

### 方式 B：Wrangler 直接上傳

```bash
npx wrangler login
npx wrangler pages deploy public --project-name ragdemo-win
```

若使用專案內的 `wrangler.toml`，輸出目錄已設為 `public`。

### 子網域

`prompter.ragdemo.win` 與 `analysis.ragdemo.win` 是各自獨立的 Cloudflare Pages 專案，請在對應專案的 **Custom domains** 中綁定，不需要放在這個 repository。

> 注意：Prompter 的正式子網域是 **prompter**.ragdemo.win（不是 promper）。本網站所有連往 Prompter 的連結都使用 `prompter.ragdemo.win`。`public/_redirects` 另外保留了 `/promper` 路徑的導轉，方便打錯字時仍能抵達。

## 設計與實作備註

- **視覺**：白、`#e8e8ed` 中間灰與 `#272729` 深色區塊三段循環；`#0066cc` 為主要行動色，首屏 CTA 以墨黑提高對比；系統 SF Pro / PingFang TC 字體堆疊、膠囊按鈕、產品截圖僅使用一層柔和陰影。
- **互動**：原生 `<dialog>` 行動版選單、`IntersectionObserver` 進場動畫、`prefers-reduced-motion` 支援、固定導覽列捲動細線。全部由 `assets/js/main.js` 處理。
- **無外部請求**：沒有 Google Fonts、沒有 CDN、沒有分析工具；CSP 的 `connect-src` 限制為 `'self'`，不允許連線到任何外部來源。
- **CSP**：`public/_headers` 內的 `script-src` 包含首頁 JSON-LD 的 SHA-256 hash。若修改 `index.html` 的 JSON-LD，請重新計算並更新 hash：

  ```bash
  node -e 'const fs=require("fs"),c=require("crypto");const h=fs.readFileSync("public/index.html","utf8");const m=h.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/);console.log(c.createHash("sha256").update(m[1]).digest("base64"));'
  ```

- **產品截圖**：`public/assets/images/prompter-editor*.webp` 與 `analysis-home*.webp` 是從兩個線上子網站擷取的 1600 × 1000 畫面，並提供 800／1200／1600 寬度給 `srcset` 選用；更新時替換同名檔案即可，版面已預留 16:10 比例。
- **社群分享圖**：`public/og-image.png` 為 1200 × 630，已套用於 Open Graph 與 Twitter Card。

## 授權

入口網站的程式碼與設計以 MIT 精神提供。Prompter 與分析師備考站的內容、程式碼與第三方素材，依各自 repository 的授權與來源說明為準。
