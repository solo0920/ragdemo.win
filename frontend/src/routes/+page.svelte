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
  let showInfo = false;
  let status = null;
  let statusLoading = false;

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
    await loadStatus();
  }

  async function loadStatus() {
    statusLoading = true;
    try {
      const r = await fetch(api('/status'));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      status = await r.json();
    } catch (_) {
      status = { ok: false, host: '-', log: null };
    } finally {
      statusLoading = false;
    }
  }

  async function toggleInfo() {
    showInfo = !showInfo;
    if (showInfo && !result) loadStatus();
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
    if (!user) {
      error = '請先登入 Google 才能查詢';
      return;
    }
    if (!question.trim()) return;
    loading = true;
    error = '';
    result = null;
    try {
      const r = await fetch(`/api/query?backend=${backendId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      result = await r.json();
      if (!r.ok) {
        let msg = '後端錯誤 ' + r.status;
        if (result?.detail) msg += '：' + result.detail;
        throw new Error(msg);
      }
      if (!result?.ok) throw new Error(result.detail || '查詢失敗');
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  const HOST_IPS = { x570: '100.119.83.111', mbp: '100.64.121.9', msi: '100.65.68.106' };

  function provName(p) {
    if (!p) return '-';
    const detail = p.model ? p.model : (p.url || '').replace(/^https?:\/\//, '');
    return `${p.host} ${detail}`;
  }

  function hostRows(r) {
    const ok = (id) => r?.log?.[id] === '連線成功';
    return ['x570', 'mbp', 'msi'].map((id) => ({
      k: id,
      ip: HOST_IPS[id],
      v: ok(id) ? '✅' : '❌',
    }));
  }

  function srcRows(r) {
    return [
      { k: '檢索後端', v: r.host ?? '-' },
      { k: 'Qdrant 檢索', v: provName(r.src?.qdrant) },
      { k: 'LLM 生成', v: provName(r.src?.llm) },
    ];
  }

  if (typeof localStorage !== 'undefined') {
    const saved = localStorage.getItem('ragdemo-backend');
    if (saved && BACKENDS.some((x) => x.id === saved)) backendId = saved;
  }
  checkHealth();
  loadStatus();
</script>

<main>
  <section class="head">
    <h1>法規判決 RAG</h1>
    <section class="auth">
      {#if user}
        <a href="/auth/logout" class="btn">登出</a>
      {:else}
        <a href="/auth/login" class="btn">使用 Google 登入</a>
      {/if}
    </section>
  </section>

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
      <span class="health ok">⦿ {health.host_id}｜{health.llm} ｜ {health.collection} ｜ {health.ms}ms</span>
    {:else if healthError}
      <span class="health bad">✗ {healthError}</span>
    {/if}
    <div class="sw-right">
      <button class="info-btn" onclick={toggleInfo} aria-expanded={showInfo}>
        連線詳細 {showInfo ? '▾' : '▸'}
      </button>
      {#if showInfo}
        <div class="info-pop">
          <div class="tip"></div>
          {#if user}<p class="pop-user">已登入：{user.email}</p>{/if}
          <h2>連線與來源</h2>
          <h3 class="pop-sub">主機狀態{result ? '' : '（即時探測）'}</h3>
          {#if statusLoading && !status}
            <p class="muted">探測中…</p>
          {:else}
            <table>
              <tbody>
                {#each hostRows(result ?? status) as row}
                  <tr><td>{row.k}</td><td>{row.ip}</td><td>{row.v}</td></tr>
                {/each}
              </tbody>
            </table>
          {/if}
          <h3 class="pop-sub">本次檢索方式</h3>
          {#if result}
            <table>
              <tbody>
                {#each srcRows(result) as row}
                  <tr><td>{row.k}</td><td>{row.v}</td></tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <p class="muted">尚未送出查詢——送出後顯示實際檢索／LLM 來源。</p>
          {/if}
        </div>
      {/if}
    </div>
  </section>

  <div class="ask-wrap">
    <textarea
      bind:value={question}
      rows="1"
      placeholder="輸入法律問題…（Enter 送出，Shift+Enter 換行）"
      disabled={!user}
      onkeydown={(e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          ask();
        }
      }}
    ></textarea>
    <button
      class="send"
      onclick={ask}
      disabled={loading || !question.trim() || !user}
      aria-label="送出查詢"
      title="送出（Enter）"
    >
      <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
    </button>
  </div>
  {#if !user}
    <p class="hint">尚未登入，請先 <a href="/auth/login" rel="external">使用 Google 登入</a> 後才能查詢。</p>
  {/if}
  {#if error}<p class="err">{error}</p>{/if}
  {#if result}
    {#if result.ok}
      {#if result.no_match}
        <h2>沒有符合比對的法條</h2>
        <p class="ans">{result.answer}</p>
        <p class="hint">信心：{result.confidence}（{result.relevance}）— 已跳過 LLM，不進行臆測。</p>
      {:else}
        <h2>回答（{result.host}）</h2>
        <p class="ans">{result.answer.replace(/^[a-z0-9]+: /, '')}</p>
        <p class="hint">信心：{result.confidence}（{result.relevance}）</p>
        <h2>引用（top {result.hits.length}）</h2>
        <ol>
          {#each result.hits as h}
            <li>
              <span class="sc">{h.score.toFixed(2)}</span>
              ｜{h.law_name}{h.art}
              ｜{h.item}
              <br /><span class="tx">{h.payload.text.slice(0, 200)}…</span>
            </li>
          {/each}
        </ol>
      {/if}
    {/if}
  {/if}
</main>

<style>
  main { max-width: 800px; margin: 2rem auto; padding: 0 1rem; font-family: sans-serif; }
  textarea {
    flex: 1 1 0%; min-width: 0; resize: none; height: 2.4rem;
    border: none; outline: none; background: transparent;
    padding: 0 0.25rem; line-height: 2.4rem; text-align: left;
    font-size: 1rem; font-family: inherit;
  }
  .ask-wrap {
    display: flex; align-items: center; gap: 0.4rem;
    padding: 0.4rem 0.5rem 0.4rem 0.75rem;
    border: 1px solid #d7d7d7; border-radius: 1rem;
    background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.07);
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .ask-wrap:focus-within {
    border-color: #1e90ff; box-shadow: 0 0 0 3px rgba(30, 144, 255, 0.12);
  }
  .send {
    flex-shrink: 0; width: 2.3rem; height: 2.3rem; padding: 0;
    display: flex; align-items: center; justify-content: center;
    border: none; border-radius: 0.6rem;
    background: #1e90ff; color: #fff;
    cursor: pointer; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
    transition: background 0.15s, opacity 0.15s;
  }
  .send:hover:not(:disabled) { background: #1c86ee; }
  .send:disabled { opacity: 0.45; cursor: default; box-shadow: none; }
  .ans { white-space: pre-wrap; }
  table { border-collapse: collapse; margin: 0.5rem 0 1rem; }
  td { border: 1px solid #999; padding: 0.25rem 0.75rem; text-align: left; }
  .sc { font-weight: bold; color: #b01; }
  .tx { color: #444; }
  .pop-sub { font-size: 0.9rem; margin: 0.6rem 0 0.2rem; }
  .err { color: red; }
  .muted { color: #666; font-size: 0.8rem; margin: 0.25rem 0; }
  .hint { color: #666; font-size: 0.85rem; margin: 0.25rem 0; }
  textarea:disabled { background: transparent; }
  .ask-wrap:has(textarea:disabled) { opacity: 0.65; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
  .head h1 { margin: 0.5rem 0; }
  .auth { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; white-space: nowrap; }
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
  .sw-right { margin-left: auto; position: relative; display: flex; }
  .info-btn {
    border: 1px solid #888; background: #fff; border-radius: 6px;
    padding: 0.25rem 0.75rem; cursor: pointer; font-size: 0.85rem;
  }
  .info-btn:hover { background: #f0f0f0; }
  .info-pop {
    position: absolute; right: 0; top: calc(100% + 10px); z-index: 20;
    background: #fff; border: 1px solid #ccc; border-radius: 8px;
    padding: 0.6rem 0.9rem; box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
    min-width: 260px;
  }
  .info-pop h2 { font-size: 1rem; margin: 0 0 0.4rem; }
  .pop-user { font-size: 0.8rem; color: #555; margin: 0 0 0.4rem; padding-bottom: 0.4rem; border-bottom: 1px dashed #ccc; }
  .info-pop table { margin: 0; }
  .info-pop .tip {
    position: absolute; top: -6px; right: 18px; width: 10px; height: 10px;
    background: #fff; border-left: 1px solid #ccc; border-top: 1px solid #ccc;
    transform: rotate(45deg);
  }
</style>