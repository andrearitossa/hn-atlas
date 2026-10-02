import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import worker,{deliver} from '../workers/newsletter-test/worker.mjs';

const NOW=1800000000, EDITION=1799990000;
const TOKEN1='abcdefghijklmnopqrstuvwxyz';
const TOKEN2='zyxwvutsrqponmlkjihgfedcba';
const LEGACY='legacyabcdefghijklmnopqrst';
function fixture() {
  const sqlite=new DatabaseSync(':memory:');
  sqlite.exec(readFileSync(new URL('../workers/newsletter-test/schema.sql',import.meta.url),'utf8'));
  const run=(sql,...args)=>sqlite.prepare(sql).run(...args);
  run('INSERT INTO newsletter_subscriptions(id,email,topic,created_at,legacy_tokens) VALUES(?,?,?,?,?)',TOKEN1,'first@example.com',1051,NOW-100,JSON.stringify([LEGACY]));
  run('INSERT INTO newsletter_subscriptions(id,email,topic,created_at) VALUES(?,?,?,?)',TOKEN2,'second@example.com',1051,NOW-100);
  run('INSERT INTO newsletter_issues(topic,edition,prepared_at,source_as_of,subject,html,posts) VALUES(?,?,?,?,?,?,?)',1051,EDITION,NOW,NOW,'Hacker Atlas · Databases','<p>One shared issue</p><a href="{{unsubscribe_url}}">Unsubscribe</a>','[]');
  const DB={
    prepare(sql){let args=[];return {bind(...values){args=values;return this},async all(){return {results:sqlite.prepare(sql).all(...args)}},async first(){return sqlite.prepare(sql).get(...args)||null},async run(){const result=sqlite.prepare(sql).run(...args);return {meta:{changes:Number(result.changes)}}},_run(){const result=sqlite.prepare(sql).run(...args);return {meta:{changes:Number(result.changes)}}}}},
    async batch(statements){sqlite.exec('BEGIN');try{const results=statements.map(statement=>statement._run());sqlite.exec('COMMIT');return results}catch(error){sqlite.exec('ROLLBACK');throw error}},
  };
  const sent=[];
  const env={DB,PUBLIC_URL:'https://newsletter.example',FROM_EMAIL:'news@hackeratlas.com',ADMIN_TOKEN:'private',EMAIL:{async send(message){sent.push(message);return {messageId:`provider-${sent.length}`}}}};
  return {sqlite,run,env,sent};
}

test('one shared HTML issue reaches every active subscriber and count is exact',async()=>{
  const f=fixture();
  assert.deepEqual(await deliver(f.env,EDITION,NOW),{sent:2,failures:0,pending:0});
  assert.equal(f.sent.length,2);
  assert.deepEqual(new Set(f.sent.map(message=>message.to)),new Set(['first@example.com','second@example.com']));
  assert.ok(f.sent.every(message=>message.html.includes('One shared issue')));
  assert.match(f.sent[0].html,new RegExp(`unsubscribe/${TOKEN1}`));
  assert.equal(f.sqlite.prepare('SELECT sent_count FROM newsletter_issues').get().sent_count,2);
  assert.equal(f.sqlite.prepare("SELECT count(*) n FROM newsletter_deliveries WHERE state='sent'").get().n,2);
});

test('repeat and concurrent runs cannot double send, and count repairs from ledger',async()=>{
  const f=fixture();
  const results=await Promise.all([deliver(f.env,EDITION,NOW),deliver(f.env,EDITION,NOW)]);
  assert.equal(results.reduce((sum,result)=>sum+result.sent,0),2);
  assert.equal(f.sent.length,2);
  f.run('UPDATE newsletter_issues SET sent_count=0');
  assert.equal((await deliver(f.env,EDITION,NOW)).sent,0);
  assert.equal(f.sqlite.prepare('SELECT sent_count FROM newsletter_issues').get().sent_count,2);
});

test('provider failure is held and pending count includes only unsent active subscribers',async()=>{
  const f=fixture();let calls=0;
  f.env.EMAIL.send=async message=>{calls++;if(message.to==='first@example.com')throw new Error('provider unavailable');f.sent.push(message);return {messageId:'ok'}};
  assert.deepEqual(await deliver(f.env,EDITION,NOW),{sent:1,failures:1,pending:1});
  assert.equal(f.sqlite.prepare('SELECT sent_count FROM newsletter_issues').get().sent_count,1);
  assert.equal((await deliver(f.env,EDITION,NOW)).pending,1);
  assert.equal(calls,2);
});

test('unsubscribe confirmation and legacy token stop this topic',async()=>{
  const f=fixture();
  const url=f.env.PUBLIC_URL+'/unsubscribe/'+LEGACY;
  assert.equal((await worker.fetch(new Request(url),f.env)).status,200);
  assert.equal(f.sqlite.prepare('SELECT unsubscribed_at FROM newsletter_subscriptions WHERE id=?').get(TOKEN1).unsubscribed_at,null);
  assert.equal((await worker.fetch(new Request(url,{method:'POST'}),f.env)).status,200);
  assert.equal((await deliver(f.env,EDITION,NOW)).sent,1);
  assert.deepEqual(f.sent.map(m=>m.to),['second@example.com']);
});

test('requested edition only; older prepared issue can be delivered after Monday',async()=>{
  const f=fixture();
  f.run('UPDATE newsletter_issues SET prepared_at=?,source_as_of=?',NOW-5*86400,NOW-5*86400);
  assert.equal((await deliver(f.env,EDITION+7*86400,NOW)).sent,0);
  assert.equal((await deliver(f.env,EDITION,NOW)).sent,2);
});

test('run requires auth and an explicit valid edition; disabled sender does no work',async()=>{
  const f=fixture();const url=f.env.PUBLIC_URL+'/run';
  assert.equal((await worker.fetch(new Request(url,{method:'POST',body:JSON.stringify({edition:EDITION})}),f.env)).status,401);
  assert.equal((await worker.fetch(new Request(url,{method:'POST',headers:{Authorization:'Bearer private'},body:'{}'}),f.env)).status,400);
  f.env.DELIVERY_DISABLED='true';
  const response=await worker.fetch(new Request(url,{method:'POST',headers:{Authorization:'Bearer private'},body:JSON.stringify({edition:EDITION})}),f.env);
  assert.equal((await response.json()).disabled,true);
  assert.equal(f.sent.length,0);
});

function articleFixture() {
  const f=fixture();
  const article='https://example.com/story?a=1&b=2';
  f.run('UPDATE newsletter_issues SET posts=?,html=?',JSON.stringify([{id:123,url:article}]),
    '<a href="https://example.com/story?a=1&amp;b=2">Title</a><a href="https://example.com/story?a=1&amp;b=2">Source</a><a href="https://news.ycombinator.com/item?id=123">Discussion</a><a href="{{unsubscribe_url}}">Unsubscribe</a>');
  return {...f,article,clickURL:`${f.env.PUBLIC_URL}/click/1051/${EDITION}/123`};
}

test('sent article links use the Worker; clicks accumulate then redirect to stored URL',async()=>{
  const f=articleFixture();
  await deliver(f.env,EDITION,NOW);
  assert.equal(f.sent[0].html.split(f.clickURL).length-1,2);
  assert.ok(f.sent[0].html.includes('https://news.ycombinator.com/item?id=123'));
  assert.ok(f.sent[0].html.includes(`/unsubscribe/${TOKEN1}`));
  for (let i=0;i<2;i++) {
    const response=await worker.fetch(new Request(f.clickURL+'?url=https://evil.example'),f.env);
    assert.equal(response.status,302);
    assert.equal(response.headers.get('Location'),f.article);
    assert.equal(response.headers.get('Cache-Control'),'no-store');
    assert.equal(response.headers.get('Referrer-Policy'),'no-referrer');
  }
  const row=f.sqlite.prepare('SELECT * FROM newsletter_clicks').get();
  assert.equal(row.clicks,2);assert.equal(row.story,123);assert.equal(row.edition,EDITION);
  assert.ok(row.last_clicked_at>0);
});

test('invalid article/edition and POST do not count; HEAD redirects without counting',async()=>{
  const f=articleFixture();
  assert.equal((await worker.fetch(new Request(f.clickURL.replace('/123','/999')),f.env)).status,404);
  assert.equal((await worker.fetch(new Request(f.clickURL.replace(String(EDITION),'1')),f.env)).status,404);
  assert.equal((await worker.fetch(new Request(f.clickURL,{method:'POST'}),f.env)).status,405);
  assert.equal((await worker.fetch(new Request(f.clickURL,{method:'HEAD'}),f.env)).status,302);
  assert.equal(f.sqlite.prepare('SELECT count(*) n FROM newsletter_clicks').get().n,0);
});

test('click recording failure does not break the article redirect',async()=>{
  const f=articleFixture();f.sqlite.exec('DROP TABLE newsletter_clicks');
  const original=console.error;const errors=[];console.error=(...args)=>errors.push(args);
  try {
    const response=await worker.fetch(new Request(f.clickURL),f.env);
    assert.equal(response.status,302);assert.equal(response.headers.get('Location'),f.article);
    assert.equal(errors.length,1);
  } finally { console.error=original; }
});

test('unsafe or absent stored article URL redirects to HN instead',async()=>{
  const f=articleFixture();
  f.run('UPDATE newsletter_issues SET posts=?',JSON.stringify([{id:123,url:'javascript:alert(1)'}]));
  const response=await worker.fetch(new Request(f.clickURL),f.env);
  assert.equal(response.headers.get('Location'),'https://news.ycombinator.com/item?id=123');
});

test('daily selection requires authentication and sends one immutable edition once',async()=>{
  const f=fixture();
  f.sqlite.exec(`CREATE TABLE daily_selections(edition TEXT PRIMARY KEY,prepared_at INTEGER,subject TEXT,html TEXT,posts TEXT,state TEXT DEFAULT 'prepared',message_id TEXT,error TEXT)`);
  f.run("INSERT INTO daily_selections(edition,subject,html) VALUES(?,?,?)",'2026-10-01','Daily selection','<p>Ten stories</p>');
  const request=(auth=true)=>new Request(f.env.PUBLIC_URL+'/daily-selection',{method:'POST',headers:auth?{Authorization:'Bearer private'}:{},body:JSON.stringify({edition:'2026-10-01'})});
  assert.equal((await worker.fetch(request(false),f.env)).status,401);
  assert.equal((await worker.fetch(request(),f.env)).status,200);
  assert.equal((await worker.fetch(request(),f.env)).status,200);
  assert.equal(f.sent.length,1);
  assert.equal(f.sent[0].to,'andre.ritossa@gmail.com');
  assert.equal(f.sent[0].html,'<p>Ten stories</p>');
});

test('daily selection holds an ambiguous provider failure instead of resending',async()=>{
  const f=fixture();
  f.sqlite.exec(`CREATE TABLE daily_selections(edition TEXT PRIMARY KEY,subject TEXT,html TEXT,state TEXT DEFAULT 'prepared',message_id TEXT,error TEXT)`);
  f.run('INSERT INTO daily_selections(edition,subject,html) VALUES(?,?,?)','2026-10-01','Daily selection','<p>Ten stories</p>');
  let calls=0;
  f.env.EMAIL.send=async()=>{calls++;throw new Error('Timeout')};
  const request=()=>new Request(f.env.PUBLIC_URL+'/daily-selection',{method:'POST',headers:{Authorization:'Bearer private'},body:JSON.stringify({edition:'2026-10-01'})});
  assert.equal((await worker.fetch(request(),f.env)).status,502);
  assert.equal((await worker.fetch(request(),f.env)).status,409);
  assert.equal(calls,1);
});

test('one-off daily selection sends without any database access',async()=>{
  const f=fixture();
  f.env.DB={prepare(){throw new Error('One-off mail must not access D1')}};
  const request=(auth=true)=>new Request(f.env.PUBLIC_URL+'/daily-selection-once',{method:'POST',headers:auth?{Authorization:'Bearer private'}:{},body:JSON.stringify({subject:'Daily selection',html:'<p>Fresh stories</p>'})});
  assert.equal((await worker.fetch(request(false),f.env)).status,401);
  assert.equal((await worker.fetch(request(),f.env)).status,200);
  assert.equal(f.sent.length,1);
  assert.equal(f.sent[0].to,'andre.ritossa@gmail.com');
});
