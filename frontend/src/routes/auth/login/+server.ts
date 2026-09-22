import type { RequestHandler } from './$types';
import { env } from '$env/dynamic/private';
import { randomB64url, sha256B64url } from '$lib/google';

export const GET: RequestHandler = async ({ request }) => {
  const clientId = env.GOOGLE_CLIENT_ID;
  if (!clientId) {
    return new Response(JSON.stringify({ detail: 'GOOGLE_CLIENT_ID 未設定' }), {
      status: 503,
      headers: { 'content-type': 'application/json' },
    });
  }
  const verifier = randomB64url(32);
  const challenge = await sha256B64url(verifier);
  const state = randomB64url(16);
  const redirectUri = new URL(request.url).origin + '/auth/callback';

  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: 'code',
    scope: 'openid email profile',
    code_challenge: challenge,
    code_challenge_method: 'S256',
    state,
  });

  const headers = new Headers({ Location: 'https://accounts.google.com/o/oauth2/v2/auth?' + params });
  headers.append('Set-Cookie', `ragdemo_verifier=${verifier}; HttpOnly; Path=/; Max-Age=600; SameSite=Lax`);
  headers.append('Set-Cookie', `ragdemo_state=${state}; HttpOnly; Path=/; Max-Age=600; SameSite=Lax`);
  return new Response(null, { status: 302, headers });
};