import test from 'node:test';
import assert from 'node:assert/strict';
import {onRequest} from '../functions/api/search/topics.js';
import {rankTopics,textScore} from '../lib/topic-search.js';
import {topicEmbeddings} from '../lib/search-embedding.js';
const topics=[
 {id:1,name:'Rust programming',slug:'rust',description:'Memory safety and systems programming'},
 {id:2,name:'Urban housing',slug:'housing',description:'Cities and homes'},
 {id:3,name:'Database systems',slug:'databases',description:'SQL and data storage'}
];
const vector=(x,y)=>[x,y,...Array(510).fill(0)];
const env={OPENAI_API_KEY:'test',NEWSLETTER_DB:{prepare(sql){return {bind(){return this;},async first(){return sql.includes('JOIN feed_users')?{id:'u',email:'a@example.com',version:0}:{hits:1};}};}},ASSETS:{fetch:async()=>Response.json({topics})}};
const request=(q,origin='https://atlas.test',cookie=true)=>new Request('https://atlas.test/api/search/topics',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...(cookie?{Cookie:'__Host-atlas-session='+'a'.repeat(64)}:{})},body:JSON.stringify({q})});

test('exact topic names outrank semantic-only matches; related concepts still match',()=>{
 assert.equal(rankTopics(topics,'Rust',vector(0,1),[vector(0,1),vector(1,0),vector(1,0)])[0].id,1);
 assert.deepEqual(rankTopics(topics,'memory-safe languages',vector(0,1),[vector(0,1),vector(1,0),vector(1,0)]).map(t=>t.id),[1]);
 assert.equal(rankTopics(topics,'DATABASE')[0].id,3);
 assert.deepEqual(rankTopics(topics,'nonsense'),[]);
 assert.equal(textScore({name:'Trust and security',description:'Trustworthy software'},'Rust'),0);
 assert.equal(textScore({name:'History',description:'A history of the world'},'buying a home'),0);
});

test('topic endpoint combines name and meaning search and caches the public catalog',async()=>{
 const originalFetch=globalThis.fetch,originalCaches=globalThis.caches,cache=new Map(),inputs=[];
 try {
  globalThis.caches={default:{async match(key){return cache.get(key.url)?.clone();},async put(key,response){cache.set(key.url,response.clone());}}};
  globalThis.fetch=async(url,options)=>{
   const body=JSON.parse(options.body);inputs.push(body.input);
   return Response.json({data:Array.isArray(body.input)?[vector(0,1),vector(1,0),vector(1,0)].map((embedding,index)=>({index,embedding})):[{embedding:vector(0,1)}]});
  };
  const response=await onRequest({env,request:request('memory-safe languages')});
  assert.equal(response.status,200);assert.equal((await response.json()).topics[0].id,1);
  await onRequest({env,request:request('Rust')});
  assert.equal(inputs.filter(Array.isArray).length,1);
  assert.match(inputs.find(Array.isArray)[0],/Rust programming\nMemory safety/);
  assert.equal((await onRequest({env,request:request('a')})).status,400);
  assert.equal((await onRequest({env,request:request('Rust','https://evil.test')})).status,403);
  assert.equal((await onRequest({env,request:request('Rust','https://atlas.test',false)})).status,401);
 } finally {globalThis.fetch=originalFetch;if(originalCaches===undefined)delete globalThis.caches;else globalThis.caches=originalCaches;}
});

test('embedding outage preserves topic name matches',async()=>{
 const response=await onRequest({env:{...env,OPENAI_API_KEY:undefined},request:request('database')});
 assert.equal(response.status,200);
 const data=await response.json();assert.equal(data.topics[0].id,3);assert.match(data.notice,/name matches/);
});

test('catalog cache invalidates when topic text changes',async()=>{
 const originalFetch=globalThis.fetch,originalCaches=globalThis.caches,cache=new Map();let calls=0;
 try {
  globalThis.caches={default:{async match(k){return cache.get(k.url)?.clone();},async put(k,v){cache.set(k.url,v.clone());}}};
  globalThis.fetch=async()=>{calls++;return Response.json({data:[{index:0,embedding:vector(1,0)}]});};
  await topicEmbeddings(['Old name'],env);await topicEmbeddings(['Old name'],env);await topicEmbeddings(['New name'],env);
  assert.equal(calls,2);
 }finally{globalThis.fetch=originalFetch;if(originalCaches===undefined)delete globalThis.caches;else globalThis.caches=originalCaches;}
});
