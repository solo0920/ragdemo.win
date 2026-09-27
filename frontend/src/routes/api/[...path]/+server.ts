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
      const h = new Headers(r.headers);
      h.set('content-type', r.headers.get('content-type') ?? 'application/json');
      h.set('x-ragdemo-origin', origin); // 前端可顯示「自動→實際服務主機」
      return new Response(r.body, { status: r.status, headers: h });
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

async function probe(url: string): Promise<boolean> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 6000);
  try {
    const r = await fetch(`${url}/health`, { signal: ctrl.signal });
    return r.ok;
  } catch {
    return false;
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
    log[h.id] = probes[i] ? '連線成功' : '連線失敗';
    if (probes[i]) okHosts.push(h.id);
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
    return json({ ok: false, host: null, log, detail: '所有後端皆無法連線' });
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
  return through('GET', params.path, undefined, platform, request.headers);
};

export const POST: RequestHandler = async ({ params, request, platform }) => {
  const blocked = await guard(request, parsed(params.path));
  if (blocked) return blocked;
  if (parsed(params.path) === 'query') return queryRoute(request, platform);
  return through('POST', params.path, await request.text(), platform, request.headers);
};