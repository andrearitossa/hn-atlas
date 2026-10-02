export const COOKIE = '__Host-atlas-session';
export const seconds = () => Math.floor(Date.now() / 1000);
export const token = () => Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, '0')).join('');
export async function hash(value) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))),
    b => b.toString(16).padStart(2, '0')).join('');
}
export class HTTPError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}
export function reply(status, body, headers = {}) {
  return Response.json(body, {status, headers: {'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer', ...headers}});
}
export const sessionCookie = (value, age = 30 * 86400) =>
  `${COOKIE}=${value}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=${age}`;
export function database(env) {
  if (!env.NEWSLETTER_DB) throw new HTTPError(503, 'Your feed is temporarily unavailable. Try again shortly.');
  return env.NEWSLETTER_DB.withSession ? env.NEWSLETTER_DB.withSession('first-primary') : env.NEWSLETTER_DB;
}
export async function jsonBody(request, maximum = 16000) {
  if (request.headers.get('Origin') !== new URL(request.url).origin) throw new HTTPError(403, 'Use this feature from Hacker Atlas.');
  if (request.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') throw new HTTPError(415, 'Expected JSON.');
  const reader = request.body?.getReader();
  if (!reader) throw new HTTPError(400, 'Missing request.');
  let size = 0; const chunks = [];
  while (true) {
    const {done, value} = await reader.read(); if (done) break;
    size += value.length;
    if (size > maximum) { await reader.cancel(); throw new HTTPError(413, 'Request is too large.'); }
    chunks.push(value);
  }
  let body;
  try { body = JSON.parse(await new Blob(chunks).text()); } catch { throw new HTTPError(400, 'Expected JSON.'); }
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new HTTPError(400, 'Expected a request object.');
  return body;
}
export async function rate(db, key, maximum, windowSeconds, now = seconds()) {
  const bucket = Math.floor(now / windowSeconds);
  const row = await db.prepare(`INSERT INTO feed_rate_limits(key,hits,expires_at) VALUES(?,1,?)
    ON CONFLICT(key) DO UPDATE SET hits=hits+1 WHERE hits<? RETURNING hits`)
    .bind(`${key}:${bucket}`, (bucket + 1) * windowSeconds, maximum).first();
  if (!row) throw new HTTPError(429, 'Please wait a moment before trying again.');
}
export async function reader(db, request) {
  const value = request.headers.get('Cookie')?.split(';').map(p => p.trim())
    .find(p => p.startsWith(COOKIE + '='))?.slice(COOKIE.length + 1);
  if (!/^[a-f0-9]{64}$/.test(value || '')) throw new HTTPError(401, 'Sign in to see your feed.');
  const user = await db.prepare(`SELECT u.id,u.email,u.version FROM feed_sessions s JOIN feed_users u ON u.id=s.user_id
    WHERE s.token_hash=? AND s.expires_at>?`).bind(await hash(value), seconds()).first();
  if (!user) throw new HTTPError(401, 'Sign in to see your feed.');
  return {...user, session_hash: await hash(value)};
}
export async function profile(db, user) {
  const current = await db.prepare('SELECT email,version FROM feed_users WHERE id=?').bind(user.id).first();
  if (!current) throw new HTTPError(401, 'Sign in to see your feed.');
  const {results} = await db.prepare('SELECT topic_id FROM feed_user_topics WHERE user_id=? ORDER BY topic_id').bind(user.id).all();
  return {id:user.id, ...current, topics: results.map(row => row.topic_id)};
}
export async function topicCatalog(env, request) {
  const response = await env.ASSETS.fetch(new URL('/discovery-topics.json', request.url));
  if (!response.ok) throw new HTTPError(503, 'Topics are temporarily unavailable.');
  return (await response.json()).topics;
}
export function errorReply(error) {
  if (!(error instanceof HTTPError)) console.warn('Reader operation unavailable');
  return reply(error.status || 503, {detail: error instanceof HTTPError ? error.message : 'Your feed is temporarily unavailable. Try again shortly.'});
}
