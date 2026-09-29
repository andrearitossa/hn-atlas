(() => {
  'use strict';
  const prefix = document.currentScript.dataset.prefix || '';
  const $ = id => document.getElementById(prefix + id);
  const slider = $('week'), map = $('treemap'), shell = $('map-shell');
  const tooltip = $('tile-tooltip');
  const format = stamp => new Date(stamp * 1000).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
  let data, series, index = 0, timer = null, selected = null, returnFocus = null;
  let tree, displayed = null, frame = 0, activeTile = null, pointer = null;
  let scale = 1, panX = 0, panY = 0, drag = null, suppressClick = false;
  const tiles = [], rankButtons = [], textMetrics = new Map();
  let visibleTopics = new Set(), currentRects = new Map(), canvasHeight = 0;
  const measure = document.createElement('canvas').getContext('2d');
  let labelFont = 'bold 12px system-ui', lineHeight = 14.4, padding = 8;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const color = id => `hsl(${(Number(id)*137.508)%360} 42% 35%)`;
  const timeframe = () => $('timeframe').value;
  function periodLabel(i) {
    const start = series.starts[i];
    return timeframe() === 'week' ? `${format(start)} – ${format(series.ends[i]-86400)}` :
      new Date(start*1000).toLocaleDateString('en-GB',{year:'numeric',...(timeframe()==='month'?{month:'long'}:{}),timeZone:'UTC'});
  }
  function hideTooltip() {
    activeTile?.removeAttribute('aria-describedby'); activeTile = null; tooltip.hidden = true;
  }
  function updateTooltip() {
    if (!activeTile) return;
    if (activeTile.getAttribute('aria-hidden') === 'true') { hideTooltip(); return; }
    tooltip.textContent = activeTile.title; $('detail').textContent = activeTile.title; tooltip.hidden = false;
    const rect = activeTile.getBoundingClientRect();
    const x = pointer ? pointer.x : rect.left+rect.width/2, y = pointer ? pointer.y : rect.top;
    tooltip.style.left = `${Math.max(12,Math.min(x+12,innerWidth-tooltip.offsetWidth-12))}px`;
    tooltip.style.top = `${Math.max(12,Math.min(y+16,innerHeight-tooltip.offsetHeight-12))}px`;
  }
  function showTooltip(tile,event) {
    if (drag?.moved) return;
    if (activeTile !== tile) hideTooltip();
    activeTile = tile;
    pointer = event?.type.startsWith('pointer') ? {x:event.clientX,y:event.clientY} : null;
    tile.setAttribute('aria-describedby',prefix+'tile-tooltip'); updateTooltip();
  }
  function stop() { clearInterval(timer); timer = null; $('play').textContent = 'Play'; }
  // Fix topic groupings and split directions for every date and timeframe.
  function partition(items,width,height) {
    if (!items.length) return null;
    if (items.length === 1) return {id:items[0].id};
    const total = items.reduce((sum,t)=>sum+t.value,0);
    let split = 1, subtotal = items[0].value;
    while (split<items.length-1 && Math.abs(subtotal+items[split].value-total/2)<Math.abs(subtotal-total/2)) subtotal+=items[split++].value;
    const ratio=subtotal/total, horizontal=width>=height;
    return {horizontal,left:partition(items.slice(0,split),horizontal?width*ratio:width,horizontal?height:height*ratio),
      right:partition(items.slice(split),horizontal?width*(1-ratio):width,horizontal?height:height*(1-ratio))};
  }
  function weigh(node,values) {
    if (!node) return 0;
    return node.value=node.id!==undefined?values[node.id]:weigh(node.left,values)+weigh(node.right,values);
  }
  function titleHeight(id,width) {
    const available=Math.floor(width-padding*2-2), key=`${id}:${available}`;
    if (available<=0) return Infinity;
    if (textMetrics.has(key)) return textMetrics.get(key);
    measure.font=labelFont;
    let lines=1, line='';
    for (const word of data.topics[id].name.split(/\s+/)) {
      const next=line?`${line} ${word}`:word;
      if (measure.measureText(next).width<=available) {line=next;continue;}
      if(line){lines++;line='';}
      // Match CSS overflow-wrap:anywhere for narrow cells and long topic names.
      for(const character of word){
        if(line && measure.measureText(line+character).width>available){lines++;line='';}
        line+=character;
      }
    }
    if(textMetrics.size>40000)textMetrics.clear();
    const result=lines*lineHeight;
    textMetrics.set(key,result); return result;
  }
  function rectangles(node,x,y,width,height,result=new Map()) {
    if(!node)return result;
    if(node.id!==undefined){result.set(node.id,{x,y,width,height,opacity:1});return result;}
    const ratio=node.value?node.left.value/node.value:.5;
    if(node.horizontal){rectangles(node.left,x,y,width*ratio,height,result);rectangles(node.right,x+width*ratio,y,width*(1-ratio),height,result);}
    else{rectangles(node.left,x,y,width,height*ratio,result);rectangles(node.right,x,y+height*ratio,width,height*(1-ratio),result);}
    return result;
  }
  function paint(rects) {
    currentRects=rects;
    tiles.forEach((tile,id)=>{
      const r=rects.get(id);
      if(!r){tile.style.opacity='0';tile.style.width='0px';tile.style.height='0px';return;}
      const needed=titleHeight(id,r.width);
      tile.style.borderWidth=r.width<6||r.height<6?'0':'';
      Object.assign(tile.style,{transform:`translate(${r.x}px,${r.y}px)`,width:`${r.width}px`,height:`${r.height}px`,opacity:String(r.opacity)});
      tile.classList.toggle('compact',needed+padding*2+6>r.height);
      tile.classList.toggle('no-count',needed+padding*2+29>r.height || r.width<85);
    });
    map.style.width=`${shell.clientWidth*scale}px`;map.style.height=`${canvasHeight*scale}px`;
    map.style.transform='none';
    const clipped=tiles.filter(tile=>!tile.classList.contains('compact')&&tile.firstChild.scrollHeight+padding*2>tile.clientHeight);
    clipped.forEach(tile=>tile.classList.add('compact'));
  }
  function targetRects(values) {
    weigh(tree,values);return rectangles(tree,0,0,shell.clientWidth*scale,canvasHeight*scale);
  }
  function draw(values) {displayed=values;paint(targetRects(values));}
  function animate(counts) {
    cancelAnimationFrame(frame);
    const total=counts.reduce((a,b)=>a+b,0),target=counts.map(n=>total?n/total:0);
    const end=targetRects(target);
    if(!displayed||reducedMotion.matches){displayed=target;paint(end);return;}
    displayed=target;
    const from=new Map(currentRects),ids=new Set([...from.keys(),...end.keys()]),started=performance.now();
    function tick(now){
      const progress=Math.min(1,(now-started)/800),ease=progress*progress*(3-2*progress),next=new Map();
      for(const id of ids){
        const a=from.get(id)||{...end.get(id),opacity:0},b=end.get(id)||{...from.get(id),opacity:0},r={};
        for(const key of ['x','y','width','height','opacity'])r[key]=a[key]+(b[key]-a[key])*ease;
        next.set(id,r);
      }
      paint(progress===1?end:next);
      if(progress<1)frame=requestAnimationFrame(tick);
    }
    frame=requestAnimationFrame(tick);
  }
  function fitTopics(order,counts,limit){
    const candidates=order.filter(t=>t.count>0).slice(0,limit);
    canvasHeight=shell.clientHeight;
    tree=partition([...candidates].sort((a,b)=>a.i-b.i).map(t=>({id:t.i,value:t.count})),shell.clientWidth,canvasHeight);
    return new Set(candidates.map(t=>t.i));
  }
  function clampPan() {}
  function zoom(next) {
    cancelAnimationFrame(frame);hideTooltip();const old=scale;scale=Math.max(1,Math.min(8,next));
    const x=(shell.scrollLeft+shell.clientWidth/2)*scale/old-shell.clientWidth/2;
    const y=(shell.scrollTop+shell.clientHeight/2)*scale/old-shell.clientHeight/2;
    $('zoom-out').disabled=scale===1;$('zoom-in').disabled=scale===8;
    if(displayed)draw(displayed);
    shell.scrollLeft=scale===1?0:x;shell.scrollTop=scale===1?0:y;
  }
  function closeSelection() {
    $('selection-panel').hidden=true;selected=null;
    tiles.forEach(t=>t.classList.remove('selected'));
    rankButtons.forEach(t=>t.setAttribute('aria-pressed','false'));
    if(returnFocus?.isConnected) returnFocus.focus({preventScroll:true});
  }
  function selectTopic(i,source) {
    stop();hideTooltip();selected=i;returnFocus=source;
    $('selection-panel').hidden=false;
    updateSelection();
    tiles.forEach((t,j)=>t.classList.toggle('selected',i===j));
    rankButtons.forEach((t,j)=>t.setAttribute('aria-pressed',String(i===j)));
    $('selection-title').focus({preventScroll:true});
    if(innerWidth>800)$('selection-panel').scrollIntoView({behavior:reducedMotion.matches?'instant':'smooth',block:'nearest'});
  }
  function updateSelection() {
    if(selected===null) return;
    const topic=data.topics[selected], count=series.counts[index][selected];
    const total=series.counts[index].reduce((a,b)=>a+b,0), partial=series.ends[index]>data.as_of;
    $('selection-title').textContent=topic.name;
    $('selection-period').textContent=periodLabel(index)+(partial?' · Partial period':'');
    $('selection-count').textContent=count.toLocaleString();
    $('selection-share').textContent=`${total?(100*count/total).toFixed(1):'0.0'}%`;
    const previous=index?series.counts[index-1][selected]:null;
    const change=$('selection-change');
    if(partial) change.textContent='Period in progress · comparison available when complete.';
    else if(previous===null) change.textContent='First recorded period · no previous comparison.';
    else {
      const delta=count-previous, prefix=delta>0?'+':'';
      change.textContent=`${prefix}${delta.toLocaleString()} stories${previous?` (${prefix}${(100*delta/previous).toFixed(1)}%)`:''} vs previous ${timeframe()}${previous===0?' · previously zero':''}`;
    }
    const values=series.counts.map(row=>row[selected]), max=Math.max(1,...values), n=Math.max(1,values.length-1);
    const x=i=>10+i/n*280, y=value=>85-value/max*65;
    const svg=$('selection-history');
    svg.replaceChildren();
    const make=(tag,attrs)=>{const el=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v);svg.append(el);return el;};
    make('path',{d:`M10 85H290`,stroke:'var(--line)',fill:'none'});
    make('polyline',{points:values.map((v,i)=>`${x(i)},${y(v)}`).join(' '),fill:'none',stroke:color(topic.id),'stroke-width':2});
    make('line',{x1:x(index),x2:x(index),y1:15,y2:85,stroke:'var(--muted)','stroke-dasharray':'3 3'});
    make('circle',{cx:x(index),cy:y(count),r:4,fill:'var(--accent)'});
    make('text',{x:10,y:12,fill:'var(--muted)','font-size':10}).textContent=`${max.toLocaleString()} stories`;
    svg.setAttribute('aria-label',`${topic.name}: stories per ${timeframe()} across the archive. Selected: ${count} stories.`);
    $('selection-history-start').textContent=format(series.starts[0]);$('selection-history-end').textContent=format(series.starts.at(-1));
    $('selection-open').href=`/topic/${encodeURIComponent(topic.slug)}/`;
    const holder=$('selection-story');holder.replaceChildren();
    const story=data.stories?.[series.picks?.[index]?.[selected]];
    if(story) {
      const link=document.createElement('a');link.href=`https://news.ycombinator.com/item?id=${story.id}`;link.textContent=story.title||'Untitled story';
      const info=document.createElement('small');info.textContent=`${Number(story.score||0).toLocaleString()} HN points · ${format(story.time)}`;
      holder.append(link,info);
    } else holder.textContent='No stories in this period.';
  }
  function filterTopics() {
    const query=$('topic-search').value.trim().toLocaleLowerCase();let matches=0;
    data.topics.forEach((t,i)=>{const match=t.name.toLocaleLowerCase().includes(query);rankButtons[i].hidden=!match;tiles[i].classList.toggle('dimmed',!match);if(match)matches++;});
    $('result-count').textContent=`${matches} ${matches===1?'topic':'topics'}${query?' found':''}`;
  }
  function render() {
    const counts=series.counts[index], total=counts.reduce((a,b)=>a+b,0), partial=series.ends[index]>data.as_of;
    $('week-label').textContent=periodLabel(index);
    $('summary').textContent=`${total.toLocaleString()} topic memberships · ${counts.filter(Boolean).length} active topics${partial?` · Partial ${timeframe()}`:''}`;
    slider.value=index;slider.setAttribute('aria-valuetext',periodLabel(index));
    $('previous').disabled=index===0;$('next').disabled=index===series.starts.length-1;
    $('detail').textContent=total?'Hover for names. Select a topic for its history and stories.':'No filed stories in this period.';
    const order=counts.map((count,i)=>({count,i})).sort((a,b)=>b.count-a.count||a.i-b.i), maximum=Math.max(1,...counts);
    const limit=Math.max(1,Math.ceil(data.topics.length*Number($('coverage').value)/100));
    visibleTopics=fitTopics(order,counts,limit);
    const represented=[...visibleTopics].reduce((sum,i)=>sum+counts[i],0);
    $('coverage-note').textContent=`${visibleTopics.size} of ${data.topics.length} topics · ${total?(100*represented/total).toFixed(1):'0.0'}% of all topic memberships${canvasHeight>shell.clientHeight+1?' · scroll to explore':''}`;
    const rows=[];
    data.topics.forEach((topic,i)=>{
      const tile=tiles[i],count=counts[i],share=total?(100*count/total).toFixed(1):'0.0';
      const shown=visibleTopics.has(i);
      tile.tabIndex=shown?0:-1;tile.setAttribute('aria-hidden',String(!shown));tile.style.pointerEvents=shown?'':'none';
      tile.title=`${topic.name}: ${count.toLocaleString()} stories · ${share}%`;tile.setAttribute('aria-label',tile.title);
      tile.lastChild.textContent=`${count.toLocaleString()} · ${share}%`;
      rankButtons[i].querySelector('span').textContent=`${count.toLocaleString()} · ${share}%`;
      rankButtons[i].querySelector('i').style.width=`${100*count/maximum}%`;
      const tr=document.createElement('tr'),td=document.createElement('td'),link=document.createElement('a');
      link.href=tile.href;link.textContent=topic.name;td.append(link);tr.append(td);
      for(const value of [count.toLocaleString(),`${share}%`]){const cell=document.createElement('td');cell.textContent=value;tr.append(cell);}rows[i]=tr;
    });
    // Reorder existing controls without removing the keyboard focus from a selected topic.
    order.forEach(({i},position)=>{rankButtons[i].style.order=position;});
    $('topic-list').replaceChildren(...order.map(({i})=>rows[i]));
    filterTopics();updateTooltip();updateSelection();animate(counts.map((count,i)=>visibleTopics.has(i)?count:0));
  }
  function move(next) {index=Math.max(0,Math.min(series.starts.length-1,next));render();}
  function configurePeriod() {
    slider.max=series.starts.length-1;
    $('first-date').textContent=format(series.starts[0]);$('last-date').textContent=format(series.starts.at(-1));
    $('period-slider-label').textContent={week:'Week',month:'Month',year:'Year'}[timeframe()];
    $('previous').setAttribute('aria-label',`Previous ${timeframe()}`);$('next').setAttribute('aria-label',`Next ${timeframe()}`);
  }
  try {
    data=JSON.parse($('analytics-data').textContent);series=data.periods[timeframe()];
    tree=partition(data.topics.map((_,id)=>({id,value:Math.max(1,series.counts.reduce((sum,row)=>sum+row[id],0))})),shell.clientWidth,shell.clientHeight);
    data.topics.forEach((topic,i)=>{
      const tile=document.createElement('a');tile.className='tile';tile.href=`/topic/${encodeURIComponent(topic.slug)}/`;tile.style.background=color(topic.id);
      const title=document.createElement('strong');title.textContent=topic.name;tile.append(title,document.createElement('small'));
      tile.onpointerenter=tile.onpointermove=event=>showTooltip(tile,event);tile.onfocus=()=>showTooltip(tile);tile.onpointerleave=tile.onblur=hideTooltip;
      tile.onclick=event=>{if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();if(!suppressClick)selectTopic(i,tile);};
      map.append(tile);tiles.push(tile);
      const button=document.createElement('button');button.type='button';button.className='ranked-topic';button.setAttribute('aria-pressed','false');
      const name=document.createElement('strong');name.textContent=topic.name;const bar=document.createElement('i');bar.className='rank-bar';bar.style.background=color(topic.id);bar.setAttribute('aria-hidden','true');
      button.append(name,document.createElement('span'),bar);button.onclick=()=>selectTopic(i,button);$('ranked-list').append(button);rankButtons.push(button);
    });
    function readMetrics() {
      if(!tiles.length)return;const style=getComputedStyle(tiles[0].firstChild);
      labelFont=`${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;lineHeight=parseFloat(style.lineHeight)||14.4;padding=parseFloat(getComputedStyle(tiles[0]).getPropertyValue('--tile-padding'))||8;textMetrics.clear();
    }
    readMetrics();index=Math.max(0,series.starts.length-2);configurePeriod();
    for(const id of ['week','play','latest','timeframe'])$(id).disabled=false;
    slider.oninput=()=>{stop();move(Number(slider.value));};$('previous').onclick=()=>{stop();move(index-1);};$('next').onclick=()=>{stop();move(index+1);};$('latest').onclick=()=>{stop();move(series.starts.length-1);};
    $('play').onclick=()=>{if(timer){stop();return;}if(index===series.starts.length-1)move(0);$('play').textContent='Pause';timer=setInterval(()=>{move(index+1);if(index===series.starts.length-1)stop();},1200);};
    $('timeframe').onchange=()=>{const date=series.starts[index];stop();series=data.periods[timeframe()];const matching=series.starts.findIndex((start,i)=>start<=date&&date<series.ends[i]);index=Math.max(0,matching);configurePeriod();render();};
    $('coverage').onchange=()=>{stop();zoom(1);render();shell.scrollTop=0;};
    $('topic-search').oninput=filterTopics;$('topic-search').onkeydown=event=>{if(event.key==='Escape'){$('topic-search').value='';filterTopics();}};
    $('close-selection').onclick=closeSelection;$('selection-title').tabIndex=-1;
    $('zoom-in').onclick=()=>zoom(scale*1.5);$('zoom-out').onclick=()=>zoom(scale/1.5);$('zoom-reset').onclick=()=>zoom(1);zoom(1);
    document.addEventListener('keydown',event=>{if(event.key==='Escape'){hideTooltip();if(selected!==null)closeSelection();}});
    new IntersectionObserver(entries=>{if(!entries[0].isIntersecting)stop();}).observe(shell);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});document.addEventListener('scroll',hideTooltip,true);
    new ResizeObserver(()=>{readMetrics();clampPan();hideTooltip();if(displayed){displayed=null;render();}}).observe(shell);
    render();
  } catch(error) {
    $('week-label').textContent='Topic history could not load';$('summary').textContent='Reload this page to try again.';console.error(error);
  }
})();
