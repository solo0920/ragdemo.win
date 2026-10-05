<script>
  import { onMount } from 'svelte';
  import {
    citationText, fetchHostDefaults, hostUrl, makeOnce, missingModels, modelAvailability,
    modelOptions, probeClouds, saveHostDefaults, targetRows,
    verdictLabel, verdictState,
  } from '$lib/hostDefaults';

  // 引用條文的展開狀態：key = url||art（同一條可能被引用兩次）。
  //
  // ⚠️ 用 **Set 的反應性賦值**（expanded = new Set(expanded)）而不是
  // expanded.add()：Svelte 5 只會在賦值時觸發更新，原地 mutate 不會。
  let expanded = new Set();
  function toggleCite(key) {
    const next = new Set(expanded);
    if (next.has(key)) next.delete(key); else next.add(key);
    expanded = next;
  }
  const citation = citationText;

  // 後端切換器的候選名單**從 /status 回的 known 推導**，不在這裡列舉主機。
  // known 來自後端的 HOST_API_URLS；未設就是空 → 只剩「自動」，單機部署正常。
  // 舊版這裡寫死 x570/mbp/msi 三台，第 4 台部署的人必須改程式才能切過去。
  let knownHosts = {};
  const buildBackends = () => [
    { id: 'auto', label: '自動', base: '' },
    ...Object.entries(knownHosts).map(([id, url]) => ({ id, label: id, base: url })),
  ];
  // ⚠️ 這裡**同時**給初值與反應式賦值，兩個都要留。
  // 起因是 2026-09-28 的一次回歸：原本這裡只有 `$: BACKENDS = ...`，而
  // 當時 restoreBackend() / checkHealth() / loadStatus() 三個呼叫寫在 script
  // 頂層 —— 那時 instance() 主體才剛跑完、反應式區塊還沒求值，BACKENDS 是
  // undefined。瀏覽器裡 BACKENDS.some() 未捕捉地丟出、instance 中斷，樣板的
  // {#each BACKENDS} 跟著炸 → 整頁空白；SSR 那邊錯誤字串則被燒進 HTML。
  // 那三個呼叫後來搬進了 onMount（見上方），但初值仍保留：`base()` 與樣板都會
  // 讀 BACKENDS，讓它不依賴反應式語句的求值時機，才不會再出現同類問題。
  let BACKENDS = buildBackends();
  // ⚠️ `knownHosts` **必須出現在這行裡**，不能只躲在 buildBackends() 內部。
  //   Svelte 的依賴分析只掃反應式語句本身出現的識別字：`buildBackends()` 讀了
  //   knownHosts，但那是函式主體，編譯器看不到 → 於是這條語句被認為沒有任何
  //   依賴，knownHosts 進來之後**永遠不重算**。
  //   症狀（2026-10-05 實測，x570 端／headless chromium）：/status 已經回來、
  //   「連線詳細」面板也列出三台，但切換器永遠只有「自動」一項 —— 讀者看到的
  //   是「面板有三台、卻沒得切」。舊版（按鈕組）與新版（<select>）都一樣，
  //   所以這不是改版造成的，是原本就壞、只是沒人拿「面板有、選擇器沒有」對照過。
  //   `void knownHosts` 是不求值只建立依賴的最小寫法；靠 buildBackends() 的
  //   副作用回傳值來帶是隱晦且脆弱的。
  $: BACKENDS = (void knownHosts, buildBackends());

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

  // ── 「設定」對話框（每台主機的預設模型）──────────────────────────────
  // 邏輯在 $lib/hostDefaults.ts，不在這裡 —— 那樣才測得到「連不上時顯示什麼」。
  let showSettings = false;
  let settingHosts = [];
  let hostPicked = {};
  let settingsLoading = false;
  let settingsSaving = false;
  let settingsMsg = '';
  let settingsErr = '';

  // 開啟對話框時要問的對象：目前服務的那台（/status 的 host）＋ known 裡的 peer。
  // ⚠️ 刻意不放進 onMount 的啟動路徑：這是使用者按下去才發生的請求，
  // 首頁不需要為它多打三台 ×2 個請求。
  async function openSettings() {
    showSettings = true;
    await reloadSettings();
  }

  async function reloadSettings() {
    settingsLoading = true;
    settingsErr = '';
    settingsMsg = '';
    // selfId 取 /status 的 host：那才是「依後端主機」實際在跑的那台。
    // 拿不到就退回切換器目前選的那個 —— 空字串會讓 targetRows 少一列，
    // 那比顯示一個問號誠實（少一列 = 使用者看得到「沒有別台」）。
    const selfId = status?.host || backendId;
    const targets = targetRows(selfId, base(), knownHosts);
    if (!targets.length) {
      settingHosts = [];
      settingsErr = '還不知道有哪些主機（/status 沒回來，或後端沒有設定 peer 清單）';
      settingsLoading = false;
      return;
    }
    // 一台讀不到不該拖垮其他台：逐台各自成敗，UI 分別呈現。
    settingHosts = await Promise.all(targets.map((t) => fetchHostDefaults(t, fetch)));
    const picked = {};
    for (const r of settingHosts) picked[r.id] = r.model ?? '';
    hostPicked = picked;
    settingsLoading = false;
  }

  function closeSettings() {
    showSettings = false;
    settingsMsg = '';
    settingsErr = '';
  }

  // model 清單裡每個選項的可用性標記。
  //
  // ⚠️ **known=false 一律回空字串** —— 沒有資訊時不可畫成「不可用」。
  // 地端 ollama 與所有「探不到」的 provider 都走這裡：那不是壞了，
  // 而是這次沒有答案（理由見 $lib/hostDefaults.ts 的 modelAvailability）。
  // 只有 probe 明確說它在 `missing`（設了但上游 catalog 沒有）才標出來 ——
  // 那是最有價值的資訊：症狀是「選了才 404」。
  function availabilitySuffix(model) {
    if (!model) return '';
    const a = modelAvailability(model, cloudProbe);
    if (!a.known) return '';
    if (a.missing) return '　⚠ 上游沒有這個模型';
    return a.ok ? '　✓ 可用' : '';
  }

  async function saveSettings() {
    settingsSaving = true;
    settingsMsg = '';
    settingsErr = '';
    try {
      const res = await saveHostDefaults(settingHosts, hostPicked, fetch);
      const ok = res.filter((r) => r.ok);
      const skipped = res.filter((r) => r.skipped);
      const failed = res.filter((r) => !r.ok && !r.skipped);
      // 逐台結果都顯示出來 —— 一次儲存三台，其中一台失敗卻只說「已儲存」
      // 就是把「部分失敗」講成「成功」。
      const parts = [];
      for (const r of res) {
        if (r.ok) parts.push(`${r.id}：${r.model ?? '（清除，回到該機 LLM_MODEL）'}`);
        else parts.push(`${r.id}：${r.skipped ? '未送出' : '失敗'} —— ${r.error}`);
      }
      // 只重讀**成功**的那些台：拿後端實際存下的值（它可能 normalize 過）。
      // 失敗的那幾台保留原狀 —— 重讀會把使用者的選擇換成舊值，等於
      // 「一台上失敗，使用者的輸入就消失了」，他得從頭再選一次。
      const okIds = new Set(ok.map((r) => r.id));
      settingHosts = await Promise.all(
        settingHosts.map(async (row) => {
          if (!okIds.has(row.id)) return row;
          const fresh = await fetchHostDefaults(row, fetch);
          hostPicked = { ...hostPicked, [row.id]: fresh.model ?? '' };
          return fresh;
        }),
      );
      // ⚠️ 訊息必須在重讀**之後**才寫：reloadSettings() 開頭會清掉它們，
      //    順序寫反的話「已儲存 2/3 台」會一閃就不見，使用者以為沒反應。
      settingsMsg = ok.length ? `已儲存 ${ok.length}/${res.length} 台` : '沒有任何一台儲存成功';
      if (skipped.length || failed.length) settingsErr = parts.join('；');
    } catch (e) {
      settingsErr = '儲存失敗：' + (e && e.message ? e.message : e);
    } finally {
      settingsSaving = false;
    }
  }

  // ── 雲端 catalog probe（登入時觸發一次）───────────────────────────────
  let cloudProbe = null;
  // 「同一個 session 內只打一次」由 makeOnce() 負責，**不要**改成
  // 「if (probeStarted) return; probeStarted = true; await …」那種寫法。
  //
  // 為什麼需要：/auth/callback redirect 回首頁，而 SvelteKit 換頁會重建
  // component。不過瀏覽器重新整理是**新的 JS realm**、狀態會重置 —— 那種
  // 情況靠後端的 300 秒 TTL 吸收（見下），前端不做跨頁的持久化節流。
  //
  // ⚠️ 刻意**不做** localStorage／cookie 節流：後端已有 300 秒 TTL +
  //   ?force=1，那才是節流的正確層級。在前端再加一套會變成兩套機制互相繞過，
  //   而且會讓「我剛換了 key，現在就探」這件事在前端就被擋住。
  const claimProbeOnce = makeOnce();
  async function probeOnceOnLogin() {
    if (!claimProbeOnce()) return;         // 同步取得權利 → 並行呼叫也只有一個進去
    try {
      // 回 null 表示失敗（未登入／網路問題／形狀看不懂）。
      cloudProbe = await probeClouds((u, i) => fetch(api(u), i));
    } catch (_) {
      cloudProbe = null;                    // probe 失敗不影響登入，也不影響查詢
    }
    // ⚠️ 刻意不設任何錯誤訊息：probe 是附加資訊。顯示「probe 失敗」會讓
    // 使用者去排查一個不影響任何功能的問題 —— 而真相可能只是沒探到。
  }

  onMount(async () => {
    document.addEventListener('click', (ev) => {
      if (modelOpen && !ev.target.closest('.model-drop')) modelOpen = false;
    });
    // Esc 關閉「設定」對話框。
    // ⚠️ 分開一個 keydown handler 而不是在 document click 裡判斷：Esc 不是點擊，
    // 而且這段必須能在對話框 focus 在裡面時生效（用 window 才保證）。
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape' && showSettings) closeSettings();
    });
    try {
      const r = await fetch('/auth/me');
      const d = await r.json();
      if (d.ok) user = d.user;
    } catch (_) {}
    // 登入後做一次雲端 catalog probe —— 掛在**確認已登入之後**、且每次
    // onMount 最多一次。理由見 probeOnceOnLogin() 的註解。
    if (user) probeOnceOnLogin();
    // 這三個必須留在 onMount 裡，不要提到 script 頂層。
    // 兩個原因：
    //  1) SSR 階段 SvelteKit 禁止相對網址的 eager fetch，症狀是 health 徽章顯示
    //     "Cannot call `fetch` eagerly during server-side rendering"，
    //     而且 /status、/models 都不會載入（整頁看起來是空的）。
    //  2) 順序有意義：loadStatus 填完 knownHosts 之後 BACKENDS 才更新，
    //     restoreBackend 才知道上次選的是哪台；loadModels 再據此決定打到哪台後端。
    await checkHealth();
    await loadStatus();
    loadModels();
    requestAnimationFrame(grow);

    // 定期重抓，讓「連線與來源」面板反映當下狀態而不是開頁面那一瞬間的。
    // 為什麼需要：後端 registry 每 REGISTRY_HEARTBEAT 秒（預設 30）寫一次
    // 心跳，`/status` 讀的是那張表；沒有輪詢的話，使用者看到的是
    // 「上次重新整理時」的拓撲 —— 某台掉線不會自己反映出來。
    //
    // 30s 配合心跳週期：抓得再快也不會有新資料，只是白打 API。
    // 只輪詢 /status，不碰 /query（那會重置查詢狀態）。
    // 分頁不可見時暫停；SvelteKit 換頁會觸發 onDestroy 自動收掉 timer。
    const timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      if (statusLoading) return;          // 避免疊請求
      loadStatus();
    }, 30_000);
    return () => clearInterval(timer);
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

  // ⚠️ 所有後端請求都走 `/api?backend=<id>`，**不要**對非 auto 主機展開成絕對網址。
  //
  //   症狀（2026-10-05 x570 端實測，下拉選單選 x570／mbp 就出來）：health 徽章
  //   變成「✗ Failed to fetch」，而查詢其實是好的。
  //
  //   為什麼：Pages worker 的 /api 會帶 CF Access Service Token 轉發
  //   （`through()` 裡 `Object.assign(..., cfHeaders())`）。瀏覽器直接打
  //   https://api-x570.ragdemo.win/health 沒有 token → Access 回 403 → fetch
  //   因為不是 2xx 又讀不到 body 而丟出「Failed to fetch」。實測三台裸打
  //   全是 403（帶 token 才是 200）。
  //
  //   `/api/query?backend=<id>` 本來就支援指定主機（queryRoute 讀 searchParams，
  //   且會用 cfHeaders），所以指定主機**不需要**繞過 worker —— 繞過去反而
  //   丟掉唯一能穿過 Access 的那條路。
  //
  //   刻意保留 base()：targetRows()／設定對話框仍需要各台的 base 網址去讀
  //   /settings/default-model（那是唯讀、被 guard 放行的路徑）。但那些呼叫
  //   同樣會撞 Access —— 見 hostDefaults.ts 的說明，那裡是刻意容忍的
  //   （modelsKnown=false → UI 顯示「取不到」，不會假裝成功）。
  function api(path) {
    const id = backendId;
    return id && id !== 'auto' ? `/api${path}?backend=${encodeURIComponent(id)}` : `/api${path}`;
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
    // 換主機 = 換一份引用清單，展開狀態必須跟著清掉；
    // 否則新結果裡 url||art 撞到的引用會直接是展開的（而且是上一次的展開）。
    expanded = new Set();
    await checkHealth();
    await loadStatus();
    loadModels();
    requestAnimationFrame(grow);

    // 定期重抓，讓「連線與來源」面板反映當下狀態而不是開頁面那一瞬間的。
    // 為什麼需要：後端 registry 每 REGISTRY_HEARTBEAT 秒（預設 30）寫一次
    // 心跳，`/status` 讀的是那張表；沒有輪詢的話，使用者看到的是
    // 「上次重新整理時」的拓撲 —— 某台掉線不會自己反映出來。
    //
    // 30s 配合心跳週期：抓得再快也不會有新資料，只是白打 API。
    // 只輪詢 /status，不碰 /query（那會重置查詢狀態）。
    // 分頁不可見時暫停；SvelteKit 換頁會觸發 onDestroy 自動收掉 timer。
    const timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      if (statusLoading) return;          // 避免疊請求
      loadStatus();
    }, 30_000);
    return () => clearInterval(timer);
  }

  async function loadStatus() {
    statusLoading = true;
    try {
      const r = await fetch(api('/status'));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      status = await r.json();
      // 後端知道的 peer 清單。取不到就清空（切換器退回只有「自動」）——
      // 那比留著上一次的值誠實：留著會讓 UI 顯示一台已經不認識的機器。
      knownHosts = status?.known && typeof status.known === 'object' ? status.known : {};
      // ⚠️ 必須 await 一個 microtask：restoreBackend() 要用 BACKENDS.some() 驗
      //   「上次選的那台還在嗎」，而 BACKENDS 是**反應式**變數 —— knownHosts 剛賦值
      //   不代表 BACKENDS 已經重算完（Svelte 把反應式更新排到 microtask）。
      //   同步呼叫會拿到「還只有自動」的舊陣列，比對必然失敗 → 還原靜默失效。
      //   症狀（2026-10-05 實測）：localStorage 記著某台 peer，重整後下拉卻回到
      //   「自動」。這個 bug 原本被「BACKENDS 根本不重算」蓋掉 —— 兩邊都是空結果，
      //   看不出差異；修好重算後才浮出來。await Promise.resolve() 讓排程跑完那一輪即可。
      //   ⚠️ 註解裡**不要寫機台代號**：tests/test_frontend_hosts.py 的黑名單守衛
      //   只剝 // 與 /* */，不剝 HTML 註解，寫在 markup 的註解會被當成程式碼。
      await Promise.resolve();
      restoreBackend();   // 清單到了才驗得動上次選的是哪台（見函式註解）
    } catch (_) {
      status = { ok: false, host: '-', log: null };
      knownHosts = {};
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
    expanded = new Set();   // 新一批引用，展開狀態不沿用（理由同 switchBackend）
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
    // 機台清單從**回應的鍵**推導，不是寫死。log 的鍵 = 探測到的機器
    // （後端 `_host_probe_log` 永遠含本機），versions 的鍵同源。
    // 舊版寫死 ['x570','mbp','msi']：別台的機器一多一少，這裡就多一列假資料
    // 或少一列真資料。
    const ids = [...new Set([...Object.keys(log || {}), ...Object.keys(vers || {})])];
    return ids.map((id) => ({
      k: id,
      // 端點從網址反推。舊版是一組寫死的 tailscale IP —— 在別台機器上就是
      // 顯示一組假的對應關係。
      ep: (knownHosts[id] || '').replace(/^https?:\/\//, '') || '—',
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
    // 全部已知機台，不寫死是哪幾台。舊版寫死三台 → 第 4 台更新了法規卻不會
    // 觸發「可更新」提示（本機以為自己最新，其實別台有新版）。
    const ds = Object.keys(vers).map((id) => _normVer(vers[id])).filter(Boolean);
    return ds.length ? ds.sort().at(-1) : null;
  }
  function localVer(s) {
    return _normVer((s?.versions ?? {})[status?.host]) ?? _normVer(s?.law_version?.update_date);
  }
  // 有新版可抓才顯示按鈕：
  //   - 已知機台裡有比本機新的 → 抓（備援機從來源機同步；來源機重跑 ingest）
  //   - 或大家都沒有版本（沒人跑過 ingest）→ 也值得提示按一次試試
  $: newest = newestVer(status);
  $: mine = localVer(status);
  $: updatable = !!upd?.can_update && (newest === null || (mine !== null && mine < newest));
  $: updateMsg = newest === null
    ? '各機台都尚未記錄法規版本（沒人跑過每日 ingest），可按此手動觸發一次'
    : mine === null
      ? `本機沒有版本記錄；各機台最新為 ${newest}`
      : `本機 ${mine}，各機台最新 ${newest}`;

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

  // 「預設（依後端主機）」那一列要顯示**實際在跑的那台**。
  // 來源是 /status 的 host（後端回 registry.HOST_ID）—— 那正是「依後端主機」
  // 四個字的意思：不是切換器上選的名字，是後端真正服務請求的那台。
  //
  // ⚠️ 分不清「還沒載入」與「載入失敗」就會講謊：status 失敗時後端給的 fallback
  // 是 `{ok:false, host:'-'}`，那個 '-' 不是主機名。兩種情況都顯示「未知」。
  function backendHostLabel() {
    const h = status?.host;
    return h && h !== '-' ? h : '未知';
  }

  function srcRows(r) {
    return [
      { k: '檢索後端', v: r.host ?? '-' },
      { k: 'Qdrant 檢索', v: provName(r.src?.qdrant) },
      { k: 'LLM 生成', v: provName(r.src?.llm) },
    ];
  }

  // 還原上次選的後端。
  // ⚠️ 必須等 knownHosts 回來之後才能驗證：切換器的候選是從 /status 的 known
  // 推導的（不在程式裡寫死），組初始化那時 knownHosts 還是空物件，提前比對
  // 只會得到「永遠不還原」。所以在 loadStatus 填完 knownHosts 之後做。
  let restoreTried = false;
  function restoreBackend() {
    if (restoreTried) return;
    restoreTried = true;
    if (typeof localStorage === 'undefined') return;
    const saved = localStorage.getItem('ragdemo-backend');
    // 認得的才還原；認不得（那台機器已從設定移除）就留在「自動」，
    // 不要硬切到一台不存在的後端。
    if (saved && BACKENDS.some((x) => x.id === saved)) backendId = saved;
  }
  // 這三個原本在 script 頂層呼叫（restoreBackend / checkHealth / loadStatus），
  // 已搬進 onMount —— 見上方說明。留在頂層會讓 SSR 直接報錯、整頁載不到資料。
</script>

<main>
  <section class="head">
    <h1>法規判決 RAG</h1>
    <section class="auth">
      {#if user}
        <div class="model-drop">
          <button class="btn model-select" onclick={() => { modelOpen = !modelOpen; }} aria-haspopup="listbox" title="選擇查詢使用的 LLM model">
            {modelLabel() || `預設（依後端主機：${backendHostLabel()}）`}
          </button>
          {#if modelOpen}
            <div class="model-menu" role="listbox">
              <div class="model-row group">
                <span class="m-name">預設</span>
                <span class="m-meta">（依後端主機：{backendHostLabel()}）</span>
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
        <button class="btn" onclick={openSettings} aria-haspopup="dialog" title="設定各主機的預設聊天模型">
          設定
        </button>
        <a href="/auth/logout" class="btn">登出</a>
      {:else}
        <a href="/auth/login" class="btn">使用 Google 登入</a>
      {/if}
    </section>
  </section>

  <section class="switcher">
    <span class="sw-label" id="backend-label">後端：</span>
    <!-- ⚠️ 用原生 <select>（這支 codebase 的既有慣例：設定對話框、題庫頁都是它），
         不是自製 menu。自製的下拉要自己處理鍵盤／焦點／Esc，而且選項一多就
         需要捲動容器；原生控制項這些都自帶且各瀏覽器一致。
         onchange 讀 e.currentTarget.value（DOM 的真值）而不是 backendId ——
         萬一 bind:value 的監聽器晚一步才更新 state，那樣寫會用「舊值」去切換。
         選項仍**從 BACKENDS 推導**（= /status 的 known），不在這裡列舉主機名，
         否則第 4 台部署的人得改程式才能切過去。 -->
    <!-- ⚠️ `bind:value` **不能拿掉**。它不只是雙向綁定，還負責在 knownHosts
         進來之後把「上次選的那台」畫進控制項：restoreBackend() 只設 backendId，
         而 `value={backendId}` 這種屬性寫法在 Svelte 5 對<select> 不會更新
         selectedOption（實測：localStorage 記著某台 peer，重整後 DOM 仍是 auto，只有 bind 會跟上）。
         先前看到的「選完變空字串」是**測試環境**造成的 —— 該次 mock 沒攔跨網域
         路徑，loadStatus 拋錯 → knownHosts={} → 選項只剩 auto → 值落空。
         `onchange` 仍讀 e.currentTarget.value（DOM 真值），不依賴 bind 的更新時序。 -->
    <select
      class="backend-select"
      bind:value={backendId}
      onchange={(e) => switchBackend(e.currentTarget.value)}
      aria-labelledby="backend-label"
      title="選擇查詢要送到哪一台後端">
      {#each BACKENDS as b}
        <option value={b.id}>{b.label}</option>
      {/each}
    </select>
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
                <tr><th>主機</th><th>端點</th><th>法規版本</th><th>狀態</th></tr>
              </thead>
              <tbody>
                {#each hostRows(result, status) as row}
                  <tr>
                    <td>{row.k}</td>
                    <td>{row.ep}</td>
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

  <!-- ── 設定對話框（每台主機的預設模型）─────────────────────────────
    * 點背景關閉：onclick 掛在遮罩上、內層 .set-box 呼叫 stopPropagation，
    * 否則點對話框裡的任何地方都會被當成點背景。
    -->
  {#if showSettings}
    <div class="set-mask" onclick={closeSettings} role="presentation">
      <div
        class="set-box"
        role="dialog"
        aria-modal="true"
        aria-label="各主機預設聊天模型"
        onclick={(e) => e.stopPropagation()}
      >
        <h2>各主機的預設聊天模型</h2>
        <p class="hint">
          每台主機各自存自己的設定（各有一個資料庫），不會互相覆蓋。
          查詢時沒指定 model 就用這裡的值；選「（未設定）」則回到該機的 LLM_MODEL。
        </p>

        <!-- 雲端可用性 probe。只顯示**本機**的結果（登入時打的是本機後端）；
             逐台的 model 清單是上面那張表的事，兩者不重疊。 -->
        {#if cloudProbe}
          <h3 class="pop-sub">雲端可用性（本機）</h3>
          <table>
            <thead>
              <tr><th>服務</th><th>狀態</th><th>說明</th></tr>
            </thead>
            <tbody>
              {#each Object.entries(cloudProbe.providers) as [name, p]}
                <tr>
                  <td>{name}</td>
                  <!-- ⚠️ 四種 verdict 各有自己的 class，unknown 不是 bad：
                       「探不到」畫成紅字會讓使用者去換 key、查網路。 -->
                  <td>
                    <span class="vp vp-{verdictState(p.verdict)}">{verdictLabel(p.verdict)}</span>
                  </td>
                  <td>
                    {p.detail ?? '—'}
                    {#if missingModels(p).length}
                      <div class="vp-missing">
                        ⚠ 上游沒有：{missingModels(p).join('、')}
                      </div>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}

        {#if settingsLoading}
          <p class="muted">讀取各主機現況中…</p>
        {:else if !settingHosts.length}
          <p class="err">{settingsErr || '沒有可設定的主機'}</p>
        {:else}
          <table>
            <thead>
              <tr><th>主機</th><th>預設模型</th><th>實際使用</th></tr>
            </thead>
            <tbody>
              {#each settingHosts as row}
                <tr>
                  <td>
                    {row.id}
                    {#if row.self}<span class="m-meta">（目前這台）</span>{/if}
                  </td>
                  <td>
                    {#if row.state !== 'ok'}
                      <!-- 連不到：明說「無法連線」。
                           ⚠️ 絕對不能在這裡給空下拉或「未設定」—— 那看起來像
                           「那台沒有任何 model」，事實是「不知道」。 -->
                      <span class="err">無法連線</span>
                      <span class="m-meta" title={row.error}>（{row.error}）</span>
                    {:else if !row.modelsKnown}
                      <!-- 清單取不到 ≠ 清單為空。disabled 且不給選項。 -->
                      <select disabled title="無法讀取該機的模型清單">
                        <option>（模型清單取不到）</option>
                      </select>
                    {:else if !row.models.length}
                      <select disabled>
                        <option>（該機沒有可用的聊天模型）</option>
                      </select>
                    {:else}
                      <select bind:value={hostPicked[row.id]}>
                        {#each modelOptions(row) as opt}
                          <option value={opt.value}>{opt.label}{availabilitySuffix(opt.value)}</option>
                        {/each}
                      </select>
                    {/if}
                  </td>
                  <td>
                    {#if row.state === 'ok'}
                      <code>{row.effective || '—'}</code>
                    {:else}
                      <span class="muted">不知道</span>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}

        {#if settingsMsg}<p class="hint">{settingsMsg}</p>{/if}
        {#if settingsErr}<p class="err">{settingsErr}</p>{/if}

        <div class="set-actions">
          <button class="btn" onclick={closeSettings}>關閉</button>
          <button
            class="btn primary"
            onclick={saveSettings}
            disabled={settingsSaving || settingsLoading || !settingHosts.length}
          >
            {settingsSaving ? '儲存中…' : '儲存'}
          </button>
        </div>
      </div>
    </div>
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
              {#if h.repealed || h.abandoned}<span class="sc repealed" title="此條已{ h.repealed ? '刪除' : '中止施行' }，僅供對照，無現行效力">{h.repealed ? '已刪除' : '已中止'}</span>{/if}
              ｜{#if h.url}<a class="lnk" href={h.url} target="_blank" rel="noreferrer">{h.law_name}{h.art} ↗</a>{:else}{h.law_name}{h.art}{/if}
              ｜{h.item}
              <br /><span class="tx"
                >{#if !expanded.has(h.url || h.art)}{citation(h.payload.text).text}{#if citation(h.payload.text).truncated}<button
                    class="more"
                    onclick={() => toggleCite(h.url || h.art)}
                    aria-expanded="false"
                    title="點擊展開法條原文">…</button>{/if}{:else}{h.payload.text}<button
                    class="more"
                    onclick={() => toggleCite(h.url || h.art)}
                    aria-expanded="true"
                    title="點擊收合"> 收合</button>{/if}</span>
              <br /><span class="jud">{h.jud}</span>
            </li>
          {/each}
        </ol>
      {/if}
    {/if}
  {/if}
</main>

<style>
  /* ── 佈局 ──────────────────────────────────────────────────────
   * 800px 是原本就有的值（單欄閱讀欄），維持不變 —— 這是閱讀寬度，
   * 不是視覺決定，DESIGN.md 沒有對內部工具的閱讀欄寬度發言。
   */
  main {
    max-width: 800px;
    margin: var(--xxl) auto;
    padding: 0 var(--lg);
  }

  /* ── 頁首 ──────────────────────────────────────────────────────
   * h1 用 display-md（36px）而不是 display-xl（64px）：DESIGN.md 的
   * 64px 是行銷頁 hero，這是內部工具的頁首。用 64px 會讓一個查詢框
   * 被標題壓住。
   */
  .head { display: flex; align-items: center; justify-content: space-between; gap: var(--md); flex-wrap: wrap; }
  .head h1 { font: var(--display-md); letter-spacing: -0.5px; margin: 0 0 var(--md); }
  .auth { display: flex; align-items: center; gap: var(--xs); white-space: nowrap; }

  /* ── 按鈕 ──────────────────────────────────────────────────────
   * DESIGN.md〈Do's and Don'ts〉：「Don't add hover state styling
   * beyond what the system already encodes —— primary darkens on
   * press; nothing else changes.」所以 .btn / .send **不寫 :hover**，
   * 只寫 :active（對應 primary-active）。舊版每個按鈕都有 :hover 變白。
   */
  .btn {
    display: inline-flex; align-items: center; justify-content: center;
    height: 40px; padding: 0 var(--md);
    border: 1px solid var(--hairline); border-radius: var(--rounded-md);
    background: var(--canvas); color: var(--ink);
    font: var(--button); text-decoration: none; cursor: pointer;
  }
  .btn:active { background: var(--surface-soft); }
  .btn.model-select { max-width: 15rem; }

  /* 送出鈕：唯一的 coral CTA（DESIGN.md button-primary） */
  .send {
    flex-shrink: 0; width: 40px; height: 40px; padding: 0;
    display: flex; align-items: center; justify-content: center;
    border: none; border-radius: var(--rounded-md);
    background: var(--primary); color: var(--on-primary);
    cursor: pointer;
  }
  .send:active:not(:disabled) { background: var(--primary-active); }
  .send:disabled { background: var(--primary-disabled); color: var(--muted); cursor: default; }

  /* ── 查詢輸入列 ────────────────────────────────────────────────
   * focus ring 用 DESIGN.md〈text-input-focused〉的 3px coral 15% ring。
   */
  .ask-wrap {
    display: flex; align-items: center; gap: var(--xs);
    padding: var(--xs) var(--sm) var(--xs) var(--md);
    border: 1px solid var(--hairline); border-radius: var(--rounded-lg);
    background: var(--surface-card);
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .ask-wrap.multi { align-items: flex-end; } /* 備援：grow() 已用 inline style 控制 */
  .ask-wrap:focus-within {
    border-color: var(--primary);
    box-shadow: 0 0 0 3px rgba(204, 120, 92, 0.15);
  }
  textarea {
    flex: 1 1 0%; min-width: 0; resize: none; min-height: 2.4rem;
    border: none; outline: none; background: transparent;
    padding: 0 var(--xxs); line-height: 2.4rem; text-align: left;
    font: var(--body-md); color: var(--ink); overflow-y: hidden;
  }
  textarea::placeholder { color: var(--muted-text); }
  textarea:disabled { background: transparent; }
  .ask-wrap:has(textarea:disabled) { opacity: 0.65; }

  /* ── 後端切換器 ──────────────────────────────────────────────── */
  .switcher {
    display: flex; align-items: center; gap: var(--xs);
    flex-wrap: wrap; margin-bottom: var(--sm);
  }
  .sw-label { font: var(--caption); color: var(--muted); }
  /* 後端選擇器。樣式對齊 .info-btn（同一列的鄰居按鈕），讓下拉與「連線詳細」
     視覺一致；`.switcher button` / `.active` 已在改成 <select> 後移除 ——
     選中狀態現在由控制項自己呈現（select 永遠顯示當前值），不需要再畫一份。 */
  .backend-select {
    height: 32px; padding: 0 var(--xs); min-width: 7rem;
    border: 1px solid var(--hairline); border-radius: var(--rounded-md);
    background: var(--canvas); color: var(--ink);
    font: var(--nav-link); cursor: pointer;
  }
  .health { font: var(--body-sm); }
  .health.ok { color: var(--success-text); }
  .health.bad { color: var(--error); }
  .sw-right { margin-left: auto; position: relative; display: flex; }

  /* ── model 下拉 ──────────────────────────────────────────────── */
  .model-drop { position: relative; display: inline-block; }
  .model-menu {
    position: absolute; top: calc(100% + var(--xxs)); left: 0; z-index: 30;
    min-width: 18rem; max-width: 30rem; max-height: 26rem; overflow-y: auto;
    background: var(--canvas); border: 1px solid var(--hairline);
    border-radius: var(--rounded-md); box-shadow: var(--shadow-float);
    padding: var(--xxs) 0;
  }
  .model-row {
    display: flex; align-items: center; justify-content: space-between;
    gap: var(--sm); padding: var(--xs) var(--sm);
    font: var(--body-sm); color: var(--body-strong);
  }
  /* 這一條 hover 保留：它是清單項，不是按鈕。DESIGN.md 的
   * 「nothing else changes」講的是按鈕；清單沒有 hover 就分不出游標在哪。 */
  .model-row[role=option] { cursor: pointer; }
  .model-row[role=option]:hover { background: var(--surface-soft); }
  .model-row.selected { background: var(--surface-card); color: var(--ink); }
  .model-row.disabled { opacity: 0.45; cursor: not-allowed; }
  .model-row.group {
    font: var(--caption); color: var(--muted);
    background: var(--surface-soft); cursor: default;
  }
  .m-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .m-meta { margin-left: auto; white-space: nowrap; color: var(--muted-text); font: var(--caption); }
  .m-meta.limited { color: var(--error); font-weight: 500; }

  /* ── 「連線詳細」浮層 ──────────────────────────────────────────
   * 這是 dark surface（DESIGN.md product-mockup-card-dark）：內容是
   * 主機拓撲表與檢索來源，屬於「產品 chrome」而不是行銷文案，
   * 放深色是系統裡本來就有的用法。
   */
  .info-btn {
    height: 32px; padding: 0 var(--sm);
    border: 1px solid var(--hairline); border-radius: var(--rounded-md);
    background: var(--canvas); color: var(--ink);
    font: var(--nav-link); cursor: pointer;
  }
  .info-btn:active { background: var(--surface-soft); }
  .info-pop {
    position: absolute; right: 0; top: calc(100% + 10px); z-index: 20;
    background: var(--surface-dark); color: var(--on-dark);
    border: 1px solid var(--surface-dark-elevated);
    border-radius: var(--rounded-lg);
    padding: var(--lg); box-shadow: var(--shadow-float);
    min-width: 260px;
  }
  .info-pop h2 { font: var(--display-sm); letter-spacing: -0.3px; color: var(--on-dark); margin: 0 0 var(--xs); }
  .pop-user {
    font: var(--body-sm); color: var(--on-dark-soft);
    margin: 0 0 var(--xs); padding-bottom: var(--xs);
    border-bottom: 1px solid var(--surface-dark-elevated);
  }
  .info-pop .tip {
    position: absolute; top: -6px; right: 18px; width: 10px; height: 10px;
    background: var(--surface-dark);
    border-left: 1px solid var(--surface-dark-elevated);
    border-top: 1px solid var(--surface-dark-elevated);
    transform: rotate(45deg);
  }
  /* 浮層內文字轉為 on-dark 階調 */
  .info-pop .muted { color: var(--on-dark-soft); }
  .info-pop .hint { color: var(--on-dark-soft); }
  .info-pop .btn {
    background: var(--surface-dark-elevated); color: var(--on-dark);
    border-color: var(--surface-dark-elevated);
  }
  .info-pop input[type=password] {
    background: var(--surface-dark-soft); color: var(--on-dark);
    border: 1px solid var(--surface-dark-elevated);
    border-radius: var(--rounded-md); height: 32px; padding: 0 var(--xs);
  }

  /* ── 表格 ──────────────────────────────────────────────────────
   * 舊版每格都有 1px 實線框（#999）。DESIGN.md 的表格屬於
   * model-comparison-card 那一類：靠 hairline 分隔行，不畫格線。
   */
  table { border-collapse: collapse; margin: var(--xs) 0 var(--md); width: 100%; }
  th {
    text-align: left; font: var(--caption); color: var(--muted-text);
    padding: var(--xs) var(--sm); border-bottom: 1px solid var(--hairline);
  }
  td {
    padding: var(--xs) var(--sm); text-align: left;
    border-bottom: 1px solid var(--hairline-soft);
    font: var(--body-sm); color: var(--body);
  }
  .info-pop table { margin: 0; }
  .info-pop th { color: var(--on-dark-soft); border-bottom-color: var(--surface-dark-elevated); }
  .info-pop td { color: var(--on-dark); border-bottom-color: var(--surface-dark-soft); }

  /* ── 答案區 ────────────────────────────────────────────────────
   * 回答 / 引用 這兩個 h2 跟浮層裡的 h2（product-mockup-card-dark）
   * 視覺權重不同，給 display-sm 的襯線 —— 這是 DESIGN.md 說的
   * 「bigger Copernicus serif before bolder weight」。
   */
  main > h2 { font: var(--display-sm); letter-spacing: -0.3px; margin: var(--lg) 0 var(--xs); }
  .ans { white-space: pre-wrap; color: var(--ink); }
  .pop-sub { font: var(--title-sm); margin: var(--md) 0 var(--xxs); }
  .err { color: var(--error); }
  .muted { color: var(--muted); font: var(--body-sm); margin: var(--xxs) 0; }
  .hint { color: var(--muted); font: var(--body-sm); margin: var(--xxs) 0; }
  .trace { color: var(--muted-text); font: var(--code); margin: var(--xxs) 0; }
  .jud { color: var(--muted-text); font: var(--code); font-size: 12px; }
  /* 引用的來源標籤：accent-amber badge（DESIGN.md badge-pill 形狀） */
  .sc {
    font: var(--caption); color: var(--ink);
    background: var(--surface-cream-strong);
    border-radius: var(--rounded-pill); padding: 0 var(--sm);
  }
  .tx { color: var(--body); }
  /* 廢止／中止徽章。刻意與 .sc 的 cream 底不同調 —— 現行條文是「可用的」，
   * 已刪除是「僅供對照」，兩者不該看起來同級。 */
  .sc.repealed {
    background: var(--surface-soft); color: var(--muted-text);
    text-decoration: line-through;
  }
  /* 「展開全文」的省略號／收合鈕。
   *
   * ⚠️ 刻意長得像文字（無邊框、貼在 .tx 尾端）而不是像按鈕：它是句子的
   * 一部分，讀者的動作是「把這句看完」。但**必須**有可見的 focus 樣式，
   * 否則鍵盤使用者看不出焦點在哪 —— 而這是唯一能展開全文的入口。
   */
  .more {
    border: 0; background: transparent; padding: 0 var(--xxs);
    color: var(--primary-active); font: inherit; cursor: pointer;
    border-radius: var(--rounded-sm);
  }
  .more:hover { text-decoration: underline; }
  .more:focus-visible { outline: 2px solid var(--primary-active); outline-offset: 2px; }
  /* text-link：coral 內文連結（DESIGN.md 說這是系統最鮮明的小細節之一）。
   * 用 primary-active 而不是 primary：原色在 canvas 上只有 3.11:1，
   * 壓暗一階是 4.80:1，且那個 hex 本來就是 DESIGN.md 的 token，不是新值。 */
  .lnk, .hint a { color: var(--primary-active); text-decoration: none; }
  .lnk:active, .hint a:active { text-decoration: underline; }
  ol { padding-left: var(--lg); }
  ol li { margin-bottom: var(--sm); }

  /* law-update：法規版本欄位後方的「更新」按鈕 */
  button.upd {
    margin-left: var(--xs); height: 24px; padding: 0 var(--xs);
    font: var(--caption); vertical-align: middle;
  }
  button.upd:disabled { opacity: 0.5; cursor: not-allowed; }
  .upd-row { margin: var(--xs) 0 0; display: flex; flex-wrap: wrap; gap: var(--xs); align-items: center; }
  .upd-row input[type=password] { height: 32px; padding: 0 var(--xs); font: var(--body-sm); min-width: 14rem; }

  /* ── 設定對話框 ────────────────────────────────────────────────────
   * 遮罩 + 卡片。刻意**不**沿用 .info-pop 的深色：那一個是「連線詳細」
   * 的旁白（DESIGN.md product-mockup-card-dark），這裡是可操作的表單，
   * 深色底上的 <select> 在各瀏覽器會掉回系統樣式、反白不可讀。
   * 高度用 dvh 而不是 100vh：iOS Safari 的 100vh 含網址列，會讓卡片
   * 在小螢幕被切掉（這是 dvh 存在的唯一理由）。
   */
  .set-mask {
    position: fixed; inset: 0; z-index: 60;
    display: flex; align-items: center; justify-content: center;
    padding: var(--lg);
    background: rgba(20, 20, 19, 0.45);
  }
  .set-box {
    background: var(--canvas);
    border: 1px solid var(--hairline);
    border-radius: var(--rounded-lg);
    box-shadow: var(--shadow-float);   /* 唯一的浮層陰影 token */
    padding: var(--lg);
    width: 100%; max-width: 40rem;
    max-height: 85dvh; overflow-y: auto;
  }
  .set-box h2 { font: var(--display-sm); letter-spacing: -0.3px; margin: 0 0 var(--xs); }
  .set-box td select {
    height: 32px; padding: 0 var(--xs);
    border: 1px solid var(--hairline); border-radius: var(--rounded-md);
    background: var(--canvas); color: var(--ink);
    font: var(--body-sm); max-width: 100%;
  }
  .set-box td select:disabled { color: var(--muted); background: var(--surface-soft); cursor: not-allowed; }
  .set-box td code { font: var(--code); font-size: 12px; color: var(--body); }
  .set-actions { display: flex; justify-content: flex-end; gap: var(--xs); margin-top: var(--md); }

  /* ── 雲端 probe 的四種 verdict ────────────────────────────────────────
   * ⚠️ 四種狀態各有階調，**unknown 不是 bad**。
   * 「探不到」畫成紅字會讓使用者以為那個 provider 壞了，於是去換 key、
   * 查網路 —— 而真相是「這次沒探到」。`off`（未設定）同樣不是故障：
   * 一台沒開某個 provider 的機器完全正常。
   *
   * 顏色用 theme.css 已有的語意階：error / warning / muted-text / success-text。
   */
  .vp { font: var(--caption); white-space: nowrap; }
  .vp-ok { color: var(--success-text); }
  .vp-bad { color: var(--error); }
  .vp-unknown { color: var(--warning); }   /* 探不到 ≠ 壞了 */
  .vp-off { color: var(--muted-text); }    /* 未設定，不是故障 */
  .vp-missing { color: var(--error); font: var(--caption); margin-top: var(--xxs); }
  /* 儲存是這個對話框的唯一 affirmative action（DESIGN.md button-primary） */
  .btn.primary {
    background: var(--primary); color: var(--on-primary); border-color: var(--primary);
  }
  .btn.primary:active:not(:disabled) { background: var(--primary-active); }
  .btn.primary:disabled { background: var(--primary-disabled); color: var(--muted); cursor: not-allowed; }
</style>
