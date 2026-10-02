import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

async function engine(){
 const replies=[];const posts=[{id:1,title:'Rust memory safety',url:'https://example.org',time:3,score:5,vector:0,topics:[10]},{id:2,title:'Safer systems without garbage collection',url:'https://example.org',time:2,score:10,vector:1,topics:[20]},{id:3,title:'Gardening',url:'https://garden.org',time:1,score:1,vector:-1}];
 const data={posts,dimensions:2,shards:[{file:'vectors-0.bin',offset:0,count:2}]};
 const context=vm.createContext({self:{},postMessage:r=>replies.push(r),Int8Array,fetch:async path=>({ok:true,json:async()=>data,arrayBuffer:async()=>new Int8Array([127,0,120,30]).buffer})});
 vm.runInContext(await readFile(new URL('../search-worker.js',import.meta.url),'utf8'),context);
 await context.self.onmessage({data:{type:'init'}});
 return async message=>{await context.self.onmessage({data:{type:'search',id:1,sort:'relevance',...message}});return JSON.parse(JSON.stringify(replies.at(-1)));};
}
test('Smart unifies words and meaning without duplicating stories',async()=>{const search=await engine();const result=await search({q:'Rust',mode:'smart',vector:[1,0]});assert.deepEqual(result.posts.map(p=>p.id),[1,2]);assert.equal(result.posts[0].match,'Words + meaning');assert.equal(result.posts[1].match,'Related idea');});
test('Text treats quotes and stars literally rather than as query syntax',async()=>{const search=await engine();assert.deepEqual((await search({q:'memory safety',mode:'text'})).posts.map(p=>p.id),[1]);assert.deepEqual((await search({q:'"memory safety"',mode:'text'})).posts,[]);assert.deepEqual((await search({q:'garb*',mode:'text'})).posts,[]);assert.deepEqual((await search({q:'[',mode:'text'})).posts,[]);});
test('Meaning excludes missing vectors and text matches can sort by points',async()=>{const search=await engine();assert.equal((await search({q:'Rust',mode:'meaning',vector:[1,0]})).posts.length,2);assert.deepEqual((await search({q:'example.org',mode:'text',sort:'points'})).posts.map(p=>p.id),[2,1]);});

test('Topic filter limits both word and meaning candidates',async()=>{const search=await engine();const result=await search({q:'Rust',mode:'smart',vector:[1,0],topic:'20'});assert.deepEqual(result.posts.map(p=>p.id),[2]);assert.equal(result.posts[0].match,'Related idea');});
