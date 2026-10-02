export const RANKING_VERSION='fts5-title3-cosine512-rrf60-v1';
const insert=`INSERT INTO searches
 (id,created_at,query,mode,topic_id,sort,ranking_version,corpus_since,corpus_until,status,latency_ms,word_candidates,semantic_candidates,word_failed,semantic_failed,results_json,error_code)
 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`;
export function impressionResults(posts,words,meaning){
 const text=new Map(words.map((p,i)=>[p.id,{rank:i+1,score:p._wordScore??null}]));
 const semantic=new Map(meaning.map((p,i)=>[p.id,{rank:i+1,score:p._semanticScore??null}]));
 return posts.map((p,i)=>({...p,position:i+1,word_rank:text.get(p.id)?.rank??null,word_score:text.get(p.id)?.score??null,
  semantic_rank:semantic.get(p.id)?.rank??null,semantic_score:semantic.get(p.id)?.score??null,
  fusion_score:(text.has(p.id)?1/(60+text.get(p.id).rank):0)+(semantic.has(p.id)?1/(60+semantic.get(p.id).rank):0)}));
}
export function storeSearch(db,event){
 return Promise.resolve().then(()=>db.prepare(insert).bind(event.id,event.created_at,event.query,event.mode,event.topic,event.sort,
  RANKING_VERSION,event.since,event.until,event.status,event.latency_ms,event.words.length,event.meaning.length,
  Number(event.word_failed),Number(event.semantic_failed),JSON.stringify(impressionResults(event.posts,event.words,event.meaning)),event.error_code).run())
  .catch(()=>{console.warn('Search history write failed',{search_id:event.id});});
}
