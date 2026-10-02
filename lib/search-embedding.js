export async function retry(operation){
 for(let attempt=0;attempt<2;attempt++){
  try{return await operation();}catch(error){if(attempt===1||error.retryable===false)throw error;}
  await new Promise(resolve=>setTimeout(resolve,200));
 }
}
export async function embedding(q,env){
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

// Topic text is public and changes infrequently. Cache each catalog fingerprint
// and coalesce simultaneous requests; never put credentials or query text in URLs.
const pendingCatalogs = new Map();
export async function topicEmbeddings(texts, env) {
 if (!env.OPENAI_API_KEY) throw Error('Embedding secret missing');
 const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(texts)));
 const digest = Array.from(new Uint8Array(bytes), b=>b.toString(16).padStart(2,'0')).join('');
 const key = new Request('https://search-cache.hackeratlas.invalid/topic-embeddings-v1/'+digest);
 const cache = globalThis.caches?.default;
 const cached = await cache?.match(key).catch(()=>null);
 if (cached) return cached.json();
 if (pendingCatalogs.has(digest)) return pendingCatalogs.get(digest);
 const operation = (async () => {
  const response = await retry(async () => {
   const result = await fetch('https://api.openai.com/v1/embeddings', {
    method:'POST', headers:{Authorization:`Bearer ${env.OPENAI_API_KEY}`,'Content-Type':'application/json'},
    body:JSON.stringify({model:'text-embedding-3-small',dimensions:512,input:texts}), signal:AbortSignal.timeout(15000)
   });
   if (!result.ok) { const error = Error('Topic embedding unavailable'); error.retryable=result.status===429||result.status>=500; throw error; }
   return result;
  });
  const rows = (await response.json()).data;
  if (!Array.isArray(rows) || rows.length !== texts.length) throw Error('Invalid topic embeddings');
  const vectors = Array(texts.length);
  for (const row of rows) {
   if (!Number.isInteger(row.index) || row.index<0 || row.index>=texts.length || vectors[row.index]
    || !Array.isArray(row.embedding) || row.embedding.length!==512 || !row.embedding.every(Number.isFinite)) throw Error('Invalid topic embeddings');
   vectors[row.index]=row.embedding;
  }
  await cache?.put(key,Response.json(vectors,{headers:{'Cache-Control':'public, max-age=604800'}})).catch(()=>{});
  return vectors;
 })();
 pendingCatalogs.set(digest, operation);
 try { return await operation; } finally { pendingCatalogs.delete(digest); }
}
