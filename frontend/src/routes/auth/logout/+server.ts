import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url }) => {
  const secure = url.protocol === 'https:';
  const headers = new Headers({ Location: '/' });
  headers.append('Set-Cookie', `ragdemo_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax${secure ? '; Secure' : ''}`);
  return new Response(null, { status: 302, headers });
};