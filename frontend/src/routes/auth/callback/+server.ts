import type { RequestHandler } from './$types';
import { env } from '$env/dynamic/private';
import {
  exchangeCode,
  readCookie,
  signSession,
  verifyIdToken,
} from '$lib/google';

const SESSION_TTL_MS = 12 * 60 * 60 * 1000;

export const GET: RequestHandler = async ({ request, url }) => {
  const clientId = env.GOOGLE_CLIENT_ID;
  const clientSecret = env.GOOGLE_CLIENT_SECRET;
  const secret = env.SESSION_SECRET;
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');

  if (!clientId || !clientSecret || !secret) {
    return new Response(JSON.stringify({ detail: 'GOOGLE_* 或 SESSION_SECRET 未設定' }), {
      status: 503,
      headers: { 'content-type': 'application/json' },
    });
  }
  function go(tag: string) {
    return new Response(null, {
      status: 302,
      headers: { Location: '/?auth=' + tag },
    });
  }
  if (!code || !state) return go('missing');
  const cookie = request.headers.get('cookie');
  const stateCookie = readCookie(cookie, 'ragdemo_state');
  const verifier = readCookie(cookie, 'ragdemo_verifier');
  if (!stateCookie || stateCookie !== state) return go('csrf');
  if (!verifier) return go('csrferr');

  const redirectUri = new URL(request.url).origin + '/auth/callback';
  let user;
  try {
    const idToken = await exchangeCode({ code, verifier, redirectUri, clientId, clientSecret });
    user = await verifyIdToken(idToken, clientId);
  } catch (e) {
    return go('exchange');
  }

  const session = await signSession(
    { sub: user.sub, email: user.email, name: user.name, picture: user.picture, exp: Math.floor(Date.now() / 1000) + SESSION_TTL_MS / 1000 },
    secret,
  );
  const secure = url.protocol === 'https:';
  const headers = new Headers({ Location: '/' });
  headers.append(
    'Set-Cookie',
    `ragdemo_session=${session}; HttpOnly; Path=/; Max-Age=${SESSION_TTL_MS / 1000}; SameSite=Lax${secure ? '; Secure' : ''}`,
  );
  headers.append('Set-Cookie', 'ragdemo_verifier=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax');
  headers.append('Set-Cookie', 'ragdemo_state=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax');
  return new Response(null, { status: 302, headers });
};