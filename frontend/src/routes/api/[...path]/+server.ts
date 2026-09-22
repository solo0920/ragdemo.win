import type { RequestHandler } from './$types';

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

export const GET: RequestHandler = ({ params, platform }) => through('GET', params.path, undefined, platform);

export const POST: RequestHandler = async ({ params, request, platform }) =>
  through('POST', params.path, await request.text(), platform);