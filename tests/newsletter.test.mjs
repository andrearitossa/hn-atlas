import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {test} from 'node:test';

const source = await readFile(new URL('../functions/api/newsletter/subscribe.js', import.meta.url), 'utf8');
const {onRequest} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const origin = 'https://hackeratlas.pages.dev';

function setup({body = {email: ' Reader@Example.com ', topic: 7}, headers = {}, method = 'POST', fail = false} = {}) {
  const writes = [];
  const request = new Request(`${origin}/api/newsletter/subscribe`, {
    method, headers: {Origin: origin, 'Content-Type': 'application/json', ...headers},
    ...(method === 'POST' ? {body: typeof body === 'string' ? body : JSON.stringify(body)} : {}),
  });
  const env = {
    ASSETS: {fetch: async () => Response.json({'7': 'Programming'})},
    NEWSLETTER_DB: {prepare: sql => ({bind: (...args) => ({run: async () => {
      if (fail) throw Error('private storage error');
      writes.push({sql, args});
    }})})},
  };
  return {writes, response: onRequest({request, env})};
}

test('normalizes email, resolves topic on server and uses bound SQL', async () => {
  const {response, writes} = setup();
  const result = await response;
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), {ok: true});
  assert.equal(result.headers.get('Cache-Control'), 'no-store');
  assert.match(writes[0].args[0], /^[0-9a-f-]{36}$/);
  assert.equal(writes[0].args[1], 'reader@example.com');
  assert.equal(writes[0].args[2], 7);
  assert.ok(writes[0].args[3] > 0);
  assert.match(writes[0].sql, /ON CONFLICT\(email, topic\) DO NOTHING/);
});

test('rejects invalid requests without touching storage', async () => {
  for (const [options, status] of [
    [{method: 'GET'}, 405],
    [{headers: {Origin: 'https://other.example'}}, 403],
    [{headers: {'Content-Type': 'text/plain'}}, 415],
    [{body: '{'}, 400], [{body: null}, 400], [{body: []}, 400],
    [{body: {email: 'bad', topic: 7}}, 400],
    [{body: {email: 'a@example.com', topic: '7'}}, 400],
    [{body: {email: 'a@example.com', topic: 999}}, 400],
    [{body: 'x'.repeat(2049)}, 413],
    [{body: {email: 'a@example.com', topic: 7, website: 'spam'}}, 200],
  ]) {
    const {response, writes} = setup(options);
    assert.equal((await response).status, status, JSON.stringify(options));
    assert.equal(writes.length, 0);
  }
});

test('storage failure is reported without a false success or private details', async () => {
  const {response} = setup({fail: true});
  const result = await response;
  assert.equal(result.status, 503);
  assert.doesNotMatch(await result.text(), /private storage error|Reader/);
});
