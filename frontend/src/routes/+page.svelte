<script>
  import { onMount } from 'svelte';

  const BACKENDS = [
    { id: 'auto', label: '自動', base: '' },
    { id: 'x570', label: 'x570', base: 'https://api-x570.ragdemo.win' },
    { id: 'mbp', label: 'mbp', base: 'https://api-mbp.ragdemo.win' },
    { id: 'msi', label: 'msi', base: 'https://api-msi.ragdemo.win' },
  ];

  let question = '';
  let ta;
  function grow() {
    if (!ta) return;
    ta.style.height = 'auto';
    const lineH = parseFloat(getComputedStyle(ta).lineHeight) || 40;
    const h = Math.min(Math.max(ta.scrollHeight, lineH), 240);
    ta.style.height = h + 'px';
    const wrap = ta.closest('.ask-wrap');
    if (wrap) wrap.style.alignItems = h > lineH + 2 ? 'flex-end' : 'center';
  }
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
  // law-update（強制更新法規版本）狀態
  let upd = null;
  let updMsg = '';
  let updBusy = false;
  let showTokenInput = false;
  let adminToken = '';
  const prefixOf = { openrouter: 'openrouter', zen: 'zen', nvidia: 'nv', gemini: 'gemini',
                     groq: 'groq', cohere: 'cohere', hf: 'hf', mistral: 'mis', ollama: 'ollama' };
  let model = '';
  let localModels = [];
  let cloudModels = [];
  let zenModels = [];
  let zenReady = false;
  let nvidiaModels = [];
  let nvidiaReady = false;
  let geminiModels = [];
  let geminiReady = false;
  let groqModels = [];
  let groqReady = false;
  let cohereModels = [];
  let cohereReady = false;
  let hfModels = [];
  let hfReady = false;
  let mistralModels = [];
  let mistralReady = false;
  let usageMap = new Map();
  let limitedMap = new Map();
  let quotaMap = new Map();
  let modelsReady = false;
  let modelOpen = false;
  let groups = [];

  onMount(async () => {
    document.addEventListener('click', (ev) => {
      if (modelOpen && !ev.target.closest('.model-drop')) modelOpen = false;
    });
    try {
      const r = await fetch('/auth/me');
      const d = await r.json();
      if (d.ok) user = d.user;
    } catch (_) {}
    loadModels();
    requestAnimationFrame(grow);
  });

  async function loadModels() {
    try {
      const r = await fetch(api('/models'));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      localModels = d.local ?? [];
      cloudModels = d.cloud ?? [];
      zenModels = d.zen ?? [];
      zenReady = !!d.zen_ready;
      nvidiaModels = d.nvidia ?? [];
      nvidiaReady = !!d.nvidia_ready;
      geminiModels = d.gemini ?? [];
      geminiReady = !!d.gemini_ready;
      groqModels = d.groq ?? [];
      groqReady = !!d.groq_ready;
      cohereModels = d.cohere ?? [];
      cohereReady = !!d.cohere_ready;
      hfModels = d.hf ?? [];
      hfReady = !!d.hf_ready;
      mistralModels = d.mistral ?? [];
      mistralReady = !!d.mistral_ready;
      const um = new Map();
      for (const u of (d.usage ?? [])) {
        const p = prefixOf[u.provider];
        if (!p) continue;
        um.set(`${p}/${u.model}`, { calls: u.calls ?? 0, tokens: u.tokens ?? 0 });
      }
      usageMap = um;
      quotaMap = new Map(Object.entries(d.quota ?? {}));
      const lm = new Map();
      for (const s of (d.limited ?? [])) lm.set(s.key, s.until);
      limitedMap = lm;
      const item = (m, label, disabled = false, key = m) => ({ value: m, label, key, disabled });
      const G = [];
      if (localModels.length) G.push({ label: '地端 ollama（隱私）', items: localModels.map((m) => item(m, m, false, `ollama/${m}`)) });
      if (cloudModels.length) G.push({ label: 'OpenRouter 閉源（速度）', items: cloudModels.map((m) => item(m, m.replace(/^openrouter\//, '').replace(/:free$/, ''))) });
      if (zenModels.length) G.push({ label: 'OpenCode Zen Free', items: zenModels.map((m) => item(m, m.replace(/^zen\//, '') + (!zenReady ? '（需 Zen key）' : ''), !zenReady)) });
      if (nvidiaModels.length) G.push({ label: 'NVIDIA NIM', items: nvidiaModels.map((m) => item(m, m.replace(/^nv\//, '') + (!nvidiaReady ? '（需 NIM key）' : ''), !nvidiaReady)) });
      if (geminiModels.length) G.push({ label: 'Google Gemini（AI Studio）', items: geminiModels.map((m) => item(m, m.replace(/^gemini\//, '') + (!geminiReady ? '（需 gateway 設定）' : ''), !geminiReady)) });
      if (groqModels.length) G.push({ label: 'Groq', items: groqModels.map((m) => item(m, m.replace(/^groq\//, '') + (!groqReady ? '（需 gateway 設定）' : ''), !groqReady)) });
      if (cohereModels.length) G.push({ label: 'Cohere', items: cohereModels.map((m) => item(m, m.replace(/^cohere\//, '') + (!cohereReady ? '（需 gateway 設定）' : ''), !cohereReady)) });
      if (hfModels.length) G.push({ label: 'Hugging Face', items: hfModels.map((m) => item(m, m.replace(/^hf\//, '') + (!hfReady ? '（需 HF token）' : ''), !hfReady)) });
      if (mistralModels.length) G.push({ label: 'Mistral', items: mistralModels.map((m) => item(m, m.replace(/^mis\//, '') + (!mistralReady ? '（需 gateway 設定）' : ''), !mistralReady)) });
      groups = G;
      modelsReady = true;
    } catch (_) {
      modelsReady = false;
    }
  }

  function base() {
    const b = BACKENDS.find((x) => x.id === backendId);
    return b ? b.base : '';
  }

  function api(path) {
    const b = base();
    return b ? b + path : '/api' + path;
  }

  function usageSuffix(key) {
    const u = usageMap.get(key);
    const prefix = key.includes('/') ? key.split('/')[0] : key;
    const prov = Object.keys(prefixOf).find((p) => prefixOf[p] === prefix);
    const quota = prov ? quotaMap.get(prov) : null;
    if (quota && quota.limit) {
      const calls = u ? u.calls : 0;
      return `${calls}/${quota.limit}`;
    }
    if (!u || (u.calls === 0 && u.tokens === 0)) return '';
    if (u.tokens === 0) return `今日 ${u.calls}次`;
    const t = u.tokens >= 1000 ? (u.tokens / 1000).toFixed(1) + 'k' : String(u.tokens);
    return `今日 ${u.calls}次/${t}`;
  }

  function isLimited(key) {
    const until = limitedMap.get(key);
    return until && until * 1000 > Date.now() ? until : null;
  }

  function isOverQuota(key) {
    const u = usageMap.get(key);
    if (!u) return false;
    const prefix = key.includes('/') ? key.split('/')[0] : key;
    const prov = Object.keys(prefixOf).find((p) => prefixOf[p] === prefix);
    const quota = prov ? quotaMap.get(prov) : null;
    return !!(quota && quota.limit && u.calls >= quota.limit);
  }

  function resetText(key) {
    const until = isLimited(key);
    if (!until) return '';
    const d = new Date(until * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `重置 ${hh}:${mm}`;
  }

  function quotaReset(key) {
    const prefix = key.includes('/') ? key.split('/')[0] : key;
    const prov = Object.keys(prefixOf).find((p) => prefixOf[p] === prefix);
    const quota = prov ? quotaMap.get(prov) : null;
    if (!quota || !quota.period) return '';
    if (isLimited(key)) return resetText(key);
    const now = new Date();
    if (quota.period === 'month') {
      const d = new Date(now.getFullYear(), now.getMonth() + 1, 1, 0, 0);
      return `重置 ${String(d.getMonth() + 1)}/1`;
    }
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0);
    return `重置 ${String(d.getHours()).padStart(2, '0')}:00`;
  }

  function modelLabel() {
    if (!model) return '';
    for (const g of groups) {
      for (const it of g.items) if (it.value === model) return it.label;
    }
    return model.replace(/^[a-z]+\//, '');
  }

  async function switchBackend(id) {
    backendId = id;
    localStorage.setItem('ragdemo-backend', id);
    result = null;
    error = '';
    await checkHealth();
    await loadStatus();
    loadModels();
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

  // 只補抓版本，不動 statusLoading —— 法規版本要靠每日 ingest 才會變，
  // 但仍希望在開彈窗時看到現況；走 loadStatus 會讓整張表閃「探測中」。
  async function refreshVersions() {
    try {
      const r = await fetch(api('/status'));
      if (!r.ok) return;
      const d = await r.json();
      if (d.versions) status = { ...(status ?? {}), versions: d.versions, law_version: d.law_version };
    } catch (_) { /* 取得不到就沿用舊值，不動版面 */ }
    loadUpd();
  }

  async function toggleInfo() {
    showInfo = !showInfo;
    if (showInfo) { refreshVersions(); }
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
        body: JSON.stringify({ question, model })
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
      grow();
    }
  }

  const HOST_IPS = { x570: '100.119.83.111', mbp: '100.64.121.9', msi: '100.65.68.106' };
  const HOST_NAMES = { x570: '主機', mbp: '加速', msi: 'demo' };

  function provName(p) {
    if (!p) return '-';
    const detail = p.model ? p.model : (p.url || '').replace(/^https?:\/\//, '');
    return `${p.host} ${detail}`;
  }

  // result（/query 的回應）只有 log，沒有 versions；只有 /status 會回 versions。
  // 所以兩邊都傳進來：連線狀態優先用 result（剛查完那次），版本用 status。
  function hostRows(r, s) {
    const log = r?.log ?? s?.log;
    const vers = s?.versions ?? r?.versions;
    const ok = (id) => log?.[id] === '連線成功';
    return ['x570', 'mbp', 'msi'].map((id) => ({
      k: id,
      role: HOST_NAMES[id],
      ip: HOST_IPS[id],
      // 官方 zip 檔名固定 ChLaw.json.zip 恆定不變，版本一律取 ChLaw.json 的 UpdateDate
      ver: vers?.[id] && vers[id] !== '-' ? vers[id] : '—',
      v: ok(id) ? '✅' : '❌',
    }));
  }

  // ── 法規版本更新 ────────────────────────────────────────────────────
  // 判斷「本機明顯較舊」：本機版本 < 三台中最新者。版本是 ISO 日期字串，
  // 字串比較即等於時間比較（_normVersion 會擋掉 '-' 這種非日期值）。
  function _normVer(v) {
    return v && /^\d{4}-\d{2}-\d{2}/.test(v) ? v.slice(0, 10) : null;
  }
  function newestVer(s) {
    const vers = s?.versions ?? {};
    const ds = ['x570', 'mbp', 'msi'].map((id) => _normVer(vers[id])).filter(Boolean);
    return ds.length ? ds.sort().at(-1) : null;
  }
  function localVer(s) {
    return _normVer((s?.versions ?? {})[status?.host]) ?? _normVer(s?.law_version?.update_date);
  }
  // 有新版可抓才顯示按鈕：
  //   - 三台裡有比本機新的 → 抓（備援機從 x570 同步；來源機重跑 ingest）
  //   - 或三台全都沒有版本（沒人跑過 ingest）→ 也值得提示按一次試試
  $: newest = newestVer(status);
  $: mine = localVer(status);
  $: updatable = !!upd?.can_update && (newest === null || (mine !== null && mine < newest));
  $: updateMsg = newest === null
    ? '三台都尚未記錄法規版本（沒人跑過每日 ingest），可按此手動觸發一次'
    : mine === null
      ? `本機沒有版本記錄；三台最新為 ${newest}`
      : `本機 ${mine}，三台最新 ${newest}`;

  async function loadUpd() {
    try {
      const r = await fetch(api('/law-update'));
      if (r.ok) upd = await r.json();
    } catch (_) { /* 拿不到就不顯示按鈕 */ }
  }

  function needToken() {
    updMsg = '需要 ADMIN_TOKEN（與題庫頁同一組）。';
    showTokenInput = true;
  }

  async function doUpdate() {
    updBusy = true; updMsg = '';
    try {
      const token = adminToken.trim();
      const r = await fetch(api('/law-update'), {
        method: 'POST',
        headers: token ? { authorization: `Bearer ${token}` } : {},
      });
      if (r.status === 401) { needToken(); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { updMsg = d.detail || `HTTP ${r.status}`; return; }
      updMsg = d.message || '已送出';
      await loadUpd();
    } catch (e) {
      updMsg = '失敗：' + (e && e.message ? e.message : e);
    } finally {
      updBusy = false;
    }
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
        <div class="model-drop">
          <button class="btn model-select" onclick={() => { modelOpen = !modelOpen; }} aria-haspopup="listbox" title="選擇查詢使用的 LLM model">
            {modelLabel() || '預設（依後端主機）'}
          </button>
          {#if modelOpen}
            <div class="model-menu" role="listbox">
              <div class="model-row group">
                <span class="m-name">預設</span><span class="m-meta">（依後端主機）</span>
              </div>
              {#each groups as g}
                <div class="model-row group">{g.label}</div>
                {#each g.items as it}
                  <div
                    class="model-row"
                    class:selected={model === it.value}
                    class:disabled={it.disabled}
                    role="option"
                    onclick={() => { if (it.disabled) return; model = it.value; modelOpen = false; }}
                  >
                    <span class="m-name">{it.label}</span>
                    <span class="m-meta" class:limited={!!isLimited(it.key) || isOverQuota(it.key)}>
                      {#if isOverQuota(it.key) || isLimited(it.key)}
                        {usageSuffix(it.key)}{quotaReset(it.key) ? '｜' + quotaReset(it.key) : ''}
                      {:else}
                        {usageSuffix(it.key)}
                      {/if}
                    </span>
                  </div>
                {/each}
              {/each}
            </div>
          {/if}
        </div>
        <a href="/rules" class="btn">題庫管理</a>
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
              <thead>
                <tr><th>主機</th><th>角色</th><th>IP</th><th>法規版本</th><th>狀態</th></tr>
              </thead>
              <tbody>
                {#each hostRows(result, status) as row}
                  <tr>
                    <td>{row.k}</td>
                    <td class="muted">{row.role}</td>
                    <td>{row.ip}</td>
                    <td title="ChLaw.json 的 UpdateDate">
                      {row.ver}
                      {#if row.k === status?.host && updatable}
                        <button
                          class="btn upd"
                          disabled={updBusy || upd?.pending || upd?.running}
                          title={updateMsg}
                          onclick={doUpdate}>更新</button>
                      {/if}
                    </td>
                    <td>{row.v}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
            {#if updatable}
              <p class="hint upd-row">
                {#if upd?.running}
                  更新執行中，請稍候（主機端 worker 執行，完成後會自動更新版本）。
                {:else if upd?.pending}
                  已排入更新，等待主機端 worker 執行。
                {:else}
                  本機法規資料較舊：{updateMsg}
                {/if}
                {#if updMsg}<span class="muted">　{updMsg}</span>{/if}
                {#if upd?.last && !upd?.pending && !upd?.running}
                  <span class="muted">
                   　上次更新：{upd.last.ok ? '成功' : '失敗'}（{upd.last.seconds}s
                    {upd.last.version ? `，版本 ${upd.last.version}` : ''}）
                  </span>
                {/if}
              </p>
              {#if showTokenInput}
                <p class="hint upd-row">
                  <input
                    type="password"
                    bind:value={adminToken}
                    placeholder="ADMIN_TOKEN"
                    autocomplete="off" />
                  <button class="btn" disabled={updBusy || !adminToken.trim()} onclick={doUpdate}>
                    送出更新
                  </button>
                </p>
              {/if}
            {/if}
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
      bind:this={ta}
      bind:value={question}
      rows="1"
      placeholder="輸入法律問題…（Enter 送出，Shift+Enter 換行）"
      disabled={!user}
      oninput={grow}
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
        <p class="trace">流程：{result.trace}</p>
        <p class="hint"><a href="/rules?q={encodeURIComponent(question)}">答案不對？把這題加入題庫 →</a></p>
      {:else}
        <h2>回答（{result.host}）</h2>
        <p class="ans">{result.answer.replace(/^[a-z0-9]+: /, '')}</p>
        <p class="hint">信心：{result.confidence}（{result.relevance}）</p>
        <p class="trace">流程：{result.trace}</p>
        <p class="hint"><a href="/rules?q={encodeURIComponent(question)}">答案有誤或想固定這題答案？加入題庫 →</a></p>
        <h2>引用（top {result.hits.length}）</h2>
        <ol>
          {#each result.hits as h}
            <li>
              <span class="sc">{#if h.law}簡介{:else if h.exact}精準{:else}{h.rel ?? '?'}%{/if}</span>
              ｜{#if h.url}<a class="lnk" href={h.url} target="_blank" rel="noreferrer">{h.law_name}{h.art} ↗</a>{:else}{h.law_name}{h.art}{/if}
              ｜{h.item}
              <br /><span class="tx">{h.payload.text.slice(0, 200)}…</span>
              <br /><span class="jud">{h.jud}</span>
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
    flex: 1 1 0%; min-width: 0; resize: none; min-height: 2.4rem;
    border: none; outline: none; background: transparent;
    padding: 0 0.25rem; line-height: 2.4rem; text-align: left;
    font-size: 1rem; font-family: inherit; overflow-y: hidden;
  }
  .ask-wrap {
    display: flex; align-items: center; gap: 0.4rem;
    padding: 0.4rem 0.5rem 0.4rem 0.75rem;
    border: 1px solid #d7d7d7; border-radius: 1rem;
    background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.07);
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .ask-wrap.multi { align-items: flex-end; } /* 備援：grow() 已用 inline style 控制 */
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
  .trace { color: #999; font-size: 0.8rem; margin: 0.25rem 0; font-family: monospace; }
  .jud { color: #a7b; font-size: 0.75rem; font-family: monospace; }
  .lnk { color: #05b; text-decoration: none; }
  .lnk:hover { text-decoration: underline; }
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
  .btn.model-select {
    font-size: inherit; font-family: inherit;
    max-width: 15rem; padding-top: 0.15rem; padding-bottom: 0.15rem;
  }
  .btn.model-select:hover { background: #fff; }
  .model-drop { position: relative; display: inline-block; }
  .model-menu {
    position: absolute; top: calc(100% + 4px); left: 0; z-index: 30;
    min-width: 18rem; max-width: 30rem; max-height: 26rem; overflow-y: auto;
    background: #fff; border: 1px solid #888; border-radius: 6px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15); padding: 0.25rem 0;
  }
  .model-row {
    display: flex; align-items: center; justify-content: space-between;
    gap: 0.75rem; padding: 0.3rem 0.75rem; cursor: pointer; font-size: 0.85rem;
  }
  .model-row:hover { background: #f0f0f0; }
  .model-row.selected { background: #e6f2ff; }
  .model-row.disabled { opacity: 0.45; cursor: not-allowed; }
  .model-row.group { font-weight: bold; color: #555; background: #fafafa; cursor: default; }
  .model-row.group:hover { background: #fafafa; }
  .m-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .m-meta { margin-left: auto; white-space: nowrap; color: #777; }
  .m-meta.limited { color: #c62828; font-weight: bold; }
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

  /* law-update：法規版本欄位後方的「更新」按鈕 */
  button.upd { margin-left: .5rem; padding: .1rem .5rem; font-size: .78rem; line-height: 1.5; vertical-align: middle; }
  button.upd:disabled { opacity: .5; cursor: not-allowed; }
  .upd-row { margin: .4rem 0 0; display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; }
  .upd-row input[type=password] { padding: .25rem .4rem; font-size: .8rem; min-width: 14rem; }
</style>
