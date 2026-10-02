import {database, reader, profile, reply, jsonBody, HTTPError, token, hash, seconds,
  sessionCookie, rate, topicCatalog, errorReply} from '../../../lib/reader.js';

export async function onRequest({request, env}) {
  try {
    const db = database(env), now = seconds();
    const action = new URL(request.url).pathname.replace(/^\/api\/reader\/?/, '').replace(/\/$/, '');
    if (action === 'login' && request.method === 'POST') {
      const body = await jsonBody(request);
      const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : '';
      if (email.length > 254 || !/^[^\s@\x00-\x1f\x7f]+@[^\s@\x00-\x1f\x7f]+\.[^\s@\x00-\x1f\x7f]+$/.test(email)) {
        throw new HTTPError(400, 'Enter a valid email address.');
      }
      if (body.website) return reply(200, {ok: true});
      await rate(db, 'login-ip:' + await hash(request.headers.get('CF-Connecting-IP') || 'local'), 10, 3600);
      await rate(db, 'login-email:' + await hash(email), 1, 60);
      await rate(db, 'login-email-hour:' + await hash(email), 5, 3600);
      const secret = token(), digest = await hash(secret);
      await db.prepare('INSERT INTO feed_login_tokens(token_hash,email,expires_at) VALUES(?,?,?)')
        .bind(digest, email, now + 15 * 60).run();
      try {
        await env.NOTIFICATIONS.sendLogin({email, url: new URL('/for-you/#token=' + secret, request.url).href});
      } catch {
        await db.prepare('DELETE FROM feed_login_tokens WHERE token_hash=?').bind(digest).run();
        throw new HTTPError(503, 'We could not send your sign-in email. Please try again shortly.');
      }
      return reply(200, {ok: true});
    }
    if (action === 'verify' && request.method === 'POST') {
      const body = await jsonBody(request, 1024);
      if (!/^[a-f0-9]{64}$/.test(body.token || '')) throw new HTTPError(400, 'This sign-in link is invalid. Request a new one.');
      await rate(db, 'verify-ip:' + await hash(request.headers.get('CF-Connecting-IP') || 'local'), 30, 60);
      const digest = await hash(body.token), session = token(), sessionHash = await hash(session);
      // The unique consumption claim makes replay and concurrent redemption harmless.
      const result = await db.batch([
        db.prepare('UPDATE feed_login_tokens SET consumed_at=?,consumed_by=? WHERE token_hash=? AND consumed_at IS NULL AND expires_at>?')
          .bind(now, sessionHash, digest, now),
        db.prepare(`INSERT INTO feed_users(id,email,created_at,verified_at)
          SELECT ?,email,?,? FROM feed_login_tokens WHERE token_hash=? AND consumed_by=?
          ON CONFLICT(email) DO NOTHING`).bind(crypto.randomUUID(), now, now, digest, sessionHash),
        db.prepare(`INSERT INTO feed_sessions(token_hash,user_id,expires_at)
          SELECT ?,u.id,? FROM feed_login_tokens t JOIN feed_users u ON u.email=t.email
          WHERE t.token_hash=? AND t.consumed_by=?`).bind(sessionHash, now + 30 * 86400, digest, sessionHash),
      ]);
      if (result[2].meta.changes !== 1) throw new HTTPError(401, 'This sign-in link has expired or was already used. Request a new one.');
      return reply(200, {ok: true}, {'Set-Cookie': sessionCookie(session)});
    }
    const user = await reader(db, request);
    if (action === 'logout' && request.method === 'POST') {
      await jsonBody(request, 1024);
      await db.prepare('DELETE FROM feed_sessions WHERE token_hash=?').bind(user.session_hash).run();
      return reply(200, {ok: true}, {'Set-Cookie': sessionCookie('', 0)});
    }
    if (action) throw new HTTPError(404, 'Not found.');
    if (request.method === 'GET') return reply(200, await profile(db, user));
    if (request.method === 'PUT') {
      const body = await jsonBody(request);
      if (!Array.isArray(body.topics) || body.topics.length < 1
        || !body.topics.every(id => Number.isSafeInteger(id) && id >= 0)
        || new Set(body.topics).size !== body.topics.length || !Number.isSafeInteger(body.version)) {
        throw new HTTPError(400, 'Choose at least one topic.');
      }
      const catalog = new Set((await topicCatalog(env, request)).map(t => t.id));
      if (body.topics.some(id => !catalog.has(id))) throw new HTTPError(400, 'Choose a current topic.');
      const saved = await db.prepare(`UPDATE feed_users SET topics_json=?,version=version+1 WHERE id=? AND version=?
        RETURNING email,version,topics_json`).bind(JSON.stringify(body.topics), user.id, body.version).first();
      if (!saved) throw new HTTPError(409, 'Your interests changed in another tab. Reload and try again.');
      return reply(200, {id:user.id, email: saved.email, version: saved.version, topics: JSON.parse(saved.topics_json)});
    }
    if (request.method === 'DELETE') {
      await jsonBody(request, 1024);
      // Tokens are keyed by email, while all profile-owned rows cascade on deletion.
      await db.batch([
        db.prepare('DELETE FROM feed_login_tokens WHERE email=?').bind(user.email),
        db.prepare('DELETE FROM feed_rate_limits WHERE key LIKE ?').bind('events:' + user.id + ':%'),
        db.prepare('DELETE FROM feed_users WHERE id=?').bind(user.id),
      ]);
      return reply(200, {ok: true}, {'Set-Cookie': sessionCookie('', 0)});
    }
    return reply(405, {detail: 'Method not allowed.'}, {Allow: 'GET, PUT, DELETE'});
  } catch (error) { return errorReply(error); }
}
