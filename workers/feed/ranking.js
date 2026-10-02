export function rankFeed(data, interests, limit = Infinity) {
  const selected = new Set(interests), seen = new Set();
  return data.posts.filter(p => p.topics.some(id => selected.has(id)) && p.time <= data.as_of
    && p.time >= data.as_of - 30 * 86400 && p.hn_points >= 5)
    .sort((a, b) => (Math.log(b.hn_points - 1) - (data.as_of - b.time) / (3 * 86400))
      - (Math.log(a.hn_points - 1) - (data.as_of - a.time) / (3 * 86400)) || b.id - a.id)
    .filter(p => { const key = p.article_key || String(p.id); if (seen.has(key)) return false; seen.add(key); return true; })
    .slice(0, limit);
}
