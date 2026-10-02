const normalized = text => String(text || '').normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase().trim();
export function textScore(topic, query) {
 const q=normalized(query), name=normalized(topic.name), description=normalized(topic.description);
 if (!q) return 0;
 if (name===q) return 1000;
 if (name.startsWith(q)) return 500;
 const start=name.indexOf(q);
 if (start>=0 && (start===0 || !/[\p{L}\p{N}]/u.test(name[start-1]))) return 250;
 const stop=new Set('a an and are as at be by for from how i in is it my of on or the to with your'.split(' '));
 const tokens=[...new Set(q.match(/[\p{L}\p{N}+#]+/gu)||[])].filter(t=>!stop.has(t));
 const words=name.match(/[\p{L}\p{N}+#]+/gu)||[];
 const details=description.match(/[\p{L}\p{N}+#]+/gu)||[];
 const matches=tokens.map(t=>words.some(w=>w.startsWith(t))?20:details.some(w=>w.startsWith(t))?2:0);
 if(tokens.length>=3 && matches.filter(Boolean).length<2)return 0;
 return matches.reduce((sum,score)=>sum+score,0);
}
function cosine(a,b) {
 let dot=0,aa=0,bb=0;
 for(let i=0;i<a.length;i++){dot+=a[i]*b[i];aa+=a[i]*a[i];bb+=b[i]*b[i];}
 return aa&&bb?dot/Math.sqrt(aa*bb):0;
}
export function rankTopics(topics, query, vector, embeddings) {
 const text=topics.map((t,i)=>({i,score:textScore(t,query)})).filter(r=>r.score>0).sort((a,b)=>b.score-a.score||a.i-b.i);
 const semantic=vector&&embeddings?topics.map((t,i)=>({i,score:cosine(vector,embeddings[i])})).sort((a,b)=>b.score-a.score||a.i-b.i):[];
 const threshold=Math.max(.25,(semantic[0]?.score||0)*.6);
 const meaning=semantic.filter(r=>r.score>=threshold).slice(0,12);
 const scores=new Map();
 for(const list of [text,meaning])for(const [rank,row] of list.entries())scores.set(row.i,(scores.get(row.i)||0)+1/(60+rank+1));
 return [...scores].sort(([a,sa],[b,sb])=>
  Number(textScore(topics[b],query)>=250)-Number(textScore(topics[a],query)>=250)
  || (textScore(topics[a],query)>=250?textScore(topics[b],query)-textScore(topics[a],query):0)
  || sb-sa || topics[a].name.localeCompare(topics[b].name)).slice(0,30).map(([i])=>topics[i]);
}
