const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stopWords=new Set('a an and are as at be by for from how in is it of on or that the this to with your'.split(' '));
const words=value=>(value.normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase().replace(/artificial intelligence/g,'ai').replace(/machine learning/g,'ml').match(/[\p{L}\p{N}+#]+/gu)||[]).map(w=>({js:'javascript',ts:'typescript'}[w]||w)).filter(w=>!stopWords.has(w));
export function matchingTopics(topics,query){
 const tokens=[...new Set(words(query))];if(!tokens.length)return [...topics];
 return topics.map(topic=>{const title=words(topic.name),description=words(topic.description||'');let score=0,hits=0;
  for(const token of tokens){const prefix=w=>token.length>=3&&w.startsWith(token);const weight=title.includes(token)?8:title.some(prefix)?5:description.includes(token)?2:description.some(prefix)?1:0;score+=weight;if(weight)hits++;}
  if(hits<Math.min(2,tokens.length))score=0;else score+=hits/tokens.length*8;if(title.join(' ')===tokens.join(' '))score+=20;
  return {topic,score};}).filter(x=>x.score>0).sort((a,b)=>b.score-a.score||(b.topic.last_7d||0)-(a.topic.last_7d||0)||a.topic.name.localeCompare(b.topic.name)).map(x=>x.topic);
}
function safeUrl(value,fallback){try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)?u.href:fallback;}catch{return fallback;}}
const shell=`<h1>Explore your next idea.</h1>
<form id="search-form" role="search" method="get"><div class="query-box"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input id="query" name="q" type="search" aria-label="Search topics and stories" placeholder="Search topics and stories" maxlength="500" autocomplete="off"><button id="clear-query" type="button" aria-label="Clear search" hidden>×</button><button id="submit" type="submit">Search</button></div>
<div id="search-options" class="search-options" hidden><fieldset id="search-modes"><legend class="sr-only">Search type</legend><label><input type="radio" name="mode" value="hybrid" checked><span>Hybrid</span></label><label><input type="radio" name="mode" value="pattern"><span>Pattern matching</span></label><label><input type="radio" name="mode" value="semantic"><span>Semantic</span></label></fieldset><label class="topic-label">Topic <select id="topic"><option value="">All topics</option></select></label><label class="sort-label">Sort <select id="sort"><option value="relevance">Relevance</option><option value="newest">Newest</option><option value="points">Most points</option></select></label></div></form>
<p id="search-prompt" class="search-prompt" hidden>Topics appear as you type. Press Enter to find stories.</p>
<section aria-label="Topic directory"><div class="section-heading"><div><h2 id="topics-heading">Browse topics</h2><p id="result-count" role="status"></p></div><div id="topic-sort" class="sort" aria-label="Sort topics"><button type="button" data-sort="active" class="on" aria-pressed="true">Most active</button><button type="button" data-sort="az" aria-pressed="false">A–Z</button></div></div><div id="directory" class="directory-grid"></div><button id="all-topics" class="quiet-button" type="button" hidden></button></section>
<section id="stories-section" aria-label="Story results" hidden><div class="section-heading results-heading"><div><h2>Stories</h2><p id="status" role="status" aria-live="polite"></p></div><span class="window-label">Last three months</span></div><p id="notice" class="notice" hidden></p><div id="results" aria-label="Search results"></div><div id="empty" class="empty" hidden><p>No matching stories.</p><small>Try different words or choose All topics.</small></div><button id="retry-search" class="quiet-button" type="button" hidden>Try again</button></section><button id="more" hidden></button>`;
export function mountDiscovery(host,overview={topics:[]}){
 host.classList.add('discovery');if(!host.querySelector('#search-form'))host.innerHTML=shell;
 const $=id=>host.querySelector('#'+id),params=new URLSearchParams(location.search);
 let topics=overview.topics||[],sort='active',expanded=false,semanticTopics=[],controller,requestId=0,submitted=false;
 const mode=()=>host.querySelector('input[name=mode]:checked').value;
 if(!$('query').value)$('query').value=params.get('q')||'';
 for(const t of [...topics].sort((a,b)=>a.name.localeCompare(b.name))){const option=document.createElement('option');option.value=t.id;option.textContent=t.name;$('topic').append(option);}
 $('topic').value=params.get('topic')||'';
 if(['pattern','semantic','hybrid'].includes(params.get('mode')))host.querySelector(`input[value="${params.get('mode')}"]`).checked=true;
 if(['relevance','newest','points'].includes(params.get('sort')))$('sort').value=params.get('sort');
 function updateUrl(searched=false){const p=new URLSearchParams();if(searched){p.set('q',$('query').value.trim());if($('topic').value)p.set('topic',$('topic').value);if(mode()!=='hybrid')p.set('mode',mode());if($('sort').value!=='relevance')p.set('sort',$('sort').value);}history.replaceState(null,'',location.pathname+(p.size?'?'+p:'')+location.hash);}
 function renderTopics(){
  const q=$('query').value.trim();host.classList.toggle('has-query',!!q);let matches=matchingTopics(topics,q);
  if(q&&semanticTopics.length){const ids=new Set(semanticTopics);matches=[...semanticTopics.map(id=>topics.find(t=>t.id===id)).filter(Boolean),...matches.filter(t=>!ids.has(t.id))];}
  else matches.sort(sort==='az'?(a,b)=>a.name.localeCompare(b.name):(a,b)=>(b.last_7d||0)-(a.last_7d||0));
  if($('topic').value)matches=matches.filter(t=>String(t.id)===$('topic').value);
  $('topics-heading').textContent=q?'Related topics':'Browse topics';$('topic-sort').hidden=!!q;
  const displayed=q&&!expanded?matches.slice(0,3):matches;
  $('result-count').textContent=`${matches.length} ${matches.length===1?'topic':'topics'}${q?' found':''}`;
  $('directory').innerHTML=displayed.map(t=>`<a class="topic-card${q?' compact':''}" href="/topic/${t.slug||t.id}/"><strong>${escape(t.name)} <span aria-hidden="true">↗</span></strong><p>${escape(t.description||'')}</p>${!q&&t.last_7d!==undefined?`<div class="card-kicker">${t.last_7d.toLocaleString()} posts · past 7 days</div>`:''}</a>`).join('')||'<p class="empty">No topics found. You can still search for stories.</p>';
  $('all-topics').hidden=!q||expanded||matches.length<=3;$('all-topics').textContent=`Show all ${matches.length} topics`;
  $('clear-query').hidden=!q;$('search-options').hidden=!q;$('search-prompt').hidden=!q||submitted;
 }
 function invalidate(){controller?.abort();requestId++;submitted=false;semanticTopics=[];expanded=false;$('stories-section').hidden=true;$('results').replaceChildren();$('submit').disabled=false;updateUrl();renderTopics();}
 function clear(){ $('query').value='';$('topic').value='';invalidate();$('query').focus();}
 function renderPosts(posts,q){
  $('results').replaceChildren();
  const tokens=[...new Set(words(q))];
  for(const [i,p] of posts.entries()){
   const article=document.createElement('article');article.className='result';const number=document.createElement('span');number.className='result-number';number.textContent=String(i+1).padStart(2,'0');
   const body=document.createElement('div'),h=document.createElement('h2'),a=document.createElement('a');const hn=`https://news.ycombinator.com/item?id=${p.id}`;
   a.href=safeUrl(p.url,hn);a.target='_blank';a.rel='noopener noreferrer';
   const pattern=tokens.length?new RegExp('('+tokens.map(s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|')+')','ig'):null;
   let last=0;if(pattern)for(const match of p.title.matchAll(pattern)){a.append(document.createTextNode(p.title.slice(last,match.index)));const mark=document.createElement('mark');mark.textContent=match[0];a.append(mark);last=match.index+match[0].length;}a.append(document.createTextNode(p.title.slice(last)));
   h.append(a);body.append(h);const meta=document.createElement('div');meta.className='result-meta';let domain='HN discussion';try{domain=new URL(p.url).hostname.replace(/^www\./,'');}catch{}
   const label=document.createElement('span');label.textContent=`${domain} · ${new Date(p.time*1000).toLocaleDateString('en-GB',{day:'numeric',month:'short'})} · ${p.score||0} points`;
   const comments=document.createElement('a');comments.href=hn;comments.target='_blank';comments.rel='noopener noreferrer';comments.textContent=`${p.descendants||0} comments ↗`;meta.append(label,comments);body.append(meta);
   const tag=document.createElement('span');tag.className='match-tag';tag.textContent=p.match;body.append(tag);article.append(number,body);$('results').append(article);
  }
 }
 async function run(){
  const q=$('query').value.trim();if(!q){clear();return;}
  const id=++requestId;controller?.abort();controller=new AbortController();submitted=true;renderTopics();updateUrl(true);
  $('stories-section').hidden=false;$('submit').disabled=true;$('results').setAttribute('aria-busy','true');$('results').replaceChildren();$('empty').hidden=true;$('notice').hidden=true;$('retry-search').hidden=true;$('status').textContent='Finding stories…';
  try{const response=await fetch('/api/search/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q,topic:$('topic').value,sort:$('sort').value,mode:mode()}),signal:controller.signal});const result=await response.json();if(id!==requestId)return;if(!response.ok)throw Error(result.detail||'Search is unavailable.');
   const posts=result.posts||[];const support=new Map();posts.forEach((p,i)=>{for(const topic of p.topics||[])support.set(topic,(support.get(topic)||0)+1/(60+i+1));});semanticTopics=[...support].sort((a,b)=>b[1]-a[1]||a[0]-b[0]).map(([id])=>id);renderTopics();renderPosts(posts,q);$('status').textContent=posts.length===20?'Top 20 matches':`${posts.length} ${posts.length===1?'story':'stories'} found`;
   $('notice').textContent=result.notice||'';$('notice').hidden=!result.notice;$('empty').hidden=!!posts.length;
  }catch(error){if(id===requestId&&error.name!=='AbortError'){$('status').textContent=error.message;$('retry-search').hidden=false;}}
  finally{if(id===requestId){$('submit').disabled=false;$('results').setAttribute('aria-busy','false');}}
 }
 $('search-form').onsubmit=e=>{e.preventDefault();run();};$('query').oninput=invalidate;$('query').onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();clear();}};$('clear-query').onclick=clear;$('retry-search').onclick=run;
 $('all-topics').onclick=()=>{expanded=true;renderTopics();};
 for(const button of host.querySelectorAll('[data-sort]'))button.onclick=()=>{sort=button.dataset.sort;for(const b of host.querySelectorAll('[data-sort]')){b.classList.toggle('on',b===button);b.setAttribute('aria-pressed',String(b===button));}renderTopics();};
 for(const input of host.querySelectorAll('input[name=mode]'))input.onchange=()=>{if($('query').value.trim())run();};
 $('topic').onchange=()=>{semanticTopics=[];renderTopics();if(params.get('q')?.trim())run();};$('sort').onchange=()=>{if($('query').value.trim())run();};
 renderTopics();if(params.get('q')?.trim())run();
 return {clear,run};
}
