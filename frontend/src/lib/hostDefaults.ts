// per-host 預設聊天模型：首頁「設定」對話框的邏輯。
//
// ## 為什麼獨立成一個模組
//
// 這裡的每一步都要能被**在 node 裡真的跑一次**（見 tests/test_frontend_host_defaults.py）——
// 「某台連不上時顯示什麼」這種東西，靠原始碼 regex 斷言是測不出來的。
// 所以規則是：邏輯全在這裡、fetch 由呼叫端注入、**不 import 任何 SvelteKit 模組**。
//
// ## 為什麼每台都要分別問
//
// GET/PUT /settings/default-model **只作用於自己那台**（body 沒有 host 欄位）。
// 三台各有自己的 pg，所以要設定三台就得問三次、寫三次。
// 問誰？問 /status 回的 known（後端 HOST_API_URLS）加上本機 —— 沿用 +page.svelte
// 既有的後端切換器做法：base 為空字串走 /api 相對路徑（經 Pages worker），
// 非空就直接打那台的網址。**不要在這裡列舉主機名。**

export interface HostTarget {
  /** 主機代號（來自 /status 的 host 或 known 的鍵），只用於顯示 */
  id: string;
  /** 該台的 API 根網址；空字串代表走 /api（由 worker／dev proxy 決定實際服務誰） */
  base: string;
  /** 是否為目前這個對話框「正在服務的那台」（查詢時的預設就是它） */
  self: boolean;
}

export interface HostRow extends HostTarget {
  /**
   * 只讀端點（/settings/default-model）是否讀到了。
   * ⚠️ 這**不等於**「該台沒有設定」—— false 的意思是「不知道」。
   */
  state: 'ok' | 'unreachable';
  /** 讀不到的原因（診斷用，別把它顯示成「那台沒設定」） */
  error: string;
  /** 該機存的 default_model；null = 未設定（後端回的就是 null，不是空字串） */
  model: string | null;
  /** 實際會用的（後端的 effective）：model 有值就是它，否則該機的 LLM_MODEL */
  effective: string;
  /** 該機可用於聊天推理的模型（/models 的 local，已濾掉 :embed 與 reranker） */
  models: string[];
  /**
   * /models 的清單是否真的取到了。
   * ⚠️ **必須與 models.length 分開表達**：false + [] 是「取不到」，
   *    true + [] 才是「該台真的沒有可用模型」。合成一個欄位就會把
   *    「無從驗證」呈現成「驗證失敗」—— 這是本專案反覆在修的那類錯誤。
   */
  modelsKnown: boolean;
}

export interface SaveResult {
  id: string;
  ok: boolean;
  /** true = 沒送出（因為連不到，不該盲寫）；否則 ok 才有意義 */
  skipped: boolean;
  error: string;
  model: string | null;
  effective: string;
}

// 路徑集中在此，避免各處字串漂移；後端契約見 backend/app/main.py。
const SETTINGS_PATH = '/settings/default-model';

export function hostUrl(base: string, path: string): string {
  return base ? base + path : '/api' + path;
}

/**
 * 組出要設定的主機清單：正在服務的那台 ＋ /status 的 known 裡的 peer。
 *
 * ⚠️ known **通常包含自己**（HOST_API_URLS 是三台互列的），不去重就會
 * 顯示兩列同一台 —— 而那看起來像「設定了兩次」。用 id 去重，本機優先。
 */
export function targetRows(selfId: string, selfBase: string, known: Record<string, string>): HostTarget[] {
  const out: HostTarget[] = [];
  const seen = new Set<string>();
  const add = (id: string, base: string, self: boolean) => {
    if (!id || seen.has(id)) return;
    seen.add(id);
    out.push({ id, base, self });
  };
  add(selfId, selfBase, true);
  for (const [id, url] of Object.entries(known || {})) add(id, url, false);
  return out;
}

function errText(e: unknown): string {
  if (!e) return '未知錯誤';
  const m = (e as Error).message;
  return m ? m : String(e);
}

/**
 * 讀一台的現況：預設模型（/settings/default-model）＋ 可用模型（/models）。
 *
 * ⚠️ 兩個請求**分別**成敗。/settings 讀到了但 /models 掛掉時，model/effective
 *    仍然有效（那是設定）；反之亦然。把兩者綁成一個 try 會讓任一端點故障
 *    看起來像「這台完全不知道」—— 那比實際知道的少。
 */
export async function fetchHostDefaults(t: HostTarget, doFetch: any): Promise<HostRow> {
  const row: HostRow = {
    ...t,
    state: 'ok',
    error: '',
    model: null,
    effective: '',
    models: [],
    modelsKnown: false,
  };
  let setting: any;
  try {
    const r = await doFetch(hostUrl(t.base, SETTINGS_PATH));
    if (!r.ok) throw new Error('HTTP ' + r.status);
    setting = await r.json();
  } catch (e) {
    // 連不到 → 不知道。**不是**「該台沒設定」。
    return { ...row, state: 'unreachable', error: errText(e) };
  }
  // model 可能是 null（未設定）也可能是字串；不要用 || 兜底，那會把 '' 變 null。
  row.model = setting && typeof setting.model === 'string' && setting.model ? setting.model : null;
  row.effective = setting && typeof setting.effective === 'string' ? setting.effective : '';

  try {
    const r = await doFetch(hostUrl(t.base, '/models'));
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    // ⚠️ `local` **不存在或不是陣列** → 當成「取不到」，不是「沒有模型」。
    // 200 但欄位對不上，代表我們沒有拿到能解讀的答案；那正是「無從驗證」。
    // 只有真的讀到陣列（即使是空的）才敢說 modelsKnown=true。
    if (!d || !Array.isArray(d.local)) {
      row.modelsKnown = false;
      row.models = [];
      return row;
    }
    row.models = d.local.filter((m: unknown) => typeof m === 'string');
    row.modelsKnown = true;   // 讀到了 —— 空陣列是「真的沒有」，不是「取不到」
  } catch (e) {
    row.modelsKnown = false;  // 取不到：清單不可信，UI 必須說明而不是給空下拉
    row.models = [];
  }
  return row;
}

/**
 * 該台的下拉選項。
 *
 * ⚠️ 第一項永远是「未設定」：對應 PUT 的 `model: null`，即回到該機的 LLM_MODEL。
 *    沒有這一項，使用者就無法把設定清回預設（後端明確支援，見契約）。
 */
export function modelOptions(row: HostRow): Array<{ value: string; label: string }> {
  return [
    { value: '', label: '（未設定 → 用該機 LLM_MODEL）' },
    ...row.models.map((m) => ({ value: m, label: m })),
  ];
}

/**
 * 一次儲存所有主機：每台各自 PUT 一次。
 *
 * ⚠️ **讀不到的那台不送出**。如果連 `/settings/default-model` 都失敗，我們手上
 *    的「目前值」其實是不知道 —— 那時送出去的不是「使用者选的」而是「我們猜的」，
 *    而且寫入端不做降級（PUT 失敗回 503），猜錯就是靜默改掉別台的設定。
 *    這裡寧可回報「未送出」也不盲寫。
 *
 * @param picked id → 選項 value；空字串代表清除（送出 null）
 */
export async function saveHostDefaults(rows: HostRow[], picked: Record<string, string>, doFetch: any): Promise<SaveResult[]> {
  const out: SaveResult[] = [];
  for (const row of rows) {
    if (row.state !== 'ok') {
      out.push({
        id: row.id, ok: false, skipped: true,
        error: `無法讀取該機現況，未送出（${row.error}）`,
        model: null, effective: '',
      });
      continue;
    }
    const v = picked[row.id] ?? '';
    const model = v ? v : null;      // 空字串 = 清除（契約：null／"" 都清除）
    try {
      const r = await doFetch(hostUrl(row.base, SETTINGS_PATH), {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ model }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        out.push({
          id: row.id, ok: false, skipped: false,
          error: (d && d.detail) || `HTTP ${r.status}`,
          model: null, effective: '',
        });
        continue;
      }
      out.push({
        id: row.id, ok: true, skipped: false, error: '',
        model: d && typeof d.model === 'string' && d.model ? d.model : null,
        effective: d && typeof d.effective === 'string' ? d.effective : '',
      });
    } catch (e) {
      out.push({ id: row.id, ok: false, skipped: false, error: errText(e), model: null, effective: '' });
    }
  }
  return out;
}

/**
 * 「同一個 session 內只做一次」的守衛。
 *
 * ⚠️ 為什麼是一個函式而不是頁面裡的一個布林值：因為**並行**呼叫。
 * 頁面裡若寫成
 *
 *     if (probeStarted) return;
 *     probeStarted = true;          // ← 這裡之後、await 之前必須設
 *     await probe();
 *
 * 順序寫對了才安全，而「寫對了」是原始碼看不出來的事（實測：把賦值移到
 * try 裡、await 之後，兩條 regex 斷言都照樣綠，但兩個並行呼叫會各打一次
 * —— 而雲端探測是會燒額度的）。
 *
 * 放在這裡是為了能**真的跑一次並行的情況**來驗證（tests 會對它做
 * `Promise.all([once(), once()])` 並斷言只打一次）。
 */
export function makeOnce(): () => boolean {
  let done = false;
  return () => {
    if (done) return false;
    done = true;          // 在回傳之前就設：同步生效，兩個並行呼叫只會有一個 true
    return true;
  };
}

// ── 雲端 catalog probe ────────────────────────────────────────────────────

/**
 * probe 的四種 verdict。⚠️ **四種都不可合併**，尤其 `unknown` 不可當成故障。
 *
 * | verdict   | 意思                        | 不是                              |
 * |-----------|-----------------------------|-----------------------------------|
 * | `up`      | catalog 可用                | —                                 |
 * | `down`    | **明確失敗**（401/403/404） | 不是「沒設定」                    |
 * | `unknown` | **探不到**（逾時/DNS/TLS）   | **不是壞了**                      |
 * | `off`     | **未設定**                  | **不是故障**                      |
 *
 * 把 `unknown` 畫成紅字等於說「那個 provider 壞了」，而真相是「不知道」——
 * 使用者會去換 key、查網路，問題其實只是這次沒探到。把 `off` 畫成紅字會讓
 * 一台完全正常的機器看起來有問題（實測 wsl 上 zen 沒設 key，那是預設狀況）。
 */
export type Verdict = 'up' | 'down' | 'unknown' | 'off';

export interface ProviderProbe {
  verdict: Verdict;
  detail?: string;
  count?: number;
  /** 已設定但**上游 catalog 裡沒有**的 —— 最有價值的資訊（症狀是「選了才 404」） */
  missing?: string[];
  available?: string[];
  configured?: string[];
}

export interface CloudProbe {
  summary?: Record<string, number>;
  providers: Record<string, ProviderProbe>;
  age?: number;
}

/** verdict → 樣式用的狀態。四種**各自有別**，`unknown` 刻意不是 `bad`。 */
export function verdictState(v: unknown): 'ok' | 'bad' | 'unknown' | 'off' {
  if (v === 'up') return 'ok';
  if (v === 'down') return 'bad';
  if (v === 'off') return 'off';
  return 'unknown';   // 含「形狀看不懂」→ 不知道，不是壞了
}

/** 給人看的字。`unknown` 說「探不到」而不是「失敗」。 */
export function verdictLabel(v: unknown): string {
  switch (verdictState(v)) {
    case 'ok': return '可用';
    case 'bad': return '失敗';
    case 'off': return '未設定';
    default: return '探不到';
  }
}

/**
 * 前端 model 值的前綴 → probe 回應裡的 provider key。
 *
 * ⚠️ **兩個名字不一樣的有兩個**，而那正是最容易漏的：
 *     前端選單：  nv/…      probe：providers["nvidia"]
 *     前端選單：  mis/…     probe：providers["mistral"]
 * 直接拿前綴當 key 去查會查不到 —— 而症狀是「那幾個 provider 的模型永遠
 * 顯示成不知道」，看起來像 probe 沒送到。
 *
 * 這是 `+page.svelte` 裡 `prefixOf` 的反方向（那個是 provider→前綴，用來
 * 組 usageMap 的 key）。兩份必須一致，測試有釘（見 tests）。
 */
const PROVIDER_OF_PREFIX: Record<string, string> = {
  openrouter: 'openrouter',
  zen: 'zen',
  nv: 'nvidia',
  nvidia: 'nvidia',
  gemini: 'gemini',
  groq: 'groq',
  cohere: 'cohere',
  hf: 'hf',
  mis: 'mistral',
  mistral: 'mistral',
};

/**
 * 某個 model 的可用性標記。
 *
 * ⚠️ 回 `known: false` = **沒有任何資訊**（provider 沒被 probe／verdict 不是
 *    up／形狀看不懂），UI **必須**照原樣顯示 model 名，不可畫成「不可用」。
 *
 * ⚠️ 只有 `verdict === 'up'` 才代表清單可信：那時 `available`／`missing`
 *    才是完整的。provider 探不到時這兩個陣列必然不完整，拿它去否定某個
 *    model，就是把「無從驗證」講成「驗證失敗」。
 */
export function modelAvailability(
  model: string,
  probe: CloudProbe | null,
): { ok: boolean; known: boolean; missing?: boolean } {
  if (!probe || !probe.providers) return { ok: false, known: false };
  const slash = model.indexOf('/');
  if (slash <= 0) return { ok: false, known: false };   // 地端 ollama：probe 不涵蓋
  const key = PROVIDER_OF_PREFIX[model.slice(0, slash)];
  const p = key ? probe.providers[key] : undefined;
  if (!p) return { ok: false, known: false };
  if (p.verdict !== 'up') return { ok: false, known: false };

  // ⚠️ **probe 的 model id 沒有前端的前綴**，而前端的下拉選單有。
  // 實測（wsl，2026-10-02）：
  //     前端選單：  openrouter/cohere/north-mini-code:free
  //     probe 回：  cohere/north-mini-code:free          ← 少了第一段
  // 直接用整串 `includes(model)` 比對會**全部落空**：於是每一個模型都變成
  // 「不知道」，而 `missing`（上游沒有這個模型，最有價值的那個訊息）永遠
  // 不會顯示 —— 症狀是「功能看起來沒壞，但從沒標出過任何一個」。
  //
  // 所以要把前綴剝掉再比對。前綴本身已經確認過是這個 provider 的
  // （上面 `probe.providers[prefix]` 那一行），所以剝掉是安全的。
  const bare = model.slice(slash + 1);

  if (Array.isArray(p.missing) && p.missing.includes(bare)) {
    return { ok: false, known: true, missing: true };
  }
  if (Array.isArray(p.available) && p.available.includes(bare)) {
    return { ok: true, known: true };
  }
  // 不在 configured 裡的模型（例如使用者手動輸入的）→ probe 沒有它的資訊。
  if (Array.isArray(p.configured) && p.configured.includes(bare)) {
    // configured 卻不在 available/missing → catalog 回來了但兩邊都沒有它。
    // 這是後端比對規則（葉子名比對）沒命中；不可判成「不可用」。
    return { ok: false, known: false };
  }
  return { ok: false, known: false };
}

// ── 引用條文的截斷 ────────────────────────────────────────────────────────

/** 超過這個長度才截斷。與後端無關，純前端版面決定。 */
export const CITATION_MAX = 200;

/**
 * 引用條文的顯示文字。
 *
 * ⚠️ 舊版是 `text.slice(0, 200) + '…'` —— **無條件**加省略號。症狀
 * （2026-10-05 使用者回報）：短條文也被砍掉尾巴、而且結尾一定帶著「…」，
 * 看起來像「後面還有東西」但其實只有 90 字。實測回「證券交易法第16條」
 * 那筆全文 90 字，仍被截成 80 字＋「…」。
 *
 * 所以規則是：**沒有被截就完全不加省略號**。省略號的唯一意義是「這裡
 * 省略了東西」，沒有省略就不該出現 —— 否則讀者會去找不存在的後半段。
 *
 * 放在這裡而不是樣板內，是為了能真的跑一次測試（`slice` 與條件判斷在
 * 樣板裡寫對寫錯，regex 斷言看不出來）。
 */
export function citationText(text: unknown): { text: string; truncated: boolean } {
  const s = typeof text === 'string' ? text : '';
  if (s.length <= CITATION_MAX) return { text: s, truncated: false };
  // ⚠️ 在**項邊界**截斷，不可直接 slice（spec FR-010：切塊不切在某個項的中間）。
  //   直接切會產生半截的項 —— 讀者看到「…前項財務報告之內容、適用範圍」這種
  //   斷句，會誤以為條文原文就這樣。在法律場合，寧可少顯示幾個字也不要給一個
  //   看起來像原文的斷句。
  //   逐項累加取放得下完整的那些；單一項本身就超過上限時才退回字元截斷
  //   （那種情況沒有「完整的一項」可選 —— 但旗標仍是 truncated，可點開看全文）。
  let out = '';
  for (const seg of s.split('\n')) {
    const next = out ? `${out}\n${seg}` : seg;
    if (next.length > CITATION_MAX) break;
    out = next;
  }
  if (out) return { text: out, truncated: true };
  return { text: s.slice(0, CITATION_MAX), truncated: true };
}

/**
 * 把條文拆成「項」的陣列 —— 一個元素對應司法院的一個 `line-*` div。
 *
 * ⚠️ 2026-10-05 使用者提供的實測 DOM（證券交易法第6條）：
 *
 *     <div class="law-article">
 *       <div class="line-0000 show-number">本法所稱有價證券，指政府債券…</div>
 *       <div class="line-0000 show-number">新股認購權利證書…</div>
 *       <div class="line-0000 show-number">前二項規定之有價證券…</div>
 *     </div>
 *
 * 三個項是**三個獨立元素**，所以「比照處理」不只是視覺換行。只用
 * `white-space: pre-wrap` 也是一行一個字，但 DOM 上仍是單一節點，
 * 讀者複製或選取時三項會黏在一起。
 */
export function paragraphs(text: unknown): string[] {
  if (typeof text !== 'string') return [];
  return text.split('\n').map((s) => s.trim()).filter((s) => s.length > 0);
}

/**
 * 一段條文的排版單元：項（含項次）或款（無項次）。
 *
 * `no` 是**項次數字**，只在「項」且「該條有 2 個以上項」時才有值 —— 那是我們
 * 從司法院官網反推出來的規則（2026-10-05，證券交易法 229 條零反例）：
 *
 *   官網 CSS：`.law-article div.show-number::before { counter-increment: num 1;
 *              content: counter(num) }`
 *   實測對照：
 *     第 1 條   項×1                → 不編號
 *     第 6 條   項×3                → 1 2 3
 *     第 15 條  項×1 + 款×3         → 全不編號
 *     第 105 條 項×1 + 款×10 + 目×6 → 全不編號
 *     第 174 條 項×7 交錯款×11      → 6 個項全部編號，11 個款全不編號
 *
 * 規則：**項數 ≥ 2 → 項全部編號；否則全部不編號；款／目永不編號。**
 * 官網的 counter 只對帶 `show-number` 的累加，所以款不會打斷項的編號。
 *
 * ⚠️ 款與項的區分不需要上游的欄位 —— 款以「一、」「（一）」「1.」開頭，
 *   其餘是項。這與後端 `law_struct._ITEM_RE` 是同一套判斷，所以前端算出的
 *   「N項M款」與後端的結構統計必然一致。
 */
export interface ArticleRow {
  /** '項' 有項次；'款' 沒有（自帶「一、」等文字標籤） */
  kind: '項' | '款';
  text: string;
  /** 項次數字（1 起算）；不該顯示時為 null */
  no: number | null;
}

// 款開頭：一、／（一）／1.／1,（與後端 law_struct._ITEM_RE 同一套）
const ITEM_LABEL = /^\s*(?:[一二三四五六七八九十百零]+、|（[一二三四五六七八九十百零]+）|\d+[\.、])/;

export function articleRows(text: unknown): ArticleRow[] {
  const segs = paragraphs(text);
  const kinds = segs.map((s) => (ITEM_LABEL.test(s) ? '款' : '項'));
  const nItem = kinds.filter((k) => k === '項').length;
  const numbered = nItem >= 2;          // 官網規則
  let n = 0;
  return segs.map((s, i) => {
    const isItem = kinds[i] === '項';
    if (isItem && numbered) n += 1;
    return { kind: kinds[i], text: s, no: isItem && numbered ? n : null };
  });
}

/** 該 provider 有沒有「設了但上游沒有」的 model。 */
export function missingModels(p: ProviderProbe | undefined): string[] {
  return p && Array.isArray(p.missing) ? p.missing : [];
}

/**
 * 打一次 probe。**冪等**（後端 300 秒 TTL，`?force=1` 才跳過），
 * 所以前端**刻意不做節流** —— 那是兩套機制互相繞過。
 *
 * @returns 失敗回 `null`。**不是**回 `{error}` —— 那會讓 UI 分不出
 *          「沒 probe」與「probe 失敗」，於是顯示成後者，而那是謊話。
 */
export async function probeClouds(doFetch: any): Promise<CloudProbe | null> {
  try {
    const r = await doFetch(hostUrl('', '/settings/probe-clouds'), { method: 'POST' });
    if (!r.ok) return null;
    const d = await r.json();
    if (!d || typeof d.providers !== 'object' || d.providers === null) return null;
    return d as CloudProbe;
  } catch {
    // ⚠️ 靜默：這是附加資訊，不是登入的前置條件。回 null，UI 據此不顯示
    // 任何 probe 區塊 —— 那比顯示「probe 失敗」誠實（真的可能只是沒探到）。
    return null;
  }
}
