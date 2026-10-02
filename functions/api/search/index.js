const reply=(status,body)=>Response.json(body,{status,headers:{'Cache-Control':'no-store'}});
const fields='p.id,p.title,p.url,p.time,p.score,p.descendants';
export function cutoff(now=new Date()) {
 const d=new Date(now),day=d.getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-3);
 const last=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate();
 d.setUTCDate(Math.min(day,last));return Math.floor(d.getTime()/1000);
}
export function wordQuery(q){
 const words=[...new Set(q.normalize('NFKC').match(/[\p{L}\p{N}\p{M}]+[\p{L}\p{N}\p{M}+#]*/gu)||[])].slice(0,16);
 return words.map(w=>'"'+w+'"').join(' OR ');
}
export function merge(words,meaning,sort='relevance'){
 const ranked=new Map();
 for(const [kind,rows] of [['text',words],['meaning',meaning]]){
  const seen=new Set();let rank=0;
  for(const row of rows){const id=Number(row.id);if(seen.has(id))continue;seen.add(id);
   const entry=ranked.get(id)||{...row,rankScore:0,text:false,meaning:false};
   entry.rankScore+=1/(60+ ++rank);entry[kind]=true;ranked.set(id,entry);
  }
 }
 return [...ranked.values()].sort((a,b)=>
  (sort==='newest'?b.time-a.time:sort==='points'?b.score-a.score:0)||b.rankScore-a.rankScore||b.time-a.time||b.id-a.id
 ).slice(0,20).map(({rankScore,text,meaning,...post})=>({...post,match:text&&meaning?'Words + meaning':text?'Text match':'Related idea'}));
}
export async function retry(operation){
 for(let attempt=0;attempt<2;attempt++){
  try{return await operation();}catch(error){if(attempt===1||error.retryable===false)throw error;}
  await new Promise(resolve=>setTimeout(resolve,200));
 }
}
async function embedding(q,env){
 if(!env.OPENAI_API_KEY)throw Error('Embedding secret missing');
 const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(q));
 const key=new Request('https://search-cache.hackeratlas.invalid/embedding-v1/'+Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join(''));
 const cache=globalThis.caches?.default;
 const cached=await cache?.match(key).catch(()=>null);
 if(cached)return cached.json();
 const response=await retry(async()=>{
 const result=await fetch('https://api.openai.com/v1/embeddings',{
  method:'POST',headers:{Authorization:`Bearer ${env.OPENAI_API_KEY}`,'Content-Type':'application/json'},
  body:JSON.stringify({model:'text-embedding-3-small',dimensions:512,input:q}),signal:AbortSignal.timeout(15000)
 });
 if(!result.ok){const error=Error(`Embedding HTTP ${result.status}`);error.retryable=result.status===429||result.status>=500;throw error;}
 return result;
 });
 const vector=(await response.json()).data?.[0]?.embedding;
 if(!Array.isArray(vector)||vector.length!==512||!vector.every(Number.isFinite))throw Error('Invalid embedding');
 await cache?.put(key,Response.json(vector,{headers:{'Cache-Control':'public, max-age=86400'}})).catch(()=>{});
 return vector;
}
async function words(db,q,topic,since,until){
 const match=wordQuery(q);if(!match)return [];
 const topicSql=topic===null?'':' AND EXISTS(SELECT 1 FROM post_topics t WHERE t.id=p.id AND t.topic=?)';
 const args=[match,since,until,...(topic===null?[]:[topic])];
 return (await db.prepare(`SELECT ${fields} FROM posts_fts JOIN posts p ON p.id=posts_fts.rowid
 WHERE posts_fts MATCH ? AND p.time>=? AND p.time<=?${topicSql}
 ORDER BY bm25(posts_fts,3.0,1.0),p.time DESC LIMIT 100`).bind(...args).all()).results;
}
async function meaning(db,env,q,topic,since,until){
 if(!env.SEARCH_VECTORS)throw Error('Vector index unavailable');
 const vector=await embedding(q,env);
 const options={topK:100,returnValues:false,returnMetadata:'none'};
 const query=options=>retry(()=>env.SEARCH_VECTORS.query(vector,options));
 const responses=topic===null?[await query(options)]:await Promise.all(
  ['topic1','topic2','topic3'].map(field=>query({...options,filter:{[field]:topic}}))
 );
 // Membership queries are one semantic ranking, not three votes in fusion.
 const scores=new Map();
 for(const response of responses)for(const match of response.matches||[]){
  const id=Number(match.id);if(!Number.isSafeInteger(id)||id<=0||!Number.isFinite(match.score)||match.score<0.2)continue;
  scores.set(id,Math.max(scores.get(id)||-Infinity,match.score));
 }
 const ids=[...scores].sort((a,b)=>b[1]-a[1]||b[0]-a[0]).slice(0,100).map(([id])=>id);
 const live=new Map();
 // D1 allows at most 100 bound parameters per statement. Recheck live date/topic membership.
 for(let start=0;start<ids.length;start+=80){
  const batch=ids.slice(start,start+80);const topicSql=topic===null?'':' AND EXISTS(SELECT 1 FROM post_topics t WHERE t.id=p.id AND t.topic=?)';
  const rows=await db.prepare(`SELECT ${fields} FROM posts p WHERE p.id IN (${batch.map(()=>'?').join(',')}) AND p.time>=? AND p.time<=?${topicSql}`)
   .bind(...batch,since,until,...(topic===null?[]:[topic])).all();
  for(const row of rows.results)live.set(row.id,row);
 }
 return ids.filter(id=>live.has(id)).map(id=>live.get(id));
}
export async function onRequest({request,env}){
 if(!env.SEARCH_DB)return reply(503,{detail:'Search is unavailable. Try again shortly.'});
 const db=env.SEARCH_DB.withSession?env.SEARCH_DB.withSession('first-primary'):env.SEARCH_DB;
 const since=cutoff(),until=Math.floor(Date.now()/1000);
 try{
  if(request.method==='GET'){
   const [topics,stats]=await db.batch([
    db.prepare('SELECT id,name FROM topics ORDER BY name'),
    db.prepare("SELECT value AS count FROM search_state WHERE key='count'")
   ]);
   if(!stats.results.length)return reply(503,{detail:'Search is still being initialized.'});
   return reply(200,{topics:topics.results,count:stats.results[0].count,since});
  }
  if(request.method!=='POST')return new Response(null,{status:405,headers:{Allow:'GET, POST'}});
  if(request.headers.get('Origin')!==new URL(request.url).origin)return reply(403,{detail:'Use Search from the website.'});
  if(request.headers.get('Content-Type')?.split(';')[0]!=='application/json')return reply(415,{detail:'Expected JSON.'});
  const reader=request.body?.getReader();if(!reader)return reply(400,{detail:'Enter a query.'});
  let size=0;const chunks=[];while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>3000){await reader.cancel();return reply(413,{detail:'Query is too long.'});}chunks.push(value);}
  let body;try{body=JSON.parse(await new Blob(chunks).text());}catch{return reply(400,{detail:'Expected JSON.'});}
  if(!body||typeof body!=='object'||Array.isArray(body))return reply(400,{detail:'Expected a query object.'});
  if(body.topic!==undefined&&body.topic!==null&&typeof body.topic!=='string'&&typeof body.topic!=='number')return reply(400,{detail:'Choose a valid topic.'});
  const q=typeof body.q==='string'?body.q.trim():'';
  const topic=body.topic===undefined||body.topic===''||body.topic===null?null:Number(body.topic);
  const sort=body.sort===undefined?'relevance':body.sort;
  const mode=body.mode===undefined?'hybrid':body.mode;
  if(!q||q.length>500||topic!==null&&(!Number.isSafeInteger(topic)||topic<0)||!['relevance','newest','points'].includes(sort)||!['pattern','semantic','hybrid'].includes(mode))return reply(400,{detail:'Enter a valid query, topic, and sort.'});
  const [text,semantic]=await Promise.allSettled([mode==='semantic'?Promise.resolve([]):words(db,q,topic,since,until),mode==='pattern'?Promise.resolve([]):meaning(db,env,q,topic,since,until)]);
  for(const [stage,result] of [['words',text],['meaning',semantic]])if(result.status==='rejected')console.warn('Search retrieval failed',{stage,reason:result.reason?.message?.slice(0,160)});
  if(mode==='pattern'&&text.status==='rejected'||mode==='semantic'&&semantic.status==='rejected'||text.status==='rejected'&&semantic.status==='rejected')return reply(503,{detail:'Search is unavailable. Try again shortly.'});
  const posts=merge(text.status==='fulfilled'?text.value:[],semantic.status==='fulfilled'?semantic.value:[],sort);
  if(posts.length){
   const memberships=await db.prepare(`SELECT id,topic FROM post_topics WHERE id IN (${posts.map(()=>'?').join(',')})`).bind(...posts.map(p=>p.id)).all();
   for(const post of posts)post.topics=memberships.results.filter(t=>t.id===post.id).map(t=>t.topic);
  }
  return reply(200,{posts,total:posts.length,notice:semantic.status==='rejected'?'Meaning search is unavailable right now. Showing text matches.':text.status==='rejected'?'Word search is unavailable right now. Showing related ideas.':''});
 }catch{return reply(503,{detail:'Search is unavailable. Try again shortly.'});}
}
