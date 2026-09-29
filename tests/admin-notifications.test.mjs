import test from 'node:test';
import assert from 'node:assert/strict';
import {notify} from '../workers/newsletter-test/notifications.mjs';

test('feedback and signup notifications use the existing sender and fixed recipient', async () => {
  const sent=[];
  const env={FROM_EMAIL:'news@hackeratlas.com',EMAIL:{send:async message=>sent.push(message)}};
  await notify(env,{type:'feedback',message:'<b>Useful!</b>',email:'',page:'#/'});
  await notify(env,{type:'signup',email:'reader@example.com',topic:'Programming'});
  assert.ok(sent.every(m=>m.to==='andre.ritossa@gmail.com' && m.from.email===env.FROM_EMAIL));
  assert.equal(sent[0].text,'From: Anonymous\nPage: #/\n\n<b>Useful!</b>');
  assert.equal(sent[1].subject,'hey! new user for topics "Programming" :>');
  assert.match(sent[1].text,/reader@example.com/);
  await assert.rejects(notify(env,{type:'unknown'}),/Unknown notification/);
  assert.equal(sent.length,2);
});
