/* One interface for the live API and exported snapshots. No build tools required. */
const data = (() => {
  const root = document.querySelector('meta[name="hn-data"]').content;
  const cache = new Map();
  const json = async url => {
    const response = await fetch(url);
    if (!response.ok) throw Error(`Request failed (${response.status})`);
    return response.json();
  };
  const file = path => {
    if (!cache.has(path)) cache.set(path, json(root + path).catch(error => {
      cache.delete(path);
      throw error;
    }));
    return cache.get(path);
  };
  const topics = () => root ? file('topics.json') : json('/api/topics');
  const resolve = async id => {
    const map = await topics();
    return map.aliases?.[id] ?? Number(id);
  };
  const topic = async id => root
    ? file(`topics/${await resolve(id)}.json`) : json(`/api/topics/${id}`);
  // SQLite LIKE folds ASCII case; %, _ and backslashes are literal search text.
  const fold = value => (value || '').replace(/[A-Z]/g, c => c.toLowerCase());
  const stories = async (id, params) => {
    if (!root) return json(`/api/topics/${id}/stories?${params}`);
    id = await resolve(id);
    const {as_of} = await topics();
    const posts = await file(`stories/${id}.json`);
    const q = fold((params.get('q') || '').trim());
    const days = Number(params.get('days') || 0);
    const year = Number(params.get('year') || 0);
    const offset = Number(params.get('offset') || 0);
    const limit = Number(params.get('limit') || 20);
    const key = {newest:'time', top:'score', discussed:'descendants'}[params.get('sort') || 'newest'];
    const matches = posts.filter(p => (!year || new Date(p.time * 1000).getUTCFullYear() === year) && (!days || p.time >= as_of - days * 86400)
      && (!q || fold(p.title).includes(q) || fold(p.url).includes(q)))
      .sort((a, b) => (a[key] === null) - (b[key] === null) || b[key] - a[key] || b.id - a.id);
    return {id, as_of, posts:matches.slice(offset, offset + limit),
      next_offset:offset + limit < matches.length ? offset + limit : null};
  };
  const digest = async (id, cadence) => root
    ? (await topic(id)).digests[cadence] : json(`/api/topics/${id}/digest?cadence=${cadence}`);
  const timeline = async (id, days = 0) => root
    ? (await file(`timelines/${id == null ? 'all' : await resolve(id)}.json`))[days]
    : json(`${id == null ? '/api/timeline' : `/api/topics/${id}/timeline`}?days=${days}`);
  const timelineZoom = async id => root
    ? (await file(`timelines/${await resolve(id)}.json`)).zoom
    : json(`/api/topics/${id}/timeline?view=zoom`);
  return {topics, topic, stories, digest, timeline, timelineZoom};
})();
