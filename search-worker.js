let data, vectors;
const fold=s=>String(s||'').normalize('NFKC').toLowerCase();
function patterns(query){
return fold(query).split(/\s+/).filter(Boolean).map(word=>({test:value=>fold(value).includes(word)}));
}
function textScore(post,query,tests){const title=fold(post.title),url=fold(post.url),hits=tests.filter(p=>p.test(title+' '+url));if(!hits.length)return 0;return hits.length/tests.length*4+tests.filter(p=>p.test(title)).length/tests.length*2+(title.includes(fold(query))?2:0);}
async function loadVectors(){if(vectors)return vectors;const all=new Int8Array(data.shards.reduce((n,s)=>n+s.count,0)*data.dimensions);for(let offset=0;offset<data.shards.length;offset+=4){await Promise.all(data.shards.slice(offset,offset+4).map(async shard=>{const response=await fetch('/search-data/'+shard.file);if(!response.ok)throw Error('Could not load meaning index');all.set(new Int8Array(await response.arrayBuffer()),shard.offset*data.dimensions);}));}vectors=all;return all;}
self.onmessage=async ({data:message})=>{try{
if(message.type==='init'){const response=await fetch('/search-data/index.json');if(!response.ok)throw Error('Search data is not available yet');data=await response.json();if(data.post_shards){const chunks=[];for(let offset=0;offset<data.post_shards.length;offset+=4){chunks.push(...await Promise.all(data.post_shards.slice(offset,offset+4).map(async file=>{const r=await fetch('/search-data/'+file);if(!r.ok)throw Error('Could not load recent stories');return r.json();})));}data.posts=chunks.flat();}postMessage({type:'ready',count:data.posts.length,since:data.since,topics:data.topics||[]});return;}
const {q,mode,vector,sort,id,topic}=message;let terms=[],meaning=[];const tests=patterns(q);const eligible=post=>!topic||(post.topics||[]).includes(Number(topic));
for(let i=0;i<data.posts.length;i++){if(!eligible(data.posts[i]))continue;const score=textScore(data.posts[i],q,tests);if(score>0)terms.push({i,score});}
terms.sort((a,b)=>b.score-a.score||data.posts[b.i].time-data.posts[a.i].time);
if(vector&&mode!=='text'){
const matrix=await loadVectors();const norm=Math.hypot(...vector);if(vector.length!==data.dimensions||!Number.isFinite(norm)||norm===0)throw Error('Invalid query vector');const query=vector.map(v=>v/norm);
for(let i=0;i<data.posts.length;i++){if(!eligible(data.posts[i]))continue;const row=data.posts[i].vector;if(row<0)continue;let dot=0,norm2=0;for(let j=0;j<data.dimensions;j++){const value=matrix[row*data.dimensions+j];dot+=value*query[j];norm2+=value*value;}const score=norm2?dot/Math.sqrt(norm2):0;if(score>=.2)meaning.push({i,score});}meaning.sort((a,b)=>b.score-a.score||data.posts[b.i].time-data.posts[a.i].time);
}
const ranks=new Map();const add=(list,key)=>list.slice(0,mode==='text'?list.length:500).forEach((item,rank)=>{const result=ranks.get(item.i)||{i:item.i,score:0,text:false,meaning:false};result.score+=1/(60+rank+1);result[key]=true;ranks.set(item.i,result);});
if(mode!=='meaning')add(terms,'text');if(mode!=='text')add(meaning,'meaning');
let results=[...ranks.values()];results.sort((a,b)=>sort==='newest'?data.posts[b.i].time-data.posts[a.i].time:sort==='points'?(data.posts[b.i].score||0)-(data.posts[a.i].score||0):b.score-a.score||data.posts[b.i].time-data.posts[a.i].time);
postMessage({type:'results',id,posts:results.slice(0,300).map(r=>({...data.posts[r.i],match:r.text&&r.meaning?'Words + meaning':r.text?'Text match':'Related idea'})),total:results.length});
}catch(error){postMessage({type:'error',id:message.id,detail:error.message});}};
