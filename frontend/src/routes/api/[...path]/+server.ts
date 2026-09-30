import type { RequestHandler } from './$types';
import { env } from '$env/dynamic/private';
import { readCookie, verifySession } from '$lib/google';

const SENSITIVE = new Set(['query', 'ingest', 'eval', 'rules']);

interface Host { id: string; url: string }

// 後端清單**只**來自 Pages 變數，不在程式裡列舉主機。
//
// 2026-09-27 之前這裡寫死三台（DEFAULT_ORIGINS），而且 `API_ORIGIN` 若正好
// 命中其中一台就「偷偷展開成完整三台」。那等於：第 4 台部署的人必須改程式，
// 而且他會在自己只想指一台時被靜默塞進兩台他沒有的位址。兩者都拿掉。
//
// `API_ORIGINS` 兩種格式都收（與後端 HOST_API_URLS / OLLAMA_URLS 同規格）：
//   https://api-a.example.com,https://api-b.example.com   ← 純網址
//   a=https://api-a.example.com,b=https://api-b.example.com ← id=網址
// 沒給 id 就從主機名第一段推導（api-x570.ragdemo.win → x570），
// 這樣純網址寫法不必為了顯示名稱而硬湊 id。
//
// ⚠️ 完全未設 → 視為單機部署，轉發一律 503 並說明缺什麼。**不**退回任何
// 內建清單：那正是「換台機器就壞掉」的來源。
function parseOrigins(s?: string): Host[] {
  const out: Host[] = [];
  for (const part of (s || '').split(',')) {
    const raw = part.trim();
    if (!raw) continue;
    // ⚠️ 順序很重要：必須先切 `id=`，**再**檢查網址。
    // 先檢查的話 `msi=https://api-msi.ragdemo.win` 會因為開頭不是 http
    // 而被整段丟掉 —— 那正是上面宣告要支援的格式。實測踩到。
    let id = '';
    let url = raw;
    const eq = raw.indexOf('=');
    if (eq > 0 && raw.slice(eq + 1).includes('://')) {
      id = raw.slice(0, eq).trim();
      url = raw.slice(eq + 1).trim();
    }
    url = url.replace(/\/$/, '');
    if (!/^https?:\/\//.test(url)) continue;   // 純 id 或壞值 → 丟棄，不產生假 peer
    if (!id) {
      try {
        id = new URL(url).hostname.split('.')[0].replace(/^api-/, '');
      } catch {
        id = url;
      }
    }
    out.push({ id, url });
  }
  return out;
}

async function guard(request: Request, path: string): Promise<Response | null> {
  if (!SENSITIVE.has(path)) return null;
  const secret = env.SESSION_SECRET;
  if (!secret) {
    return new Response(JSON.stringify({ detail: 'SESSION_SECRET 未設定，無法驗證登入狀態' }), {
      status: 503,
      headers: { 'content-type': 'application/json' },
    });
  }
  const user = await verifySession(readCookie(request.headers.get('cookie'), 'ragdemo_session'), secret);
  if (!user) {
    return new Response(JSON.stringify({ detail: '請先登入 Google 後再操作（查詢與寫入皆需登入）' }), {
      status: 401,
      headers: { 'content-type': 'application/json' },
    });
  }
  return null;
}

function parsed(path: string): string {
  return path.split('/')[0];
}

interface Env {
  API_ORIGIN?: string;
  API_ORIGINS?: string;
}

// 依序清單。API_ORIGINS 優先（多台備援）；只有 API_ORIGIN 就當單一台。
// 兩者都未設 → 空陣列（= 未設定，呼叫端會回 503 說明缺什麼）。
function hostsOf(platform?: { env?: Env }): Host[] {
  const pe = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  const many = platform?.env?.API_ORIGINS || pe?.env?.API_ORIGINS;
  if (many) return parseOrigins(many);
  const single = (platform?.env?.API_ORIGIN || pe?.env?.API_ORIGIN || '').trim();
  return parseOrigins(single);
}

// 判斷該後端是否「已離線（不值得重試）」：網路層失敗，或 Cloudflare
// tunnel 離線的典型回應（502/503/504/530/1033）。其他 4xx（如 401/404）視為
// 後端正常回應，直接回傳不切換。
function dead(status: number): boolean {
  return status === 502 || status === 503 || status === 504 || status === 530 || status === 1033;
}

// 轉發時**不得**沿用上游的編碼／長度／連線相關標頭。
//
// ⚠️ 這是 2026-09-28 修的線上全站 502。舊版是：
//     const h = new Headers(r.headers);          // ← 上游 headers 整份抄過來
//     return new Response(r.body, { headers: h });
// 上游經 Cloudflare 會回 `content-encoding: br` 與 `content-length`，但 Workers
// runtime 已經把 body 解壓過了 —— 宣告的編碼與長度跟實際內容對不上，Cloudflare
// 送不出去，就回一張**自己的**錯誤頁（`text/plain`、`error code: 502`、16 bytes）。
//
// 症狀為什麼那麼難查：`/api/query` 走 guard() 拿 401 正常回應，所以「Function
// 有在跑」是成立的；只有需要轉發的端點全死，看起來像後端掛了，而後端其實
// 從公網打完全正常（第三方主機代打得到 200）。
const DROP_ON_PROXY = new Set([
  'content-encoding',
  'content-length',
  'transfer-encoding',
  'connection',
  'keep-alive',
  'content-range',
  'accept-ranges',
  'content-md5',
]);

// body 緩衝下來再送出，不用 `r.body` 串流。
//
// 串流時上游連線一斷就變成同樣那種沒有錯誤訊息的 502，而且已經送出標頭就
// 無法再補。/api/query 這類回答動辄數十 KB，緩衝成本可以忽略。
async function relay(r: Response, origin: string): Promise<Response> {
  const buf = await r.arrayBuffer();
  const h = new Headers();
  for (const [k, v] of r.headers) {
    if (!DROP_ON_PROXY.has(k.toLowerCase())) h.set(k, v);
  }
  h.set('content-type', r.headers.get('content-type') ?? 'application/json');
  h.set('x-ragdemo-origin', origin); // 前端可顯示「自動→實際服務主機」
  return new Response(buf, { status: r.status, headers: h });
}

// 任何未捕捉的例外都要變成**看得懂的 JSON**，不能變成 Cloudflare 的錯誤頁。
//
// 線上那個 502 之所以查不出原因，就是因為 worker 死掉時只留一張 text/plain 的
// 502（`error code: 502`），完全不說是哪一步壞的。這裡把「哪一步」一起帶出去。
function jsonError(phase: string, e: unknown): Response {
  const msg = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
  return new Response(JSON.stringify({ detail: `worker 在「${phase}」失敗：${msg}` }), {
    status: 502,
    headers: { 'content-type': 'application/json' },
  });
}

async function through(method: string, path: string, body: string | undefined, platform?: { env?: Env }, headers?: Headers): Promise<Response> {
  const origins = hostsOf(platform).map((h) => h.url);
  if (origins.length === 0) {
    return new Response(JSON.stringify({ detail: 'API_ORIGINS/API_ORIGIN 未設定（請在 Cloudflare Pages 變數設定）' }), {
      status: 503,
      headers: { 'content-type': 'application/json' },
    });
  }
  const init: RequestInit = { method, headers: { 'content-type': 'application/json' } };
  // 透傳管理 token（/rules 寫入用），不落入 cookie
  const ah = headers?.get('authorization');
  if (ah) init.headers['authorization'] = ah;
  if (body !== undefined) init.body = body;

  const failures: string[] = [];
  for (const origin of origins) {
    try {
      const r = await fetch(`${origin}/${path}`, init);
      if (dead(r.status)) {
        failures.push(`${origin} HTTP ${r.status}`);
        continue; // 這台離線／壞了 → 依序試下一台
      }
      // ⚠️ relay() 也要在 try 裡：上游 body 讀取中途斷掉會拋出，那時
      // 已經知道是哪一台 origin 壞了，別把它變成無來源的 502。
      return await relay(r, origin);
    } catch (e) {
      failures.push(`${origin} ${(e as Error).message}`);
    }
  }
  const detail = failures.length ? failures.join('；') : '後端全數不可達';
  return new Response(JSON.stringify({ detail: `全數後端備援失敗：${detail}` }), {
    status: 502,
    headers: { 'content-type': 'application/json' },
  });
}

// 回 {ok, seen} 而不是 boolean：全掛時 `seen` 會被帶進 detail，讓「Access 沒配
// token」(403)、「後端沒開」(502/1033)、「網路層死」(network error) 分得開。
// 舊版回 boolean，全掛時一律顯示「所有後端皆無法連線」—— 那句話在 Access
// 的情況下是假的（token 壞掉時三台都活得好好的）。
//
// ⚠️ `seen` 是**診斷用**，不是契約。log 的值域仍是 '連線成功' | '連線失敗'
// 兩個字串（+page.svelte 用 `=== '連線成功'` 精確比對，gateway._host_probe_log
// 產同一組字串）—— 這裡刻意不擴充它，那要前後端同時改。
async function probe(url: string): Promise<{ ok: boolean; seen: string }> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 6000);
  try {
    const r = await fetch(`${url}/health`, { signal: ctrl.signal });
    // 兩個條件缺一不可，理由與 gateway._host_probe_log 完全相同：
    //   r.ok          —— 已經擋掉 4xx/5xx。
    //   content-type  —— Workers 的 fetch() 依 Fetch 規範預設 redirect:"follow"，
    //     Cloudflare Access 若回 302，runtime 會跟到登入頁、拿到 **200 +
    //     text/html**，而 r.ok 對那個 200 為真 → 舊版會說這台活著，實際上
    //     真正的 /query 會被擋。面板說「連線成功」但查詢全 403。
    const ct = r.headers.get('content-type') || '';
    if (r.ok && ct.includes('application/json')) return { ok: true, seen: '200' };
    return {
      ok: false,
      seen: r.ok ? `200 但非 JSON（${ct || '無 content-type'}）` : String(r.status),
    };
  } catch (e) {
    return { ok: false, seen: `network error` };
  } finally {
    clearTimeout(timer);
  }
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

// /api/query?backend=auto|<某台 id>
// 先列出各台連線 log（清單來自 API_ORIGINS，不是程式裡寫死的三台），
// 再依指定或自動選一台生成回答。
async function queryRoute(request: Request, platform?: { env?: Env }): Promise<Response> {
  const hosts = hostsOf(platform);
  const want = new URL(request.url).searchParams.get('backend');
  const id = hosts.some((h) => h.id === want) ? want : 'auto';

  const probes = await Promise.all(hosts.map((h) => probe(h.url)));
  const log: Record<string, string> = {};
  const okHosts: string[] = [];
  hosts.forEach((h, i) => {
    log[h.id] = probes[i].ok ? '連線成功' : '連線失敗';
    if (probes[i].ok) okHosts.push(h.id);
  });

  let host: string | null = null;
  let base = '';
  if (id !== 'auto' && okHosts.includes(id)) {
    host = id;
    base = hosts.find((h) => h.id === id)!.url;
  } else if (id === 'auto') {
    const first = okHosts[0] ?? null;
    host = first;
    if (first) base = hosts.find((h) => h.id === first)!.url;
  }
  if (!host || !base) {
    // 帶上每台實際看到的回應。舊版這裡恆為「所有後端皆無法連線」，而那句話
    // 對「Access token 壞掉」是假的 —— 那時三台後端都活著，只是到不了。
    const seen = hosts.map((h, i) => `${h.id}: ${probes[i].seen}`).join('，');
    return json({ ok: false, host: null, log, detail: `所有後端皆無法連線（${seen}）` });
  }

  const body = await request.text();
  try {
    const r = await fetch(`${base}/query`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body,
    });
    const data = await r.json();
    if (!r.ok) return json({ ok: false, host, log, detail: data.detail ?? `後端錯誤 ${r.status}` });
    // 注意：confidence/relevance/trace/no_match 必須透傳，前端要顯示「信心/流程」
    // 與 no_match 專屬區塊（此處只補 host/log/ok，勿把後端欄位過濾掉）。
    return json({
      ok: true, host, log,
      answer: data.answer ?? null,
      hits: data.hits ?? [],
      src: data.src ?? null,
      no_match: data.no_match ?? false,
      confidence: data.confidence ?? null,
      relevance: data.relevance ?? null,
      trace: data.trace ?? null,
    });
  } catch (e) {
    return json({ ok: false, host, log, detail: `轉發失敗：${(e as Error).message}` });
  }
}

export const GET: RequestHandler = async ({ params, request, platform }) => {
  const blocked = await guard(request, parsed(params.path));
  if (blocked) return blocked;
  try {
    return await through('GET', params.path, undefined, platform, request.headers);
  } catch (e) {
    return jsonError(`GET /${params.path} 轉發`, e);
  }
};

export const POST: RequestHandler = async ({ params, request, platform }) => {
  const blocked = await guard(request, parsed(params.path));
  if (blocked) return blocked;
  try {
    if (parsed(params.path) === 'query') return await queryRoute(request, platform);
    return await through('POST', params.path, await request.text(), platform, request.headers);
  } catch (e) {
    return jsonError(`POST /${params.path} 轉發`, e);
  }
};