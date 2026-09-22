const enc = new TextEncoder();
const dec = new TextDecoder();

export interface GoogleUser {
  sub: string;
  email: string;
  name: string;
  picture: string;
}

export function bytesToB64url(b: Uint8Array): string {
  let bin = '';
  for (const x of b) bin += String.fromCharCode(x);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function b64urlToBytes(s: string): Uint8Array {
  const pad = s.replace(/-/g, '+').replace(/_/g, '/');
  const b64 = pad + '='.repeat((4 - (pad.length % 4)) % 4);
  const bin = atob(b64);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

export function randomB64url(n: number): string {
  const u = new Uint8Array(n);
  crypto.getRandomValues(u);
  return bytesToB64url(u);
}

export async function sha256B64url(data: string): Promise<string> {
  return bytesToB64url(new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(data))));
}

export function jsonB64url(o: unknown): string {
  return bytesToB64url(enc.encode(JSON.stringify(o)));
}

let jwksCache: { keys: any[]; at: number } | null = null;

async function googleJwks(): Promise<any[]> {
  if (jwksCache && Date.now() - jwksCache.at < 600_000) return jwksCache.keys;
  const disc = await (await fetch('https://accounts.google.com/.well-known/openid-configuration')).json() as any;
  const res = await (await fetch(disc.jwks_uri)).json() as any;
  jwksCache = { keys: res.keys, at: Date.now() };
  return res.keys;
}

export async function verifyIdToken(token: string, clientId: string): Promise<GoogleUser> {
  const [hS, pS, sS] = token.split('.');
  if (!hS || !pS || !sS) throw new Error('id_token 格式錯誤');
  const header = JSON.parse(dec.decode(b64urlToBytes(hS))) as { alg?: string; kid?: string };
  const payload = JSON.parse(dec.decode(b64urlToBytes(pS))) as {
    iss?: string; aud?: string; exp?: number; sub?: string; email?: string; name?: string; picture?: string;
  };
  if (header.alg !== 'RS256') throw new Error('演算法不支援');
  if (payload.aud !== clientId) throw new Error('aud 不符');
  if (payload.iss !== 'accounts.google.com' && payload.iss !== 'https://accounts.google.com') throw new Error('iss 不符');
  if (!payload.exp || payload.exp * 1000 < Date.now()) throw new Error('已過期');
  const keys = await googleJwks();
  const jwk = keys.find((k) => k.kid === header.kid) as any;
  if (!jwk) throw new Error('無此 kid 的金鑰');
  const key = await crypto.subtle.importKey(
    'jwk',
    { kty: jwk.kty, n: jwk.n, e: jwk.e, kid: jwk.kid, alg: 'RS256' },
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    false,
    ['verify'],
  );
  const ok = await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, b64urlToBytes(sS), enc.encode(hS + '.' + pS));
  if (!ok) throw new Error('簽章驗證失敗');
  return { sub: payload.sub ?? '', email: payload.email ?? '', name: payload.name ?? '', picture: payload.picture ?? '' };
}

export async function exchangeCode(opts: {
  code: string;
  verifier: string;
  redirectUri: string;
  clientId: string;
  clientSecret: string;
}): Promise<string> {
  const params = new URLSearchParams({
    code: opts.code,
    client_id: opts.clientId,
    client_secret: opts.clientSecret,
    redirect_uri: opts.redirectUri,
    code_verifier: opts.verifier,
    grant_type: 'authorization_code',
  });
  const r = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: params,
  });
  const data = await r.json() as { id_token?: string; error?: string; error_description?: string };
  if (!r.ok || !data.id_token) throw new Error(data.error_description ?? 'token 換取失敗');
  return data.id_token;
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}

export async function signSession(claims: object, secret: string): Promise<string> {
  const body = jsonB64url(claims);
  const sig = await crypto.subtle.sign('HMAC', await hmacKey(secret), enc.encode(body));
  return body + '.' + bytesToB64url(new Uint8Array(sig));
}

export async function verifySession(value: string | undefined, secret: string): Promise<GoogleUser | null> {
  if (!value) return null;
  const i = value.lastIndexOf('.');
  if (i < 0) return null;
  const body = value.slice(0, i);
  const sig = b64urlToBytes(value.slice(i + 1));
  let claims: GoogleUser & { exp?: number };
  try {
    claims = JSON.parse(dec.decode(b64urlToBytes(body)));
  } catch {
    return null;
  }
  if (!claims || typeof claims.exp !== 'number' || claims.exp * 1000 < Date.now()) return null;
  const ok = await crypto.subtle.verify('HMAC', await hmacKey(secret), sig, enc.encode(body));
  if (!ok) return null;
  return { sub: claims.sub, email: claims.email, name: claims.name, picture: claims.picture };
}

export function readCookie(header: string | null, name: string): string | undefined {
  if (!header) return undefined;
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    if (k === name) return part.slice(i + 1).trim();
  }
  return undefined;
}