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

  function auth() {
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

<main style="max-width:980px;margin:24px auto;font-family:system-ui,sans-serif;color:#1a1a1a;">
  <a href="/" style="font-size:14px;">← 回到查詢</a>
  <h1 style="font-size:20px;">題庫管理</h1>
  <p style="color:#666;font-size:13px;">
    命中優先序：<strong>使用者自定題庫</strong>（你的覆寫）→ 內建規則題庫（法名＋計數/日期/主管機關等）→ JEV 產出驗證 → LLM。
    在查詢頁看到爛答案時，把問題貼到「關鍵字／問題」並寫下正確答案，下次該問題就直接回覆、不再進 LLM。
  </p>

  {#if error}<p style="color:#c0392b;background:#fdecea;padding:8px 10px;border-radius:6px;">{error}</p>{/if}
  {#if notice}<p style="color:#1e7e34;background:#eafaf1;padding:8px 10px;border-radius:6px;">{notice}</p>{/if}

  <!-- 管理 token -->
  <fieldset style="margin:14px 0;border:1px solid #ccc;border-radius:8px;padding:10px 12px;">
    <legend style="font-size:13px;">管理 token（寫入才需要，存在瀏覽器本機）</legend>
    <input type="password" bind:value={token} placeholder="ADMIN_TOKEN"
           style="padding:6px;width:60%;box-sizing:border-box;" />
    <button onclick={saveToken} style="margin-left:8px;padding:6px 14px;">存入</button>
  </fieldset>

  <!-- 新增表單 -->
  <fieldset style="margin:14px 0;border:1px solid #ccc;border-radius:8px;padding:12px;">
    <legend style="font-size:13px;">新增題庫規則</legend>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px;">
      <select bind:value={kind} style="padding:6px;">
        <option value="contains">包含關鍵字</option>
        <option value="regex">正規表示式</option>
        <option value="exact">問題完全相同</option>
      </select>
      <span style="font-size:13px;color:#666;">限定法名（可不填）</span>
      <input bind:value={law} placeholder="例：證券交易法" style="padding:6px;width:200px;" />
    </div>
    <input bind:value={match} placeholder="關鍵字／問題樣本（{kindDesc[kind]}匹配，命中即直接回覆）"
           style="width:100%;padding:8px;box-sizing:border-box;margin-bottom:8px;" />
    <textarea bind:value={answer} rows="2" placeholder="正確答案（直接回給使用者，不進 LLM）"
              style="width:100%;padding:8px;box-sizing:border-box;margin-bottom:8px;"></textarea>
    <div style="display:flex;gap:8px;align-items:center;">
      <input bind:value={note} placeholder="備註（為何覆寫）" style="flex:1;padding:6px;" />
      <button onclick={addRule} style="padding:8px 18px;">新增</button>
    </div>
  </fieldset>

  <!-- 使用者規則表 -->
  <h2 style="font-size:15px;margin-top:4px;">使用者自定規則（{counts.enabled}/{counts.total} 啟用）</h2>
  {#if loading}
    <p style="color:#888;">載入中…</p>
  {:else if userRules.length === 0}
    <p style="color:#888;">尚未加入規則。試用：在查詢頁問出爛答案後回報這裡。</p>
  {:else}
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead>
        <tr style="text-align:left;">
          <th style="padding:6px;border-bottom:1px solid #ddd;">類型</th>
          <th style="padding:6px;border-bottom:1px solid #ddd;">匹配（問題）</th>
          <th style="padding:6px;border-bottom:1px solid #ddd;">限定法</th>
          <th style="padding:6px;border-bottom:1px solid #ddd;">答案</th>
          <th style="padding:6px;border-bottom:1px solid #ddd;">備註</th>
          <th style="padding:6px;border-bottom:1px solid #ddd;">狀態</th>
          <th style="padding:6px;border-bottom:1px solid #ddd;"></th>
        </tr>
      </thead>
      <tbody>
        {#each userRules as r (r.id)}
          <tr style="border-bottom:1px solid #eee;">
            <td style="padding:6px;">{kindDesc[r.kind] ?? r.kind}</td>
            <td style="padding:6px;max-width:220px;word-break:break-all;">{r.match}</td>
            <td style="padding:6px;">{r.law || '—'}</td>
            <td style="padding:6px;max-width:260px;">{r.answer}</td>
            <td style="padding:6px;max-width:160px;color:#777;">{r.note || ''}</td>
            <td style="padding:6px;">{r.enabled ? '啟用' : '停用'}</td>
            <td style="padding:6px;white-space:nowrap;">
              <button onclick={() => toggle(r.id)} style="padding:4px 10px;margin-right:4px;">{r.enabled ? '停用' : '啟用'}</button>
              <button onclick={() => del(r.id)} style="padding:4px 10px;">刪除</button>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

  <!-- 內建題庫 -->
  <h2 style="font-size:15px;">內建規則題庫（程式內建，這裡僅供檢視）</h2>
  <table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead>
      <tr style="text-align:left;">
        <th style="padding:6px;border-bottom:1px solid #ddd;">名稱</th>
        <th style="padding:6px;border-bottom:1px solid #ddd;">觸發關鍵字</th>
        <th style="padding:6px;border-bottom:1px solid #ddd;">說明</th>
        <th style="padding:6px;border-bottom:1px solid #ddd;">範例答案（證交法）</th>
      </tr>
    </thead>
    <tbody>
      {#each builtins as b}
        <tr style="border-bottom:1px solid #eee;">
          <td style="padding:6px;"><code>{b.id}</code></td>
          <td style="padding:6px;max-width:200px;word-break:break-all;"><code>{b.pattern}</code></td>
          <td style="padding:6px;max-width:280px;">{b.label}</td>
          <td style="padding:6px;max-width:260px;color:#555;">{b.sample_answer}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</main>