import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {test} from 'node:test';
const source = await readFile(new URL('../functions/api/feedback.js', import.meta.url), 'utf8');
const {onRequest} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const origin = 'https://hackeratlas.com';
const valid = {id:'8e551e6f-9b03-4a6d-83f1-fd06519540b9', message:' Useful! ', page:'#/topic/71'};
async function run(body = valid, options = {}) {
  const writes = [];
  const request = new Request(origin + '/api/feedback', {
    method:options.method || 'POST',
    headers:{Origin:origin, 'Content-Type':'application/json', ...options.headers},
    ...(options.method === 'GET' ? {} : {body:typeof body === 'string' ? body : JSON.stringify(body)}),
  });
  const env = {NEWSLETTER_DB:{prepare:sql => ({bind:(...args) => ({run:async () => {
    if (options.fail) throw Error('private failure');
    writes.push({sql,args});
  }})})}};
  return {response:await onRequest({request,env}), writes};
}
test('anonymous feedback is stored with parameterized SQL and retry protection', async () => {
  const {response,writes} = await run();
  assert.equal(response.status,200);
  assert.equal(response.headers.get('Cache-Control'),'no-store');
  assert.deepEqual(writes[0].args,[valid.id,'Useful!',null,'#/topic/71']);
  assert.match(writes[0].sql,/ON CONFLICT\(id\) DO NOTHING/);
});
test('optional email is normalized and unsafe page context is discarded', async () => {
  const {writes} = await run({...valid,email:' Reader@Example.com ',page:'https://other.test/?secret=yes'});
  assert.deepEqual(writes[0].args,[valid.id,'Useful!','reader@example.com','#/']);
});
test('invalid requests never write feedback', async () => {
  for (const [body,options,status] of [
    [valid,{method:'GET'},405], [valid,{headers:{Origin:'https://other.test'}},403],
    [valid,{headers:{'Content-Type':'text/plain'}},415],
    ['{',{},400], [null,{},400], [[],{},400],
    [{...valid,message:' '},{},400], [{...valid,message:'a'.repeat(2001)},{},400],
    [{...valid,email:'bad'},{},400], [{...valid,id:'bad'},{},400],
    ['a'.repeat(12001),{},413], [{...valid,website:'spam'},{},200],
  ]) {
    const result = await run(body,options);
    assert.equal(result.response.status,status);
    assert.equal(result.writes.length,0);
  }
});
test('storage failure does not claim success or leak details', async () => {
  const {response} = await run(valid,{fail:true});
  assert.equal(response.status,503);
  assert.doesNotMatch(await response.text(),/private failure/);
});
