import test from 'node:test';
import assert from 'node:assert/strict';
import {onRequest,merge,wordQuery,cutoff} from '../functions/api/search/index.js';
const post=id=>({id,title:`Story ${id}`,url:'https://example.com',time:Math.floor(Date.now()/1000)-id,score:id,descendants:0});
const request=body=>new Request('https://atlas.test/api/search/',{method:'POST',headers:{Origin:'https://atlas.test','Content-Type':'application/json'},body:JSON.stringify(body)});
function db(words=[],live=[]){return {prepare(sql){return {bind(...args){this.args=args;return this;},async all(){if(sql.includes('posts_fts MATCH'))return {results:words};return {results:live.filter(p=>this.args.includes(p.id))};}};}};}
test('RRF promotes overlap, removes duplicates, returns 20 and sorts deterministically',()=>{
 const rows=Array.from({length:30},(_,i)=>post(i+1));
 const result=merge(rows,[post(2),post(40),post(40)]);
 assert.equal(result.length,20);assert.equal(result[0].id,2);assert.equal(result[0].match,'Words + meaning');assert.equal(new Set(result.map(p=>p.id)).size,20);
 assert.equal(merge([post(1),post(2)],[],'newest')[0].id,1);
 assert.equal(merge([post(1),post(2)],[],'points')[0].id,2);
});
test('Plain words are quoted safely without accepting FTS operators, wildcards, or phrases',()=>{
 assert.equal(wordQuery('"Rust compiler" * OR C++ C#'), '"Rust" OR "compiler" OR "OR" OR "C++" OR "C#"');
 assert.equal(wordQuery('***'), '');
 assert.equal(cutoff(new Date('2026-05-31T12:00:00Z')),Date.parse('2026-02-28T12:00:00Z')/1000);
});
test('Search merges word and semantic retrieval and drops vector IDs absent from current D1',async()=>{
 const old=globalThis.fetch;try{
  globalThis.fetch=async()=>Response.json({data:[{embedding:Array(512).fill(.1)}]});
  const env={OPENAI_API_KEY:'secret',SEARCH_DB:db([post(1)],[post(1),post(2)]),SEARCH_VECTORS:{async query(vector,options){assert.equal(options.namespace,undefined);assert.equal(options.topK,100);assert.ok(['topic1','topic2','topic3'].includes(Object.keys(options.filter)[0]));assert.equal(Object.values(options.filter)[0],7);return {matches:[{id:'1',score:.9},{id:'99',score:.8},{id:'2',score:.7}]};}}};
  const response=await onRequest({request:request({q:'Rust',topic:7}),env});const body=await response.json();assert.equal(response.status,200);assert.deepEqual(body.posts.map(p=>p.id),[1,2]);assert.equal(body.posts[0].match,'Words + meaning');
 }finally{globalThis.fetch=old;}
});
test('Embedding outage preserves word results and explains fallback',async()=>{
 const response=await onRequest({request:request({q:'Rust'}),env:{SEARCH_DB:db([post(1)]),SEARCH_VECTORS:{}}});const body=await response.json();assert.equal(response.status,200);assert.equal(body.posts[0].match,'Text match');assert.match(body.notice,/Showing text matches/);
});
test('Invalid requests and cross-origin requests are rejected before retrieval',async()=>{
 for(const payload of [null,[],{q:''},{q:'a'.repeat(501)},{q:'Rust',topic:'bad'},{q:'Rust',topic:true},{q:'Rust',sort:'other'}])assert.equal((await onRequest({request:request(payload),env:{SEARCH_DB:db()}})).status,400);
 const foreign=new Request('https://atlas.test/api/search/',{method:'POST',headers:{Origin:'https://evil.test'}});
 assert.equal((await onRequest({request:foreign,env:{SEARCH_DB:db()}})).status,403);
});

test('Search startup reads the synced count instead of scanning the post corpus',async()=>{
 const env={SEARCH_DB:{prepare(sql){assert.ok(!sql.includes('COUNT(*)'));return sql;},async batch(){return [{results:[{id:7,name:'Rust'}]},{results:[{count:92487}]}];}}};
 const response=await onRequest({request:new Request('https://atlas.test/api/search/'),env});
 assert.equal(response.status,200);assert.equal((await response.json()).count,92487);
});

test('Malformed bodies return a client error instead of a backend outage',async()=>{
 const req=new Request('https://atlas.test/api/search/',{method:'POST',headers:{Origin:'https://atlas.test','Content-Type':'application/json'},body:'{bad'});
 assert.equal((await onRequest({request:req,env:{SEARCH_DB:db()}})).status,400);
});
test('Pattern matching skips embeddings and vectors entirely',async()=>{
 const old=globalThis.fetch;try{
  globalThis.fetch=async()=>{throw Error('Must not embed pattern queries');};
  const env={SEARCH_DB:db([post(1)]),SEARCH_VECTORS:{query(){throw Error('Must not query vectors');}}};
  const response=await onRequest({request:request({q:'Rust',mode:'pattern'}),env});const body=await response.json();
  assert.equal(response.status,200);assert.equal(body.notice,'');assert.equal(body.posts[0].match,'Text match');
 }finally{globalThis.fetch=old;}
});
test('Semantic mode skips word retrieval and does not silently substitute it on outage',async()=>{
 const old=globalThis.fetch;try{
  globalThis.fetch=async()=>Response.json({data:[{embedding:Array(512).fill(.1)}]});
  const base=db([],[post(1)]);const original=base.prepare;
  base.prepare=sql=>{assert.ok(!sql.includes('posts_fts'));return original(sql);};
  const env={SEARCH_DB:base,OPENAI_API_KEY:'secret',SEARCH_VECTORS:{async query(){return {matches:[{id:'1',score:.9}]};}}};
  const response=await onRequest({request:request({q:'Rust',mode:'semantic'}),env});assert.equal((await response.json()).posts[0].match,'Related idea');
  delete env.OPENAI_API_KEY;assert.equal((await onRequest({request:request({q:'Rust',mode:'semantic'}),env})).status,503);
 }finally{globalThis.fetch=old;}
});

test('Topic membership searches merge by score before RRF and use all three fields',async()=>{
 const old=globalThis.fetch;const calls=[];
 try{
  globalThis.fetch=async()=>Response.json({data:[{embedding:Array(512).fill(.1)}]});
  const env={OPENAI_API_KEY:'secret',SEARCH_DB:db([],[post(1),post(2),post(3)]),SEARCH_VECTORS:{async query(v,o){
   const field=Object.keys(o.filter)[0];calls.push(field);
   return {matches:field==='topic1'?[{id:'1',score:.6},{id:'2',score:.5}]:field==='topic2'?[{id:'2',score:.8}]:[{id:'3',score:.7},{id:'1',score:.6}]};
  }}};
  const response=await onRequest({request:request({q:'technology',topic:7,mode:'semantic'}),env});
  assert.deepEqual((await response.json()).posts.map(p=>p.id),[2,3,1]);assert.deepEqual(calls.sort(),['topic1','topic2','topic3']);
 }finally{globalThis.fetch=old;}
});

test('Transient embedding failure is retried before showing a fallback',async()=>{
 const old=globalThis.fetch;let calls=0;
 try{
  globalThis.fetch=async()=>++calls===1?new Response('',{status:429}):Response.json({data:[{embedding:Array(512).fill(.1)}]});
  const env={SEARCH_DB:db([post(1)],[post(2)]),OPENAI_API_KEY:'secret',SEARCH_VECTORS:{async query(){return {matches:[{id:'2',score:.8}]};}}};
  const response=await onRequest({request:request({q:'Rust'}),env});const body=await response.json();
  assert.equal(body.notice,'');assert.equal(calls,2);assert.ok(body.posts.some(p=>p.match==='Related idea'));
 }finally{globalThis.fetch=old;}
});
test('Transient vector failure is retried and permanent embedding errors are not retried',async()=>{
 const old=globalThis.fetch;let embeddings=0,vectors=0;
 try{
  globalThis.fetch=async()=>{embeddings++;return Response.json({data:[{embedding:Array(512).fill(.1)}]});};
  const env={SEARCH_DB:db([post(1)],[post(2)]),OPENAI_API_KEY:'secret',SEARCH_VECTORS:{async query(){if(++vectors===1)throw Error('Temporary vector failure');return {matches:[{id:'2',score:.8}]};}}};
  assert.equal((await (await onRequest({request:request({q:'Rust'}),env})).json()).notice,'');assert.equal(vectors,2);
  embeddings=0;globalThis.fetch=async()=>{embeddings++;return new Response('',{status:401});};
  await onRequest({request:request({q:'Rust'}),env});assert.equal(embeddings,1);
 }finally{globalThis.fetch=old;}
});

test('Repeated queries reuse cached embeddings without caching credentials or query text in URLs',async()=>{
 const oldFetch=globalThis.fetch,oldCache=globalThis.caches;let calls=0;const stored=new Map();
 try{
  globalThis.caches={default:{async match(key){const r=stored.get(key.url);return r?.clone();},async put(key,value){assert.ok(!key.url.includes('private'));stored.set(key.url,value.clone());}}};
  globalThis.fetch=async()=>{calls++;return Response.json({data:[{embedding:Array(512).fill(.1)}]});};
  const env={SEARCH_DB:db([],[post(1)]),OPENAI_API_KEY:'secret',SEARCH_VECTORS:{async query(){return {matches:[{id:'1',score:.8}]};}}};
  for(let i=0;i<2;i++)assert.equal((await onRequest({request:request({q:'private query',mode:'semantic'}),env})).status,200);
  assert.equal(calls,1);assert.equal(stored.size,1);
 }finally{globalThis.fetch=oldFetch;if(oldCache===undefined)delete globalThis.caches;else globalThis.caches=oldCache;}
});

test('Search history records impressions in the background without delaying the response',async()=>{
 const writes=[],background=[];let release;
 const gate=new Promise(resolve=>{release=resolve;});const base=db([post(1),post(2)]);
 const env={SEARCH_DB:{prepare(sql){if(!sql.startsWith('INSERT INTO searches'))return base.prepare(sql);return {bind(...params){this.params=params;return this;},async run(){writes.push(this.params);await gate;return {success:true};}};}}};
 try{
  const response=await onRequest({request:request({q:"Rust's compiler",mode:'pattern',topic:7,sort:'points'}),env,waitUntil:p=>background.push(p)});
  const body=await response.json();assert.equal(response.status,200);assert.match(body.search_id,/^[0-9a-f-]{36}$/);assert.equal(background.length,1);
  assert.equal(writes[0][0],body.search_id);assert.equal(writes[0][2],"Rust's compiler");assert.equal(writes[0][3],'pattern');assert.equal(writes[0][4],7);assert.equal(writes[0][5],'points');
  const results=JSON.parse(writes[0][15]);assert.deepEqual(results.map(p=>p.id),[2,1]);assert.deepEqual(results.map(p=>p.position),[1,2]);assert.equal(results[0].word_rank,2);assert.equal(results[0].semantic_rank,null);assert.equal(results[0].title,'Story 2');
  assert.ok(!('_wordScore' in body.posts[0]));assert.ok(writes[0][10]>=0);
 }finally{release();await Promise.all(background);}
});
test('Empty searches, degraded results and failed retrieval are recorded; malformed queries are not',async()=>{
 const writes=[];const base=db();const env={SEARCH_DB:{prepare(sql){if(!sql.startsWith('INSERT INTO searches'))return base.prepare(sql);return {bind(...p){this.params=p;return this;},async run(){writes.push(this.params);return {success:true};}};}}};
 let response=await onRequest({request:request({q:'unknown',mode:'pattern'}),env});assert.equal(response.status,200);assert.equal(writes[0][9],200);assert.deepEqual(JSON.parse(writes[0][15]),[]);
 response=await onRequest({request:request({q:'unknown',mode:'hybrid'}),env});assert.equal(response.status,200);assert.equal(writes[1][14],1);
 response=await onRequest({request:request({q:'unknown',mode:'semantic'}),env});assert.equal(response.status,503);assert.equal(writes[2][9],503);assert.equal(writes[2][16],'retrieval_unavailable');
 await onRequest({request:request({q:''}),env});assert.equal(writes.length,3);
});
test('History storage failure never turns a successful search into an outage',async()=>{
 const base=db([post(1)]);const env={SEARCH_DB:{prepare(sql){if(!sql.startsWith('INSERT INTO searches'))return base.prepare(sql);return {bind(){return this;},async run(){throw Error('D1 write unavailable');}};}}};
 const response=await onRequest({request:request({q:'Rust',mode:'pattern'}),env});const body=await response.json();assert.equal(response.status,200);assert.equal(body.posts.length,1);assert.ok(body.search_id);
});
