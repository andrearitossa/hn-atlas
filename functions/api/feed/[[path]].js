import {reply} from '../../../lib/reader.js';
export async function onRequest({request, env}) {
  if (!env.FEED) return reply(503, {detail:'Your feed is temporarily unavailable.'});
  return env.FEED.fetch(request);
}
