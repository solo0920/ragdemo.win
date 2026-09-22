import type { RequestHandler } from './$types';
import { env } from '$env/dynamic/private';
import { readCookie, verifySession } from '$lib/google';

export const GET: RequestHandler = async ({ request }) => {
  const secret = env.SESSION_SECRET;
  if (!secret) return new Response(JSON.stringify({ ok: false, reason: 'SESSION_SECRET 未設定' }), {
    status: 503,
    headers: { 'content-type': 'application/json' },
  });
  const user = await verifySession(readCookie(request.headers.get('cookie'), 'ragdemo_session'), secret);
  if (!user) return new Response(JSON.stringify({ ok: false }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
  return new Response(JSON.stringify({ ok: true, user }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
};