const stop=new Set('a an and are as at be by for from how i in is it my of on or that the this to with your'.split(' '));
const words=value=>(String(value||'').normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase()
 .replace(/artificial intelligence/g,'ai').replace(/machine learning/g,'ml').match(/[\p{L}\p{N}+#]+/gu)||[])
 .map(w=>({js:'javascript',ts:'typescript'}[w]||w)).filter(w=>!stop.has(w));
export function textScore(topic, query) {
 const tokens=[...new Set(words(query))];if(!tokens.length)return 0;
 const title=words(topic.name),description=words(topic.description),name=title.join(' '),q=tokens.join(' ');
 if(name===q)return 1000;
 if(name.startsWith(q))return 500;
 const start=name.indexOf(q);
 if(start>=0 && (start===0 || name[start-1]===' '))return 250;
 let score=0,hits=0;
 for(const token of tokens){const prefix=w=>token.length>=3&&w.startsWith(token);
  const weight=title.includes(token)?8:title.some(prefix)?5:description.includes(token)?2:description.some(prefix)?1:0;
  score+=weight;if(weight)hits++;
 }
 return hits<Math.min(2,tokens.length)?0:score+hits/tokens.length*8;
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
