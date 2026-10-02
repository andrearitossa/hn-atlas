import {database, reader, profile, reply, jsonBody, HTTPError, seconds, rate, errorReply} from '../../lib/reader.js';
import {rankFeed} from './ranking.js';
const RETENTION = 90 * 86400;
export async function handle(request, env) {
  try {
    const db = database(env), user = await reader(db, request), url = new URL(request.url), now = seconds();
    const path = url.pathname.replace(/\/$/, '');
    if (path === '/api/feed/events' && request.method === 'POST') {
      const body = await jsonBody(request);
      if (!Array.isArray(body.events) || body.events.length < 1 || body.events.length > 20
        || body.events.some(e => !e || !/^[a-f0-9]{64}$/.test(e.article_key || '')
          || !['visible', 'article_opened'].includes(e.type))) throw new HTTPError(400, 'Invalid reading activity.');
      await rate(db, 'events:' + user.id, 60, 60);
      // Account-scoped feedback, not proof of reading. Canonical keys survive daily catalog replacement.
      await db.batch(body.events.map(e => db.prepare(`INSERT INTO feed_story_state(user_id,article_key,seen_at,opened_at)
        VALUES(?,?,?,?) ON CONFLICT(user_id,article_key) DO UPDATE SET
        seen_at=max(seen_at,excluded.seen_at),opened_at=coalesce(opened_at,excluded.opened_at)`)
        .bind(user.id, e.article_key, now, e.type === 'article_opened' ? now : null)));
      return reply(200, {ok:true});
    }
    if (path !== '/api/feed') throw new HTTPError(404, 'Not found.');
    if (request.method !== 'GET') return reply(405, {detail:'Method not allowed.'}, {Allow:'GET'});
    const current = await profile(db, user);
    if (!current.topics.length) return reply(200, {items:[], next_cursor:null, caught_up:false, topics_required:true});
    const catalog = await db.prepare('SELECT payload FROM feed_catalog WHERE id=1').first();
    if (!catalog) throw new HTTPError(503, 'Stories are being prepared. Please try again shortly.');
    const data = JSON.parse(catalog.payload);
    const ranked = rankFeed(data, current.topics);
    let start = 0;
    if (url.searchParams.has('cursor')) {
      let cursor;
      try {
        const raw = url.searchParams.get('cursor');
        if (raw.length > 1024) throw Error();
        cursor = JSON.parse(atob(raw));
        if (!cursor || !Number.isSafeInteger(cursor.after)) throw Error();
      } catch { throw new HTTPError(400, 'Invalid feed cursor.'); }
      if (cursor.edition !== data.edition || cursor.version !== current.version) {
        return reply(409, {detail:'Your feed has been updated.', code:'feed_changed'});
      }
      start = ranked.findIndex(p => p.id === cursor.after) + 1;
      if (!start) throw new HTTPError(400, 'Invalid feed cursor.');
    }
    const {results} = await db.prepare(`SELECT article_key FROM feed_story_state WHERE user_id=? AND seen_at>=?
      AND article_key IN (SELECT value FROM json_each(?))`)
      .bind(user.id, now - RETENTION, JSON.stringify(ranked.map(p => p.article_key))).all();
    const seen = new Set(results.map(row => row.article_key));
    const available = ranked.slice(start).filter(p => !seen.has(p.article_key));
    const items = available.slice(0, 20);
    const next_cursor = available.length > 20 ? btoa(JSON.stringify({edition:data.edition,version:current.version,after:items.at(-1).id})) : null;
    return reply(200, {items, topics:data.topics, updated_at:data.as_of, next_cursor, caught_up:!next_cursor});
  } catch (error) { return errorReply(error); }
}
export default {fetch:handle};
