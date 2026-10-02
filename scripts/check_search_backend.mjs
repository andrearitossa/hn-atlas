// Exercise the production handler against actual D1, Vectorize, and embeddings.
// Run explicitly; normal tests use fixtures and never call paid services.
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {onRequest} from '../functions/api/search/index.js';
for(const line of readFileSync('.env','utf8').split('\n')){
 const at=line.indexOf('=');if(at<1||line.trim().startsWith('#'))continue;
 const key=line.slice(0,at).trim(),value=line.slice(at+1).trim().replace(/^(['"])(.*)\1$/,'$2');
 process.env[key]??=value;
}
const config=JSON.parse(readFileSync('search-cloudflare.json','utf8'));
const base=`https://api.cloudflare.com/client/v4/accounts/${config.account_id}`;
async function api(path,body){
 const response=await fetch(base+path,{method:'POST',headers:{Authorization:`Bearer ${process.env.CLOUDFLARE_API_TOKEN}`,'Content-Type':'application/json'},body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok||!data.success)throw Error(`Cloudflare API failed: ${path}`);return data.result;
}
const db={prepare(sql){return {bind(...params){this.params=params;return this;},async all(){const result=await api(`/d1/database/${config.database_id}/query`,{sql,params:this.params||[]});assert.ok(result.every(r=>r.success));return result[0];}};},async batch(statements){return Promise.all(statements.map(s=>s.all()));}};
const env={SEARCH_DB:db,OPENAI_API_KEY:process.env.OPENAI_API_KEY,SEARCH_VECTORS:{query(vector,options){return api(`/vectorize/v2/indexes/${config.index_name}/query`,{vector,...options});}}};
const origin='https://atlas.test';
async function search(body){
 const response=await onRequest({request:new Request(origin+'/api/search/',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),env});
 const data=await response.json();assert.equal(response.status,200);assert.equal(data.notice,'');assert.ok(data.posts.length>0&&data.posts.length<=20);
 assert.equal(new Set(data.posts.map(p=>p.id)).size,data.posts.length);return data.posts;
}
const words=await search({q:'Rust compiler'});assert.ok(words.some(p=>p.match!=='Text match'));
console.log('Word + semantic retrieval:',words.length,'unique results');
const memberships=await db.prepare('SELECT topic FROM post_topics ORDER BY id LIMIT 1').all();const topic=memberships.results[0].topic;
const scoped=await search({q:'technology',topic});
for(const row of scoped){const membership=await db.prepare('SELECT 1 AS present FROM post_topics WHERE id=? AND topic=?').bind(row.id,topic).all();assert.equal(membership.results.length,1);}
console.log('Topic filtering:',scoped.length,'verified memberships');
for(const sort of ['newest','points']){
 const rows=await search({q:'Rust compiler',sort});const field=sort==='newest'?'time':'score';
 for(let i=1;i<rows.length;i++)assert.ok(rows[i-1][field]>=rows[i][field]);console.log(sort+': descending order verified');
}

for(const mode of ['pattern','semantic']){
 const rows=await search({q:'Rust compiler',mode});
 assert.ok(rows.every(p=>p.match===(mode==='pattern'?'Text match':'Related idea')));
 console.log(mode+': isolated retrieval verified');
}
