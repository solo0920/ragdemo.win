<script lang="ts">
  import { onMount } from 'svelte';

  let builtins: any[] = [];
  let userRules: any[] = [];
  let counts = { total: 0, enabled: 0 };
  let error = '';
  let notice = '';
  let loading = true;
  let loggedIn = false;

  const kindDesc: Record<string, string> = {
    contains: '包含關鍵字',
    regex: '正規表示式',
    exact: '完全相同',
  };

  // ---- 新增表單 ----
  let kind = 'contains';
  let match = '';
  let law = '';
  let answer = '';
  let note = '';
  let token = '';

  onMount(async () => {
    token = localStorage.getItem('ragdemo-admin-token') || '';
    const q = new URLSearchParams(location.search).get('q');
    if (q) {
      match = q;
      law = '';
      note = '從查詢頁回報帶入';
    }
    await refresh();
  });

  // 回傳型別標成 HeadersInit：沒有它，TS 推不出 `{} | {authorization: string}`
  // 的聯集，svelte-check 會在三處 `...auth()` 展開處報
  // "Type '{authorization: string; ...} | {authorization?: undefined; ...}'
  //  is not assignable to type 'HeadersInit'"（2026-09-30 實測）。
  // 只標型別、不改行為。
  function auth(): HeadersInit {
    return token ? { authorization: `Bearer ${token}` } : {};
  }

  async function refresh() {
    loading = true;
    error = '';
    try {
      const r = await fetch('/api/rules');
      if (r.status === 401) {
        error = '請先登入 Google（查詢頁右上角登入）後再管理題庫。';
        return;
      }
      if (!r.ok) {
        error = '讀取題庫失敗：HTTP ' + r.status;
        return;
      }
      const d = await r.json();
      builtins = d.builtins || [];
      userRules = d.user || [];
      counts = d.counts || counts;
    } catch (e) {
      error = '讀取失敗：' + (e as Error).message;
    } finally {
      loading = false;
    }
  }

  async function saveToken() {
    localStorage.setItem('ragdemo-admin-token', token);
    notice = '已存入瀏覽器（僅本機）。';
  }

  async function addRule() {
    error = '';
    notice = '';
    if (!match.trim() || !answer.trim()) {
      error = '關鍵字／問題與答案都要填。';
      return;
    }
    try {
      const r = await fetch('/api/rules', {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...auth() },
        body: JSON.stringify({ kind, match, law, answer, note }),
      });
      const d = await r.json();
      if (!r.ok) {
        error = '新增失敗：' + (d.detail ?? d.error ?? 'HTTP ' + r.status);
        return;
      }
      notice = '已加入題庫，立即生效。';
      match = '';
      answer = '';
      note = '';
      await refresh();
    } catch (e) {
      error = '新增失敗：' + (e as Error).message;
    }
  }

  async function toggle(rid: string) {
    const r = await fetch(`/api/rules/${rid}/toggle`, {
      method: 'POST', headers: { 'content-type': 'application/json', ...auth() },
    });
    if (!r.ok) { error = '切換失敗：' + r.status; return; }
    await refresh();
  }

  async function del(rid: string) {
    if (!confirm('確定刪除這條規則？')) return;
    const r = await fetch(`/api/rules/${rid}/delete`, {
      method: 'POST', headers: { 'content-type': 'application/json', ...auth() },
    });
    if (!r.ok) { error = '刪除失敗：' + r.status; return; }
    await refresh();
  }
</script>

<svelte:head><title>題庫管理 — RagDemo</title></svelte:head>

<main>
  <a class="back" href="/">← 回到查詢</a>
  <h1>題庫管理</h1>
  <p class="lede">
    命中優先序：<strong>使用者自定題庫</strong>（你的覆寫）→ 內建規則題庫（法名＋計數/日期/主管機關等）→ JEV 產出驗證 → LLM。
    在查詢頁看到爛答案時，把問題貼到「關鍵字／問題」並寫下正確答案，下次該問題就直接回覆、不再進 LLM。
  </p>

  {#if error}<p class="alert err">{error}</p>{/if}
  {#if notice}<p class="alert ok">{notice}</p>{/if}

  <!-- 管理 token -->
  <fieldset>
    <legend>管理 token（寫入才需要，存在瀏覽器本機）</legend>
    <div class="row">
      <input type="password" bind:value={token} placeholder="ADMIN_TOKEN" />
      <button class="btn" onclick={saveToken}>存入</button>
    </div>
  </fieldset>

  <!-- 新增表單 -->
  <fieldset>
    <legend>新增題庫規則</legend>
    <div class="row">
      <select bind:value={kind}>
        <option value="contains">包含關鍵字</option>
        <option value="regex">正規表示式</option>
        <option value="exact">問題完全相同</option>
      </select>
      <span class="field-label">限定法名（可不填）</span>
      <input bind:value={law} placeholder="例：證券交易法" class="law" />
    </div>
    <input bind:value={match} class="full" placeholder="關鍵字／問題樣本（{kindDesc[kind]}匹配，命中即直接回覆）" />
    <textarea bind:value={answer} rows="2" class="full" placeholder="正確答案（直接回給使用者，不進 LLM）"></textarea>
    <div class="row">
      <input bind:value={note} placeholder="備註（為何覆寫）" class="grow" />
      <button class="btn primary" onclick={addRule}>新增</button>
    </div>
  </fieldset>

  <!-- 使用者規則表 -->
  <h2>使用者自定規則（{counts.enabled}/{counts.total} 啟用）</h2>
  {#if loading}
    <p class="muted">載入中…</p>
  {:else if userRules.length === 0}
    <p class="muted">尚未加入規則。試用：在查詢頁問出爛答案後回報這裡。</p>
  {:else}
    <table>
      <thead>
        <tr>
          <th>類型</th>
          <th>匹配（問題）</th>
          <th>限定法</th>
          <th>答案</th>
          <th>備註</th>
          <th>狀態</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {#each userRules as r (r.id)}
          <tr>
            <td>{kindDesc[r.kind] ?? r.kind}</td>
            <td class="wrap">{r.match}</td>
            <td>{r.law || '—'}</td>
            <td>{r.answer}</td>
            <td class="wrap muted">{r.note || ''}</td>
            <td>{r.enabled ? '啟用' : '停用'}</td>
            <td class="nowrap">
              <button class="btn small" onclick={() => toggle(r.id)}>{r.enabled ? '停用' : '啟用'}</button>
              <button class="btn small" onclick={() => del(r.id)}>刪除</button>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

  <!-- 內建題庫 -->
  <h2>內建規則題庫（程式內建，這裡僅供檢視）</h2>
  <!-- dark surface：這是程式內建的規格表（觸發關鍵字 / regex / 範例答案），
       屬於「產品 chrome」而非行銷文案 —— 放深色讓它跟上方使用者輸入
       資料在視覺上分屬兩層（DESIGN.md 的 cream → dark 節奏）。 -->
  <div class="dark-card">
    <table>
      <thead>
        <tr>
          <th>名稱</th>
          <th>觸發關鍵字</th>
          <th>說明</th>
          <th>範例答案（證交法）</th>
        </tr>
      </thead>
      <tbody>
        {#each builtins as b}
          <tr>
            <td><code>{b.id}</code></td>
            <td class="wrap"><code>{b.pattern}</code></td>
            <td class="wrap">{b.label}</td>
            <td class="wrap">{b.sample_answer}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</main>

<style>
  main { max-width: 980px; margin: var(--xxl) auto; padding: 0 var(--lg); }

  .back { font: var(--nav-link); color: var(--primary-active); text-decoration: none; }
  .back:active { text-decoration: underline; }

  h1 { font: var(--display-md); letter-spacing: -0.5px; margin: var(--xs) 0 var(--sm); }
  h2 { font: var(--display-sm); letter-spacing: -0.3px; margin: var(--xl) 0 var(--xs); }

  .lede { color: var(--body); margin: 0 0 var(--lg); }
  .lede strong { color: var(--ink); font-weight: 500; }
  .muted { color: var(--muted-text); font: var(--body-sm); }

  /* DESIGN.md 沒有 warning/error callout 元件，但語意色是有的。
   * 底色用 canvas（不是 surface-soft）—— error 在 canvas 上有 4.59:1，
   * 在 surface-soft 上只有 4.27:1，不達 AA。 */
  .alert {
    margin: var(--xs) 0; padding: var(--xs) var(--sm);
    background: var(--canvas); border: 1px solid var(--hairline);
    border-radius: var(--rounded-md); font: var(--body-sm);
  }
  .alert.err { color: var(--error); }
  .alert.ok { color: var(--success-text); }

  /* fieldset 當 feature-card 用（surface-card 底、無陰影） */
  fieldset {
    margin: var(--lg) 0; padding: var(--lg);
    background: var(--surface-card);
    border: none; border-radius: var(--rounded-lg);
  }
  legend { font: var(--caption); color: var(--muted-text); padding: 0 var(--xs); }

  .row { display: flex; gap: var(--xs); align-items: center; margin-bottom: var(--xs); }
  .row:last-child { margin-bottom: 0; }
  .field-label { font: var(--body-sm); color: var(--muted-text); white-space: nowrap; }
  .grow { flex: 1; }
  .law { width: 200px; }
  .full { display: block; width: 100%; margin-bottom: var(--xs); }

  /* DESIGN.md text-input：canvas 底 + hairline、40px 高、rounded-md */
  input, select, textarea {
    height: 40px; padding: 0 var(--sm);
    background: var(--canvas); color: var(--ink);
    border: 1px solid var(--hairline); border-radius: var(--rounded-md);
    font: var(--body-md);
  }
  textarea.full { height: auto; padding: var(--xs) var(--sm); line-height: 1.55; resize: vertical; }
  input::placeholder, textarea::placeholder { color: var(--muted-text); }
  input:focus, select:focus, textarea:focus {
    outline: none; border-color: var(--primary);
    box-shadow: 0 0 0 3px rgba(204, 120, 92, 0.15);
  }

  .btn {
    display: inline-flex; align-items: center; justify-content: center;
    height: 40px; padding: 0 var(--md);
    background: var(--canvas); color: var(--ink);
    border: 1px solid var(--hairline); border-radius: var(--rounded-md);
    font: var(--button); cursor: pointer;
  }
  .btn:active { background: var(--surface-soft); }
  /* 站內唯一的 coral CTA（DESIGN.md：coral 用在 primary action 上） */
  .btn.primary { background: var(--primary); color: var(--on-primary); border-color: var(--primary); }
  .btn.primary:active { background: var(--primary-active); }
  .btn.small { height: 28px; padding: 0 var(--xs); font: var(--caption); }

  /* 表格靠 hairline 分隔行，不畫格線（同查詢頁的處理） */
  table { width: 100%; border-collapse: collapse; font: var(--body-sm); }
  th {
    text-align: left; font: var(--caption); color: var(--muted-text);
    padding: var(--xs) var(--sm); border-bottom: 1px solid var(--hairline);
  }
  td {
    padding: var(--xs) var(--sm);
    border-bottom: 1px solid var(--hairline-soft);
    vertical-align: top;
  }
  .wrap { word-break: break-word; }
  .nowrap { white-space: nowrap; }

  code { font: var(--code); }

  /* dark surface card（DESIGN.md product-mockup-card-dark） */
  .dark-card {
    background: var(--surface-dark); color: var(--on-dark);
    border-radius: var(--rounded-lg); padding: var(--xl);
  }
  .dark-card th { color: var(--on-dark-soft); border-bottom-color: var(--surface-dark-elevated); }
  .dark-card td { color: var(--on-dark); border-bottom-color: var(--surface-dark-soft); }
  .dark-card code { color: var(--accent-amber); }
</style>