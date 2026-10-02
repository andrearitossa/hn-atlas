import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {onRequest as readerAPI} from '../functions/api/reader/[[path]].js';
import {handle as feedAPI} from '../workers/feed/worker.mjs';
import {hash, COOKIE, seconds} from '../lib/reader.js';
import {sendLogin} from '../workers/newsletter-test/login.mjs';

function fixture() {
  const sqlite = new DatabaseSync(':memory:');
  sqlite.exec(readFileSync(new URL('../migrations/0006_feed_profiles.sql',import.meta.url),'utf8'));
  sqlite.exec(readFileSync(new URL('../migrations/0007_feed_state.sql',import.meta.url),'utf8'));
  const wrap = sql => {
    const stmt = sqlite.prepare(sql); let args = [];
    return {bind(...values) {args=values;return this;},
      async first() {return stmt.get(...args) || null;},
      async all() {return {results:stmt.all(...args)};},
      async run() {const result=stmt.run(...args);return {meta:{changes:Number(result.changes)}};}};
  };
  const db = {prepare:wrap, withSession(){return this;}, async batch(statements) {
    sqlite.exec('BEGIN');try {const result=[];for(const stmt of statements)result.push(await stmt.run());sqlite.exec('COMMIT');return result;}
    catch(error){sqlite.exec('ROLLBACK');throw error;}
  }};
  const edition='a'.repeat(32), sent=[];
  const data={version:1,edition,as_of:seconds(),topics:[{id:3,name:'Rust',slug:'rust'}],
    posts:[{id:123,title:'Rust story',topics:[3],time:seconds(),hn_points:20,article_key:'1'.repeat(64)}]};
  const env={NEWSLETTER_DB:db,NOTIFICATIONS:{sendLogin:async message=>sent.push(message)},ASSETS:{async fetch(url){
    if(new URL(url).pathname==='/discovery-topics.json')return Response.json({topics:data.topics});
    return new Response('Missing',{status:404});
  }}};
  const request=(path='',method='GET',body,cookie,origin='https://hackeratlas.com')=>new Request('https://hackeratlas.com/api/reader/'+path,
    {method,headers:{Origin:origin,...(body?{'Content-Type':'application/json'}:{}),...(cookie?{Cookie:cookie}:{})},body:body?JSON.stringify(body):undefined});
  const call=(path,method,body,cookie,origin)=>readerAPI({env,request:request(path,method,body,cookie,origin)});
  async function login(email='reader@example.com') {
    assert.equal((await call('login','POST',{email})).status,200);
    const token=new URL(sent.at(-1).url).hash.slice(7);
    const response=await call('verify','POST',{token});assert.equal(response.status,200);
    return {token,cookie:response.headers.get('Set-Cookie').split(';')[0],response};
  }
  const sync=()=>sqlite.prepare('INSERT INTO feed_catalog(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload').run(JSON.stringify(data));
  const eventCall=(body,cookie)=>feedAPI(new Request('https://hackeratlas.com/api/feed/events',{
    method:'POST',headers:{Origin:'https://hackeratlas.com','Content-Type':'application/json',Cookie:cookie},body:JSON.stringify(body)}),env);
  const feedCall=(cookie,cursor)=>{sync();return feedAPI(new Request('https://hackeratlas.com/api/feed'+(cursor?'?cursor='+encodeURIComponent(cursor):''),{headers:cookie?{Cookie:cookie}:{}}),env);};
  return {sqlite,env,data,sent,call,login,eventCall,feedCall};
}

test('email login verifies once, uses hashed secrets, creates persistent topics and expires sessions', async () => {
  const f=fixture();const {token,cookie,response}=await f.login(' Reader@Example.com ');
  assert.match(response.headers.get('Set-Cookie'),/Secure; HttpOnly; SameSite=Lax/);
  assert.match(response.headers.get('Cache-Control'),/no-store/);
  assert.equal(f.sqlite.prepare('SELECT email FROM feed_users').get().email,'reader@example.com');
  assert.equal(f.sqlite.prepare('SELECT token_hash FROM feed_login_tokens').get().token_hash,await hash(token));
  assert.notEqual(f.sqlite.prepare('SELECT token_hash FROM feed_sessions').get().token_hash,cookie.split('=')[1]);
  assert.equal((await f.call('verify','POST',{token})).status,401);
  assert.equal((await f.call('','GET')).status,401);
  const saved=await f.call('','PUT',{topics:[3],version:0},cookie);
  assert.deepEqual((await saved.json()).topics,[3]);
  assert.equal(f.sqlite.prepare('SELECT topic_id FROM feed_user_topics').get().topic_id,3);
  assert.equal((await f.call('','PUT',{topics:[3],version:0},cookie)).status,409);
  assert.equal((await f.call('','PUT',{topics:[999],version:1},cookie)).status,400);
  assert.equal((await f.call('','PUT',{topics:[3],version:1},cookie,'https://evil.test')).status,403);
  f.sqlite.exec('UPDATE feed_sessions SET expires_at=0');
  assert.equal((await f.call('','GET',undefined,cookie)).status,401);
  f.sqlite.close();
});

test('expired links, failed email delivery, login limits, and malformed requests fail without sessions', async () => {
  const f=fixture();await f.call('login','POST',{email:'old@example.com'});
  const token=new URL(f.sent[0].url).hash.slice(7);f.sqlite.exec('UPDATE feed_login_tokens SET expires_at=0');
  assert.equal((await f.call('verify','POST',{token})).status,401);
  assert.equal((await f.call('login','POST',{email:'old@example.com'})).status,429);
  f.env.NOTIFICATIONS.sendLogin=async()=>{throw Error('down');};
  assert.equal((await f.call('login','POST',{email:'fail@example.com'})).status,503);
  assert.equal(f.sqlite.prepare("SELECT count(*) AS n FROM feed_login_tokens WHERE email='fail@example.com'").get().n,0);
  assert.equal((await f.call('login','POST',{email:'invalid'})).status,400);
  assert.equal((await f.call('login','POST',{email:'a@example.com'},undefined,'https://evil.test')).status,403);
  assert.equal(f.sqlite.prepare('SELECT count(*) AS n FROM feed_sessions').get().n,0);f.sqlite.close();
});

test('seen and opened articles stay excluded across sessions, catalog editions, and reposts', async () => {
  const f=fixture(), a=await f.login('a@example.com');
  assert.equal((await f.feedCall()).status,401);
  assert.equal((await (await f.feedCall(a.cookie)).json()).topics_required,true);
  await f.call('','PUT',{topics:[3],version:0},a.cookie);
  const initial=await f.feedCall(a.cookie);assert.match(initial.headers.get('Cache-Control'),/no-store/);
  assert.equal((await initial.json()).items.length,1);
  const event={article_key:f.data.posts[0].article_key,type:'visible'};
  for(let i=0;i<2;i++)assert.equal((await f.eventCall({events:[event]},a.cookie)).status,200);
  assert.equal(f.sqlite.prepare('SELECT count(*) AS n FROM feed_story_state').get().n,1);
  assert.deepEqual((await (await f.feedCall(a.cookie)).json()).items,[]);
  await f.eventCall({events:[{...event,type:'article_opened'}]},a.cookie);
  assert.ok(f.sqlite.prepare('SELECT opened_at FROM feed_story_state').get().opened_at);
  // A second session for the same account sees the same persisted history.
  f.sqlite.exec('DELETE FROM feed_rate_limits');
  const second=await f.login('a@example.com');
  f.data.edition='b'.repeat(32);f.data.posts[0].id=456;
  assert.deepEqual((await (await f.feedCall(second.cookie)).json()).items,[]);
  const other=await f.login('b@example.com');await f.call('','PUT',{topics:[3],version:0},other.cookie);
  assert.equal((await (await f.feedCall(other.cookie)).json()).items.length,1);
  assert.equal((await f.eventCall({events:[{...event,type:'upvoted'}]},a.cookie)).status,400);
  assert.equal((await f.eventCall({events:[{...event,article_key:'invalid'}]},a.cookie)).status,400);
  await f.call('','DELETE',{},a.cookie);
  assert.equal(f.sqlite.prepare('SELECT count(*) AS n FROM feed_story_state').get().n,0);
  f.sqlite.close();
});

test('pagination does not skip unread stories after seen writes and rejects stale cursors', async () => {
  const f=fixture(), a=await f.login();await f.call('','PUT',{topics:[3],version:0},a.cookie);
  f.data.posts=Array.from({length:45},(_,i)=>({id:1000+i,title:'Story',topics:[3],time:f.data.as_of,hn_points:100-i,article_key:i.toString(16).padStart(64,'0')}));
  const first=await (await f.feedCall(a.cookie)).json();assert.equal(first.items.length,20);
  await f.eventCall({events:first.items.map(p=>({article_key:p.article_key,type:'visible'}))},a.cookie);
  const next=await (await f.feedCall(a.cookie,first.next_cursor)).json();
  assert.deepEqual(next.items.map(p=>p.id),f.data.posts.slice(20,40).map(p=>p.id));
  const last=await (await f.feedCall(a.cookie,next.next_cursor)).json();assert.equal(last.items.length,5);assert.equal(last.caught_up,true);
  const reload=await (await f.feedCall(a.cookie)).json();assert.equal(reload.items[0].id,1020);
  f.data.edition='c'.repeat(32);assert.equal((await f.feedCall(a.cookie,first.next_cursor)).status,409);
  f.data.edition='a'.repeat(32);await f.call('','PUT',{topics:[3],version:1},a.cookie);
  assert.equal((await f.feedCall(a.cookie,first.next_cursor)).status,409);
  assert.equal((await f.feedCall(a.cookie,'garbage')).status,400);
  f.sqlite.close();
});

test('sign-in sender only sends valid links for the configured site', async () => {
  const messages=[],env={FROM_EMAIL:'news@hackeratlas.com',EMAIL:{send:async msg=>messages.push(msg)}};
  const event={email:'reader@example.com',url:'https://hackeratlas.com/for-you/#token='+'a'.repeat(64)};
  await sendLogin(env,event);assert.equal(messages[0].to,event.email);
  assert.match(messages[0].text,/15 minutes/);
  await assert.rejects(sendLogin(env,{...event,url:event.url.replace('hackeratlas.com','evil.test')}));
  await assert.rejects(sendLogin({...env,DELIVERY_DISABLED:'true'},event));
  assert.equal(messages.length,1);
});

 test('profiles accept the full topic catalog and events preserve those interests', async () => {
  const f=fixture();
  f.data.topics=Array.from({length:400},(_,i)=>({id:10000+i,name:`Topic ${i}`,slug:`topic-${i}`}));
  const topics=f.data.topics.map(t=>t.id);
  f.data.posts[0].topics=[topics[0]];
  const {cookie}=await f.login();
  const saved=await f.call('','PUT',{topics,version:0},cookie);
  assert.equal(saved.status,200);
  assert.deepEqual((await saved.json()).topics,topics);
  assert.deepEqual((await (await f.call('','GET',undefined,cookie)).json()).topics,topics);
  assert.equal((await (await f.feedCall(cookie)).json()).items.length,1);
  assert.equal((await f.call('','PUT',{topics:[],version:1},cookie)).status,400);
  assert.equal((await f.call('','PUT',{topics:[topics[0],topics[0]],version:1},cookie)).status,400);
  f.sqlite.close();
});
