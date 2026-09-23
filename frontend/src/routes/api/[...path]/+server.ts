import type { RequestHandler } from './$types';
import { env } from '$env/dynamic/private';
import { readCookie, verifySession } from '$lib/google';

const SENSITIVE = new Set(['query', 'ingest', 'eval']);

// 公網後端依優先序（自動模式依此順序即時備援：先通者勝）。
// 可用 Pages 變數 API_ORIGINS（逗號分隔）覆寫；未設則用內建三台。
const DEFAULT_ORIGINS = [
  'https://api-x570.ragdemo.win',
  'https://api-mbp.ragdemo.win',
  'https://api-msi.ragdemo.win',
];

// /query 連線 log 用的三台識別（id → 公網 URL）。
const HOSTS = [
  { id: 'x570', url: DEFAULT_ORIGINS[0] },
  { id: 'mbp', url: DEFAULT_ORIGINS[1] },
  { id: 'msi', url: DEFAULT_ORIGINS[2] },
];

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

// 展開成依序清單：API_ORIGINS 優先；其次 API_ORIGIN 若命中我們三台之一，
// 就展開成完整三台（一律 x570 優先，符合同一優先序）；其餘自架單一台原樣。
function originsOf(platform?: { env?: Env }): string[] {
  const fromEnv = (s?: string) => (s || '').split(',').map((x) => x.trim().replace(/\/$/, '')).filter(Boolean);
  const pe = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  if (platform?.env?.API_ORIGINS) return fromEnv(platform.env.API_ORIGINS);
  if (pe?.env?.API_ORIGINS) return fromEnv(pe.env.API_ORIGINS);
  const singleA = (platform?.env?.API_ORIGIN || '').trim().replace(/\/$/, '');
  const singleB = (pe?.env?.API_ORIGIN || '').trim().replace(/\/$/, '');
  const single = singleA || singleB;
  if (single) {
    if (DEFAULT_ORIGINS.includes(single)) return [...DEFAULT_ORIGINS];
    return [single];
  }
  return DEFAULT_ORIGINS;
}

// 判斷該後端是否「已離線（不值得重試）」：網路層失敗，或 Cloudflare
// tunnel 離線的典型回應（502/503/504/530/1033）。其他 4xx（如 401/404）視為
// 後端正常回應，直接回傳不切換。
function dead(status: number): boolean {
  return status === 502 || status === 503 || status === 504 || status === 530 || status === 1033;
}

async function through(method: string, path: string, body: string | undefined, platform?: { env?: Env }): Promise<Response> {
  const origins = originsOf(platform);
  if (origins.length === 0) {
    return new Response(JSON.stringify({ detail: 'API_ORIGINS/API_ORIGIN 未設定（請在 Cloudflare Pages 變數設定）' }), {
      status: 503,
      headers: { 'content-type': 'application/json' },
    });
  }
  const init: RequestInit = { method, headers: { 'content-type': 'application/json' } };
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

// /api/query?backend=auto|x570|mbp|msi
// 先列出三台連線 log，再依指定或自動選一台生成回答。
async function queryRoute(request: Request, platform?: { env?: Env }): Promise<Response> {
  const want = new URL(request.url).searchParams.get('backend');
  const id = HOSTS.some((h) => h.id === want) ? want : 'auto';

  const probes = await Promise.all(HOSTS.map((h) => probe(h.url)));
  const log: Record<string, string> = {};
  const okHosts: string[] = [];
  HOSTS.forEach((h, i) => {
    log[h.id] = probes[i] ? '連線成功' : '連線失敗';
    if (probes[i]) okHosts.push(h.id);
  });

  let host: string | null = null;
  let base = '';
  if (id !== 'auto' && okHosts.includes(id)) {
    host = id;
    base = HOSTS.find((h) => h.id === id)!.url;
  } else if (id === 'auto') {
    const first = okHosts[0] ?? null;
    host = first;
    if (first) base = HOSTS.find((h) => h.id === first)!.url;
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
    return json({ ok: true, host, log, answer: data.answer, hits: data.hits, src: data.src ?? null });
  } catch (e) {
    return json({ ok: false, host, log, detail: `轉發失敗：${(e as Error).message}` });
  }
}

export const GET: RequestHandler = async ({ params, request, platform }) => {
  const blocked = await guard(request, parsed(params.path));
  if (blocked) return blocked;
  return through('GET', params.path, undefined, platform);
};

export const POST: RequestHandler = async ({ params, request, platform }) => {
  const blocked = await guard(request, parsed(params.path));
  if (blocked) return blocked;
  if (parsed(params.path) === 'query') return queryRoute(request, platform);
  return through('POST', params.path, await request.text(), platform);
};