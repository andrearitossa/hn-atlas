import {jsonBody, HTTPError} from '../../lib/reader.js';
const reply = (status, body) => Response.json(body, {
  status, headers: {'Cache-Control': 'no-store'},
});

export async function onRequest({request, env, waitUntil}) {
  if (request.method !== 'POST') {
    return new Response(null, {status: 405, headers: {Allow: 'POST'}});
  }
  let data;
  try { data = await jsonBody(request, 12000, {origin: 'Please send feedback from the website.', missing: 'Missing feedback.', tooLarge: 'Feedback is too long.', invalid: 'Invalid feedback.', object: 'Invalid feedback.'}); }
  catch (error) { return reply(error.status || 400, {detail: error instanceof HTTPError ? error.message : 'Invalid feedback.'}); }
  if (data.website) return reply(200, {ok: true});
  const message = typeof data.message === 'string' ? data.message.trim() : '';
  const email = typeof data.email === 'string' ? data.email.trim().toLowerCase() : '';
  if (!message || message.length > 2000) {
    return reply(400, {detail: 'Write a message of up to 2,000 characters.'});
  }
  if (email && (email.length > 254 || !/^[^\s@\x00-\x1f\x7f]+@[^\s@\x00-\x1f\x7f]+\.[^\s@\x00-\x1f\x7f]+$/.test(email))) {
    return reply(400, {detail: 'Enter a valid email or leave it blank.'});
  }
  if (typeof data.id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(data.id)) {
    return reply(400, {detail: 'Please reopen the feedback form.'});
  }
  const page = typeof data.page === 'string' && /^(?:#\/(?:connections|topics|topic\/\d+)?|\/(?:#\/(?:topics)?|topic\/[a-z0-9-]+\/)?)$/.test(data.page) ? data.page : '#/';
  try {
    const saved = await env.NEWSLETTER_DB.prepare(
      'INSERT INTO feedback (id, message, email, page) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING'
    ).bind(data.id, message, email || null, page).run();
    if (saved.meta.changes === 1) {
      const notification = Promise.resolve().then(() => env.NOTIFICATIONS.notify({type: 'feedback', message, email, page}))
        .catch(() => console.error('Admin notification failed: feedback'));
      if (waitUntil) waitUntil(notification);
      else await notification;
    }
    return reply(200, {ok: true});
  } catch {
    return reply(503, {detail: 'Could not save your feedback. Please try again.'});
  }
}
