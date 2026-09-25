"""Standalone, offline viewer for the observed weekly topic maps."""
import json
from pathlib import Path


def render(out=Path('data/comparison-2020')):
    maps=out/'maps'
    frames=[json.loads((maps/'stream-initial.json').read_text())]
    frames += [json.loads(p.read_text()) for p in sorted(maps.glob('week-*.json'))]
    static=json.loads((maps/'static-initial.json').read_text())
    weeks=[json.loads(line) for line in (out/'weeks.jsonl').read_text().splitlines()]
    payload=json.dumps(dict(frames=frames,static=static,events=[[]]+[w['events'] for w in weeks])).replace('<','\\u003c')
    page='''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HN topic map evolution</title><style>
body{font:15px system-ui;margin:32px auto;padding:0 24px;max-width:1300px;color:#202020;background:#fafafa}
h1{font-size:28px}button,input,select{font:inherit}button,select{padding:8px;border:1px solid #ddd;border-radius:6px;background:white}
#controls{display:flex;gap:16px;align-items:center;flex-wrap:wrap}input[type=range]{flex:1;min-width:220px;accent-color:#f60}
#summary{margin:18px 0;color:#555}#layout{display:grid;grid-template-columns:2fr 1fr;gap:24px}svg{width:100%;background:white;border:1px solid #ddd;border-radius:12px}
#detail{padding:16px;background:white;border:1px solid #ddd;border-radius:12px;min-height:170px}#list{columns:3;line-height:1.9}#list button{border:0;background:none;text-align:left;padding:2px;color:#333;cursor:pointer}
.new{color:#087b49!important}.changed{color:#7851a9!important}small{color:#666}line{stroke:#bbb;stroke-opacity:.3}circle{cursor:pointer;stroke:white;stroke-width:1.2}circle:hover{stroke:#111;stroke-width:2}
@media(max-width:800px){#layout{grid-template-columns:1fr}#list{columns:2}}
</style><h1>How the topic map evolves</h1>
<p>Weekly replay from a pre-2024 starting map. Compare its evolution with static clustering of all 2020–2026 stories.</p>
<div id="controls"><select id="mode"><option value="stream">Weekly streaming</option><option value="static">All-history static</option></select><input id="week" type="range" min="0" value="0"><b id="date"></b></div>
<div id="summary"></div><div id="layout"><svg id="map" viewBox="0 0 900 600" role="img" aria-label="Topic relationships"></svg><div><div id="detail">Select a topic to inspect its boundary and closest neighbors.</div><p><small>Green: new ID this week. Purple: renamed boundary. Orange: retained topic. Positions are the pipeline's saved coordinates, independently scaled for each build. Lines show three nearest semantic neighbors above 0.25 similarity; this is a diagnostic view, not the application's graph.</small></p></div></div>
<h2>Changes this week</h2><div id="changes"></div><h2>Topics</h2><div id="list"></div>
<script>const DATA=__PAYLOAD__;
const $=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
$('week').max=DATA.frames.length-1;
function el(tag,attrs={}){const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);return n}
function draw(){const fixed=$('mode').value==='static',i=+$('week').value,f=fixed?DATA.static:DATA.frames[i],prev=fixed?null:DATA.frames[i-1];$('week').disabled=fixed;
const old=new Map((prev?.nodes||[]).map(n=>[n.id,n])),nodes=new Map(f.nodes.map(n=>[n.id,n]));
$('date').textContent=new Date(f.at*1000).toISOString().slice(0,10);
const added=prev?f.nodes.filter(n=>!old.has(n.id)):[],removed=prev?prev.nodes.filter(n=>!nodes.has(n.id)):[],changed=prev?f.nodes.filter(n=>old.has(n.id)&&(old.get(n.id).name!==n.name||old.get(n.id).description!==n.description)):[];
$('summary').textContent=`${f.nodes.length} active topics`+(prev?` · ${added.length} new IDs · ${removed.length} retired IDs · ${changed.length} revised boundaries`:' · initial clustering');
const all=fixed?f.nodes:DATA.frames.flatMap(x=>x.nodes),xs=all.map(n=>n.x),ys=all.map(n=>n.y),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
const xy=n=>[35+(n.x-xmin)/(xmax-xmin||1)*830,35+(n.y-ymin)/(ymax-ymin||1)*530];
const svg=$('map');svg.replaceChildren();const seen=new Set();
for(const n of f.nodes)for(const near of n.neighbors){const other=nodes.get(near.id),key=[n.id,near.id].sort((a,b)=>a-b).join('-');if(!other||near.similarity<.25||seen.has(key))continue;seen.add(key);const[a,b]=xy(n),[c,d]=xy(other);svg.append(el('line',{x1:a,y1:b,x2:c,y2:d}))}
function detail(n){const box=$('detail');box.replaceChildren();const title=document.createElement('h3');title.textContent=n.name;box.append(title);for(const text of[n.description,`ID ${n.id} · ${Number(n.size).toLocaleString()} assigned stories`,`Nearest subjects: ${n.neighbors.map(x=>nodes.get(x.id)?.name).filter(Boolean).join(', ')}`]){const p=document.createElement('p');p.textContent=text;box.append(p)}}
const addedIds=new Set(added.map(n=>n.id)),changedIds=new Set(changed.map(n=>n.id));$('list').replaceChildren();
for(const n of [...f.nodes].sort((a,b)=>a.name.localeCompare(b.name))){const[x,y]=xy(n),color=addedIds.has(n.id)?'#087b49':changedIds.has(n.id)?'#7851a9':'#ff6600';const circle=el('circle',{cx:x,cy:y,r:4+Math.min(8,Math.log10(1+n.size)),fill:color,tabindex:0});const title=el('title');title.textContent=n.name;circle.append(title);circle.onclick=()=>detail(n);circle.onkeydown=e=>{if(e.key==='Enter')detail(n)};svg.append(circle);const p=document.createElement('div'),b=document.createElement('button');b.textContent=n.name;b.className=addedIds.has(n.id)?'new':changedIds.has(n.id)?'changed':'';b.onclick=()=>detail(n);p.append(b);$('list').append(p)}
const changes=$('changes');changes.replaceChildren();const lines=[];for(const n of added)lines.push(`Added: ${n.name}`);for(const n of removed)lines.push(`Retired: ${n.name}`);for(const n of changed)lines.push(`Revised: ${old.get(n.id).name} → ${n.name}`);if(!lines.length)lines.push(prev?'No structural changes this week.':'Starting taxonomy.');for(const text of lines){const p=document.createElement('div');p.textContent=text;changes.append(p)}
} $('week').oninput=draw;$('mode').onchange=draw;draw();</script></html>'''
    target=Path('report/production-readiness-2020-2026/map-evolution.html')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(page.replace('__PAYLOAD__',payload))
    print(target)


if __name__=='__main__':
    render()
