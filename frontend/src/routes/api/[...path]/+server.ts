import type { RequestHandler } from './$types';
import { env } from '$env/dynamic/private';
import { readCookie, verifySession } from '$lib/google';

const SENSITIVE = new Set(['ingest', 'eval']);

async function guard(request: Request, path: string): Promise<Response | null> {
  if (!SENSITIVE.has(path)) return null;
  const secret = env.SESSION_SECRET;
  if (!secret) {
    return new Response(JSON.stringify({ detail: 'SESSION_SECRET 未設定，無法驗證寫入權限' }), {
      status: 503,
      headers: { 'content-type': 'application/json' },
    });
  }
  const user = await verifySession(readCookie(request.headers.get('cookie'), 'ragdemo_session'), secret);
  if (!user) {
    return new Response(JSON.stringify({ detail: '請先登入（僅登入者可寫入）' }), {
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
}

function originOf(platform?: { env?: Env }): string {
  if (platform?.env?.API_ORIGIN) return platform.env.API_ORIGIN;
  const pe = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  if (pe?.env?.API_ORIGIN) return pe.env.API_ORIGIN;
  return '';
}

async function through(method: string, path: string, body: string | undefined, platform?: { env?: Env }): Promise<Response> {
  const origin = originOf(platform).replace(/\/$/, '');
  if (!origin) {
    return new Response(JSON.stringify({ detail: 'API_ORIGIN 未設定（請在 Cloudflare Pages 變數加 API_ORIGIN）' }), {
      status: 503,
      headers: { 'content-type': 'application/json' },
    });
  }
  const init: RequestInit = { method, headers: { 'content-type': 'application/json' } };
  if (body !== undefined) init.body = body;
  const r = await fetch(`${origin}/${path}`, init);
  return new Response(r.body, {
    status: r.status,
    headers: { 'content-type': r.headers.get('content-type') ?? 'application/json' },
  });
}

export const GET: RequestHandler = async ({ params, request, platform }) => {
  const blocked = await guard(request, parsed(params.path));
  if (blocked) return blocked;
  return through('GET', params.path, undefined, platform);
};

export const POST: RequestHandler = async ({ params, request, platform }) => {
  const blocked = await guard(request, parsed(params.path));
  if (blocked) return blocked;
  return through('POST', params.path, await request.text(), platform);
};