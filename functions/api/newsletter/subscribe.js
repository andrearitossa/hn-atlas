const reply = (status, body) => Response.json(body, {
  status, headers: {'Cache-Control': 'no-store'},
});

export async function onRequest({request, env, waitUntil}) {
  if (request.method !== 'POST') {
    return new Response(null, {status: 405, headers: {Allow: 'POST'}});
  }
  const origin = new URL(request.url).origin;
  if (request.headers.get('Origin') !== origin) {
    return reply(403, {detail: 'Please sign up from the topic page.'});
  }
  if (request.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') {
    return reply(415, {detail: 'Expected JSON.'});
  }
  let data;
  try {
    // Bound actual bytes, including requests without Content-Length.
    const reader = request.body?.getReader();
    if (!reader) return reply(400, {detail: 'Missing signup details.'});
    const chunks = [];
    let size = 0;
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 2048) {
        await reader.cancel();
        return reply(413, {detail: 'Signup details are too long.'});
      }
      chunks.push(value);
    }
    data = JSON.parse(await new Blob(chunks).text());
  } catch {
    return reply(400, {detail: 'Invalid signup details.'});
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return reply(400, {detail: 'Invalid signup details.'});
  }
  if (data.website) return reply(200, {ok: true}); // Honeypot; never store bots.
  const email = typeof data.email === 'string' ? data.email.trim().toLowerCase() : '';
  if (email.length > 254 || !/^[^\s@\x00-\x1f\x7f]+@[^\s@\x00-\x1f\x7f]+\.[^\s@\x00-\x1f\x7f]+$/.test(email)) {
    return reply(400, {detail: 'Enter a valid email address.'});
  }
  if (!Number.isSafeInteger(data.topic) || data.topic <= 0) {
    return reply(400, {detail: 'Choose a valid topic.'});
  }
  try {
    const response = await env.ASSETS.fetch(new URL('/newsletter-topics.json', origin));
    if (!response.ok) throw new Error('Topic catalog unavailable');
    const topics = await response.json();
    if (typeof topics[String(data.topic)] !== 'string') return reply(400, {detail: 'This topic is no longer available.'});
    const saved = await env.NEWSLETTER_DB.prepare(
      'INSERT INTO newsletter_subscriptions (id, email, topic, created_at) VALUES (?, ?, ?, ?) '
      + 'ON CONFLICT(email, topic) DO NOTHING'
    ).bind(crypto.randomUUID(), email, data.topic, Math.floor(Date.now() / 1000)).run();
    if (saved.meta.changes === 1) {
      const notification = Promise.resolve().then(() => env.NOTIFICATIONS.notify({type: 'signup', email, topic: topics[String(data.topic)]}))
        .catch(() => console.error('Admin notification failed: signup'));
      if (waitUntil) waitUntil(notification);
      else await notification;
    }
    return reply(200, {ok: true});
  } catch {
    // Do not log request bodies or email addresses.
    return reply(503, {detail: 'Signup is temporarily unavailable. Please try again shortly.'});
  }
}
