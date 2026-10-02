(() => {
const host=document.querySelector('#topic-graph');
if(!host)return;
const $=selector=>host.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hn=id=>`https://news.ycombinator.com/item?id=${id}`;
const safeUrl=value=>{try {const u=new URL(value);return ['http:','https:'].includes(u.protocol)?u.href:'';}catch{return '';}};
const reduceMotion=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
let T,mapResizeObserver;
const json=async url=>{const r=await fetch(url);if(!r.ok)throw Error('Could not load topic graph');return r.json();};
const root=host.dataset.root||'';
const topicDetails=id=>json(root?`${root}topics/${id}.json`:`/api/topics/${id}`);
function drawMap() {
  const svg=d3.select("#topic-graph-map"), layer=svg.append("g");
  const rect=svg.node().getBoundingClientRect();
  let mapWidth=rect.width || 1000, mapHeight=rect.height || 620;
  svg.attr('viewBox',`0 0 ${mapWidth} ${mapHeight}`);
  const x=d3.scaleLinear([0,1],[36,mapWidth-36]), y=d3.scaleLinear([0,1],[64,mapHeight-80]);
  const radius=d3.scaleSqrt([0,d3.max(T.topics,t=>t.size)],[2.5,Math.min(12,Math.max(6,mapWidth/80))]);
  // The same stable topic color is used in the trends treemap.
  const color=t=>`hsl(${(Number(t.id)*137.508)%360} 42% 35%)`;
  const byId=new Map(T.topics.map(t=>[t.id,t]));
  const edges=T.edges.filter(e=>byId.has(e.a)&&byId.has(e.b));
  const neighbors=new Map(T.topics.map(t=>[t.id,new Set([t.id])]));
  edges.forEach(e=>{neighbors.get(e.a).add(e.b);neighbors.get(e.b).add(e.a);});
  let selected=null, matching=new Set(byId.keys()), hovered=null, transform=d3.zoomIdentity;
  const edgePath=e=>{const a=byId.get(e.a),b=byId.get(e.b);return `M${x(a.x)},${y(a.y)} Q${(x(a.x)+x(b.x))/2+12},${(y(a.y)+y(b.y))/2-12} ${x(b.x)},${y(b.y)}`;};
  const paths=layer.append("g").selectAll("path").data(edges).join("path").attr("class","map-edge")
    .attr("d",edgePath);
  const dots=layer.append("g").selectAll("circle").data(T.topics).join("circle").attr("class","topic-dot")
    .attr("cx",t=>x(t.x)).attr("cy",t=>y(t.y)).attr("r",t=>radius(t.size)).attr("fill",color)
    .attr("tabindex",0).attr("role","button").attr("aria-label",t=>`Explore ${t.name}`).attr("aria-pressed","false");
  // Keep labels outside the zoomed layer so names remain a readable size.
  const labels=svg.append("g").attr("aria-hidden","true").selectAll("text").data(T.topics).join("text")
    .attr("class","map-label").attr("x",t=>x(t.x)).attr("y",t=>y(t.y)-radius(t.size)-9)
    .attr("text-anchor","middle").text(t=>t.name);
  let labelWidths=new Map(), labelHeight=12, labelFrame=0;
  const layoutLabels=()=>{
    labelFrame=0;
    if (!svg.node().isConnected) return;
    const active=selected ?? hovered, connected=active==null ? null : neighbors.get(active);
    const visible=t=>connected ? connected.has(t.id) : matching.has(t.id);
    const boxes=[], positions=new Map(), padding=labelHeight*.35;
    const nodeBoxes=T.topics.filter(visible).map(t=>{
      const [cx,cy]=transform.apply([x(t.x),y(t.y)]), r=radius(t.size)*transform.k;
      return [cx-r,cy-r,cx+r,cy+r];
    });
    const candidates=[...T.topics].filter(visible).sort((a,b)=>(b.id===active)-(a.id===active)||b.size-a.size||a.id-b.id);
    for(const t of candidates) {
      const [cx,cy]=transform.apply([x(t.x),y(t.y)]);
      if(cx<0 || cx>mapWidth || cy<0 || cy>mapHeight) continue;
      const w=labelWidths.get(t.id) || t.name.length*labelHeight*.5;
      const r=(radius(t.size)+(t.id===active ? 3 : 0))*transform.k;
      // Place each name near its node, using actual visible space at this zoom.
      const options=[
        [cx,cy-r-padding], [cx,cy+r+labelHeight],
        [cx+r+padding+w/2,cy+labelHeight*.3], [cx-r-padding-w/2,cy+labelHeight*.3]
      ];
      for(const [lx,ly] of options) {
        const box=[lx-w/2-padding,ly-labelHeight,lx+w/2+padding,ly+padding];
        if(box[0]<8 || box[2]>mapWidth-8 || box[1]<60 || box[3]>mapHeight-66) continue;
        if(boxes.some(b=>box[0]<b[2]&&box[2]>b[0]&&box[1]<b[3]&&box[3]>b[1]))continue;
        if(nodeBoxes.some(b=>box[0]<b[2]&&box[2]>b[0]&&box[1]<b[3]&&box[3]>b[1]))continue;
        boxes.push(box);positions.set(t.id,{x:lx,y:ly});break;
      }
    }
    labels.attr("opacity",t=>positions.has(t.id)?1:0)
      .attr("x",t=>positions.get(t.id)?.x ?? transform.applyX(x(t.x)))
      .attr("y",t=>positions.get(t.id)?.y ?? transform.applyY(y(t.y)))
      .style("font-weight",t=>t.id===active ? 700 : 500);
  };
  const queueLabels=()=>{if(!labelFrame)labelFrame=requestAnimationFrame(layoutLabels);};
  const measureLabels=()=>{
    const rect=svg.node().getBoundingClientRect();
    if(!rect.width || !rect.height)return;
    if(rect.width!==mapWidth || rect.height!==mapHeight) {
      const next=d3.zoomIdentity.translate(transform.x*rect.width/mapWidth,transform.y*rect.height/mapHeight).scale(transform.k);
      mapWidth=rect.width;mapHeight=rect.height;
      svg.attr('viewBox',`0 0 ${mapWidth} ${mapHeight}`);
      x.range([36,mapWidth-36]);y.range([64,mapHeight-80]);
      radius.range([2.5,Math.min(12,Math.max(6,mapWidth/80))]);
      paths.attr('d',edgePath);
      dots.attr('cx',t=>x(t.x)).attr('cy',t=>y(t.y));
      svg.call(zoom.transform,next);
    }
    labels.style('font-size',`${labelHeight}px`).style('stroke-width','3px');
    labelWidths=new Map();
    labels.each(function(t){labelWidths.set(t.id,this.getComputedTextLength());});
    queueLabels();
  };
  const refresh=()=>{
    const active=selected ?? hovered, connected=active==null ? null : neighbors.get(active);
    const visible=t=>connected ? connected.has(t.id) : matching.has(t.id);
    dots.attr("opacity",t=>visible(t) ? 1 : .13).attr("r",t=>radius(t.size)+(t.id===active ? 3 : 0))
      .attr("tabindex",t=>visible(t)?0:-1).attr("aria-pressed",t=>t.id===selected).attr("fill",color)
      .attr("stroke",t=>t.id===active ? "var(--ink)" : "var(--bg)");
    paths.attr("stroke",e=>active!=null && (e.a===active||e.b===active) ? color(byId.get(active)) : color(byId.get(e.a)))
      .attr("stroke-opacity",e=>active==null ? .18 : (e.a===active||e.b===active) ? .7 : .025)
      .attr("stroke-width",e=>active!=null && (e.a===active||e.b===active) ? 1.5 : .7);
    queueLabels();
  };
  const preview=$("#map-preview");
  const clear=()=>{selected=null;hovered=null;preview.hidden=true;refresh();};
  const select=id=>{
    const t=byId.get(id);
    if(!t)return;
    selected=id;hovered=null;
    const related=[...neighbors.get(id)].filter(other=>other!==id).map(other=>byId.get(other)).sort((a,b)=>b.last_7d-a.last_7d);
    preview.hidden=false;
    preview.innerHTML=`<button class="preview-close" aria-label="Close topic preview">×</button><p class="eyebrow">${related.length} connected topics</p><h2>${esc(t.name)}</h2><p>${esc(t.description)}</p><div class="preview-stats">${t.last_7d.toLocaleString()} posts · past 7 days</div><a class="preview-open" href="/topic/${t.slug}/">Read this topic <span>↗</span></a><div class="preview-stories" id="map-stories"></div><h3>Explore related topics</h3><div class="preview-related">${related.map(r=>`<button data-neighbor="${r.id}">${esc(r.name)} →</button>`).join("") || '<p>No connections yet.</p>'}</div>`;
    refresh();
    topicDetails(id).then(data=>{
      if(selected!==id || !preview.isConnected)return;
      preview.querySelector("#map-stories").innerHTML='<h3>Recent stories</h3>'+data.trending.slice(0,3).map(p=>`<a href="${esc(safeUrl(p.url)||hn(p.id))}" target="_blank" rel="noopener">${esc(p.title)} ↗</a>`).join("")+(data.trending.length ? "" : '<p>No featured stories yet.</p>');
    }).catch(()=>{if(selected===id && preview.isConnected)preview.querySelector("#map-stories").innerHTML='<p>Stories could not load. Open the topic to try again.</p>';});
    if(innerWidth<=600)preview.querySelector('.preview-close').focus({preventScroll:true});
  };
  preview.onclick=e=>{
    if(e.target.closest(".preview-close")){const previous=selected;clear();dots.filter(t=>t.id===previous).node()?.focus();}
    const next=e.target.closest("[data-neighbor]");if(next)select(Number(next.dataset.neighbor));
  };
  dots.on("pointerenter focus",(_,t)=>{if(selected===null){hovered=t.id;refresh();}})
    .on("pointerleave blur",()=>{hovered=null;refresh();})
    .on("click",(_,t)=>select(t.id)).on("keydown",(e,t)=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select(t.id);}});
  const zoom=d3.zoom().scaleExtent([1,12])
    .filter(e=>e.type==='wheel' ? e.ctrlKey || e.metaKey : !e.button)
    .on("start",()=>{hovered=null;refresh();svg.style('cursor','grabbing');})
    .on("zoom",e=>{
      transform=e.transform;
      layer.attr("transform",transform);
      $('#graph-zoom-in').disabled=transform.k>=12;
      $('#graph-zoom-out').disabled=transform.k<=1;
      queueLabels();
    }).on("end",()=>svg.style('cursor',null));
  svg.call(zoom).on("dblclick.zoom",null);
  const animate=()=>svg.interrupt().transition().duration(reduceMotion()?0:280).ease(d3.easeCubicOut);
  $("#graph-zoom-in").onclick=()=>animate().call(zoom.scaleBy,1.5);
  $("#graph-zoom-out").onclick=()=>animate().call(zoom.scaleBy,1/1.5);
  $("#graph-zoom-reset").onclick=()=>{clear();animate().call(zoom.transform,d3.zoomIdentity);};
  $('#graph-zoom-out').disabled=true;
  svg.on('keydown',e=>{
    if(e.target!==svg.node())return;
    const control=({'+' : '#graph-zoom-in', '=' : '#graph-zoom-in', '-' : '#graph-zoom-out', '0' : '#graph-zoom-reset'})[e.key];
    if(control){e.preventDefault();$(control).click();}
  });
  mapResizeObserver=new ResizeObserver(measureLabels);
  mapResizeObserver.observe(svg.node());
  measureLabels();
  refresh();
  return {select,clear,filter:ids=>{matching=ids;clear();}};
}


async function start(){
try{
const initial=document.querySelector('#topic-graph-data');
T=initial?JSON.parse(initial.textContent):await json('/api/topics');
if(!window.d3)throw Error('Graph library could not load');
const map=drawMap();
$('#map-search').oninput=e=>{const q=e.target.value.trim().toLowerCase();const matches=T.topics.filter(t=>`${t.name} ${t.description}`.toLowerCase().includes(q));map.filter(new Set(matches.map(t=>t.id)));$('#map-results').textContent=q?`${matches.length} matching topics`:'';};
host.onkeydown=e=>{if(e.key==='Escape'){map.clear();$('#map-search').focus();}};
}catch(error){$('.connections-canvas').textContent='The topic graph could not load. Refresh to try again.';}
}
start();
})();
