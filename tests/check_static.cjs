// Exercise the actual browser adapter against exported files, including subpath hosting.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const [output, expectations] = process.argv.slice(2);
const html = fs.readFileSync(path.join(output, 'index.html'), 'utf8');
const root = html.match(/name="hn-data" content="([^"]+)"/)[1];
const script = html.match(/src="([^"]+data.js)"/)[1];
const requests = [];
const baseURI = 'https://example.test/';
const absoluteRoot = new URL(root, baseURI).href;
const context = vm.createContext({
  URL,
  document: {baseURI, querySelector: () => ({content:root})},
  fetch: async url => {
    requests.push(url);
    assert.ok(url.startsWith(absoluteRoot), `Unexpected request: ${url}`);
    const file = path.join(output, new URL(url).pathname);
    return {ok:fs.existsSync(file), status:404, json:async () => JSON.parse(fs.readFileSync(file, 'utf8'))};
  },
});
vm.runInContext(fs.readFileSync(path.join(output, script), 'utf8'), context);
const data = vm.runInContext('data', context);
const plain = value => JSON.parse(JSON.stringify(value));
(async () => {
  for (const {query, expected} of JSON.parse(fs.readFileSync(expectations))) {
    assert.deepEqual(plain(await data.stories(3, new URLSearchParams(query))), expected);
  }
  assert.equal((await data.timelineZoom(3)).id, 3);
  assert.ok((await data.timelineZoom(3)).levels.week.length);
  assert.equal((await data.timeline(3)).interval, 'year');
  assert.equal((await data.timeline(3)).id, 3);
  assert.equal((await data.timeline(3, 30)).id, 3);
  assert.equal((await data.timeline(null, 365)).interval, 'month');
  assert.equal((await data.topic(3)).id, 3);
  assert.equal(requests.filter(url => url.endsWith('stories/3.json')).length, 1);
  await assert.rejects(data.topic(999));
  await assert.rejects(data.topic(999));
  assert.equal(requests.filter(url => url.endsWith('topics/999.json')).length, 2);
  await data.stories(3, new URLSearchParams('days=30'));
  assert.ok(requests.some(url => url.endsWith('recent/3.json')));
  // Parse the page's inline JS too, catching accidental integration syntax errors.
  new vm.Script(html.match(/<script>\n([\s\S]*?)<\/script>/)[1]);
  console.log('Static browser queries match the API.');
})().catch(error => { console.error(error); process.exitCode = 1; });
