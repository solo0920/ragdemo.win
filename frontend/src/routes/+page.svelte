<script>
  import { onMount } from 'svelte';

  const BACKENDS = [
    { id: 'auto', label: '自動', base: '' },
    { id: 'x570', label: 'x570', base: 'https://api-x570.ragdemo.win' },
    { id: 'mbp', label: 'mbp', base: 'https://api-mbp.ragdemo.win' },
    { id: 'msi', label: 'msi', base: 'https://api-msi.ragdemo.win' },
  ];

  let question = '';
  let loading = false;
  let result = null;
  let error = '';
  let backendId = 'auto';
  let health = null;
  let healthLoading = false;
  let healthError = '';
  let user = null;

  onMount(async () => {
    try {
      const r = await fetch('/auth/me');
      const d = await r.json();
      if (d.ok) user = d.user;
    } catch (_) {}
  });

  function base() {
    const b = BACKENDS.find((x) => x.id === backendId);
    return b ? b.base : '';
  }

  function api(path) {
    const b = base();
    return b ? b + path : '/api' + path;
  }

  async function switchBackend(id) {
    backendId = id;
    localStorage.setItem('ragdemo-backend', id);
    result = null;
    error = '';
    await checkHealth();
  }

  async function checkHealth() {
    healthLoading = true;
    healthError = '';
    health = null;
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 5000);
      const t0 = performance.now();
      const r = await fetch(api('/health'), { signal: ctrl.signal });
      clearTimeout(timer);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const ms = Math.round(performance.now() - t0);
      health = { ...(await r.json()), ms };
    } catch (e) {
      healthError = e.name === 'AbortError' ? '連線逾時（5s）' : e.message;
    } finally {
      healthLoading = false;
    }
  }

  async function ask() {
    loading = true;
    error = '';
    result = null;
    try {
      const r = await fetch(api('/query'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      if (!r.ok) {
        let msg = '後端錯誤 ' + r.status;
        try { msg += '：' + ((await r.json()).detail || ''); } catch (_) {}
        throw new Error(msg);
      }
      result = await r.json();
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  if (typeof localStorage !== 'undefined') {
    const saved = localStorage.getItem('ragdemo-backend');
    if (saved && BACKENDS.some((x) => x.id === saved)) backendId = saved;
  }
  checkHealth();
</script>

<main>
  <section class="auth">
    {#if user}
      <span class="user">已登入：{user.email}</span>
      <a href="/auth/logout" class="btn">登出</a>
    {:else}
      <a href="/auth/login" class="btn">使用 Google 登入</a>
    {/if}
  </section>

  <h1>法規判決 RAG</h1>

  <section class="switcher">
    <span class="sw-label">後端：</span>
    {#each BACKENDS as b}
      <button
        class:active={backendId === b.id}
        onclick={() => switchBackend(b.id)}>
        {b.label}
      </button>
    {/each}
    {#if healthLoading}
      <span class="health">連線中…</span>
    {:else if health}
      <span class="health ok">⦿ {health.llm} ｜ {health.collection} ｜ {health.ms}ms</span>
    {:else if healthError}
      <span class="health bad">✗ {healthError}</span>
    {/if}
  </section>

  <textarea bind:value={question} rows="3" placeholder="輸入法律問題…"></textarea>
  <button onclick={ask} disabled={loading || !question.trim()}>
    {loading ? '檢索生成中…' : '送出'}
  </button>
  {#if error}<p class="err">{error}</p>{/if}
  {#if result}
    <h2>回答（{BACKENDS.find((x) => x.id === backendId)?.label}）</h2>
    <p class="ans">{result.answer}</p>
    <h2>引用（top {result.hits.length}）</h2>
    <ol>
      {#each result.hits as h}
        <li>{h.payload.case_no}｜{h.payload.law}｜{h.score.toFixed(3)}<br />{h.payload.text.slice(0, 200)}…</li>
      {/each}
    </ol>
  {/if}
</main>

<style>
  main { max-width: 800px; margin: 2rem auto; padding: 0 1rem; font-family: sans-serif; }
  textarea { width: 100%; }
  .ans { white-space: pre-wrap; }
  .err { color: red; }
  .auth { display: flex; align-items: center; justify-content: flex-end; gap: 0.5rem; margin-bottom: 0.5rem; font-size: 0.85rem; }
  .btn {
    border: 1px solid #888; background: #fff; border-radius: 6px;
    padding: 0.25rem 0.75rem; cursor: pointer; text-decoration: none; color: #222;
  }
  .btn:hover { background: #f0f0f0; }
  .switcher { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .sw-label { font-weight: bold; }
  .switcher button {
    border: 1px solid #888; background: #fff; border-radius: 6px;
    padding: 0.25rem 0.75rem; cursor: pointer;
  }
  .switcher button.active { background: #1e90ff; color: #fff; border-color: #1e90ff; }
  .health { font-size: 0.85rem; }
  .health.ok { color: #2e7d32; }
  .health.bad { color: #c62828; }
</style>