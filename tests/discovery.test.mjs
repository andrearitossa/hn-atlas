import test from 'node:test';
import assert from 'node:assert/strict';
import {matchingTopics} from '../search.js';
const topics=[{id:1,name:'AI assistants',description:'Language models and agents',last_7d:10},{id:2,name:'Public transit and rail',description:'Trains',last_7d:20},{id:3,name:'JavaScript',description:'Web programming',last_7d:5}];
test('Topic discovery handles aliases, exact short words and natural queries',()=>{
 assert.deepEqual(matchingTopics(topics,'AI').map(t=>t.id),[1]);
 assert.deepEqual(matchingTopics(topics,'js').map(t=>t.id),[3]);
 assert.equal(matchingTopics(topics,'small language models')[0].id,1);
 assert.equal(matchingTopics(topics,'zzzz_982374').length,0);
 assert.equal(matchingTopics(topics,'').length,3);
});
test('Common words do not flood the topic suggestions',()=>{
 const rows=[...topics,{id:4,name:'Policies and governance',description:'Impact of AI governance',last_7d:0}];
 assert.equal(matchingTopics(rows,'AI governance and policy')[0].id,4);
 assert.equal(matchingTopics(rows,'AI governance and policy').some(t=>t.id===2),false);
});
