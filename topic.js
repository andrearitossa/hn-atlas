/* Progressive enhancement: the complete topic is already readable in the HTML. */
const $ = selector => document.querySelector(selector);
const t = JSON.parse($('#topic-initial').textContent);
const root = new URL($('meta[name="hn-data"]').content, document.baseURI);
const esc = value => String(value ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const safeUrl = value => { try { const u = new URL(value); return /^https?:$/.test(u.protocol) ? u.href : ''; } catch { return ''; } };
const date = time => new Date(time * 1000).toLocaleDateString(undefined, {timeZone:'UTC',year:'numeric',month:'short',day:'numeric'});
const post = p => {
  const hn = `https://news.ycombinator.com/item?id=${p.id}`, url = safeUrl(p.url) || hn;
  return `<div class="post"><a href="${esc(url)}" target="_blank" rel="noopener">${esc(p.title)}</a><small><a href="${esc(url)}" target="_blank" rel="noopener">↗ ${esc(new URL(url).hostname.replace(/^www\./,''))}</a> · <a href="${hn}">HN · ${p.descendants || 0} comments</a> · ${p.score} points · ${date(p.time)}</small></div>`;
};
const cache = new Map();
const file = path => {
  if (!cache.has(path)) cache.set(path, fetch(new URL(path, root)).then(r => {
    if (!r.ok) throw Error('Request failed');
    return r.json();
  }).catch(e => {cache.delete(path); throw e;}));
  return cache.get(path);
};

// Signup stays out of the reading flow until explicitly opened.
document.querySelectorAll('.topic-follow-link').forEach(link => link.onclick = () => {
  $('#topic-updates').open = true;
});
if (['#topic-updates', '#topic-newsletter'].includes(location.hash)) $('#topic-updates').open = true;

// Native anchors and documents handle navigation. No router or startup fetches.
const form = $('#story-search'), results = $('#story-results'), more = $('#more-stories');
let offset = results.children.length, request = 0;
async function search(reset = true) {
  const current = ++request, fields = Object.fromEntries(new FormData(form));
  const days = Number(fields.days), year = Number(fields.year);
  const fold = value => (value || '').replace(/[A-Z]/g, c => c.toLowerCase());
  const query = fold(fields.q.trim());
  const start = reset ? 0 : offset;
  more.disabled = true;
  results.setAttribute('aria-busy','true');
  try {
    // Small recent window or individual year files; never a whole-topic archive file.
    const years = year ? [String(year)] : t.years.filter(y => !days || Number(y) >= new Date((t.as_of-days*86400)*1000).getUTCFullYear());
    const posts = days && days <= 30 ? await file(`recent/${t.id}.json`)
      : (await Promise.all(years.map(y => file(`archive/${t.id}/${y}.json`)))).flat();
    if (current !== request) return;
    const key = {newest:'time',top:'score',discussed:'descendants'}[fields.sort];
    const matches = posts.filter(p => (!year || new Date(p.time*1000).getUTCFullYear() === year)
      && (!days || p.time >= t.as_of-days*86400)
      && (!query || fold(p.title).includes(query) || fold(p.url).includes(query)))
      .sort((a,b) => (a[key] === null)-(b[key] === null) || b[key]-a[key] || b.id-a.id);
    if (reset) results.innerHTML = '';
    results.insertAdjacentHTML('beforeend', matches.slice(start,start+10).map(post).join(''));
    offset = start+10;
    more.hidden = offset >= matches.length;
    $('#story-status').textContent = results.children.length ? `${results.children.length} stories shown.` : 'No stories match. Try another search or period.';
    $('#archive-context').textContent = `${{top:'Highest rated',newest:'Newest',discussed:'Most discussed'}[fields.sort]} · ${year || (days ? `the last ${days} days` : 'all years')}${query ? ` · “${fields.q}”` : ''}`;
    $('#reset-search').hidden = !query && !year && days === 30 && fields.sort === 'top';
  } catch {
    if (current === request) $('#story-status').textContent = 'Could not update stories. Please try again.';
  } finally {
    if (current === request) {more.disabled = false; results.removeAttribute('aria-busy');}
  }
}
form.onsubmit = e => {e.preventDefault();search();};
form.onchange = e => {
  if (e.target.name === 'year') form.elements.days.value = '0';
  if (e.target.name === 'days') form.elements.year.value = '0';
  if (e.target.tagName === 'SELECT') search();
};
$('#reset-search').onclick = () => {form.reset();search();};
more.onclick = () => search(false);

// Every timeline level is rendered in the document; switching needs no network.
const periods = $('#zt-periods'), viewport = $('#zt-view'), slider = $('#zt-zoom');
const originalYears = document.createElement('template');
originalYears.content.append(...[...periods.children].map(p => p.cloneNode(true)));
const scales = [{key:'year',limit:2,name:'Big picture'}, {key:'year',limit:5,name:'Year by year'},
  {key:'month',limit:3,name:'Month by month'}, {key:'week',limit:3,name:'Week by week'}];
let level = 1;
function selectedPeriod() {
  const top = viewport.getBoundingClientRect().top;
  return [...periods.children].find(p => p.getBoundingClientRect().bottom > top+20);
}
function zoom(next) {
  next = Math.max(0,Math.min(3,next));
  const anchor = selectedPeriod()?.dataset.time;
  const scale = scales[next];
  try {
    if (next <= 1) {
      periods.replaceChildren(originalYears.content.cloneNode(true));
      if (next === 0) periods.querySelectorAll('.zt-stories').forEach(group => [...group.children].slice(2).forEach(p => p.hidden = true));
    } else {
      periods.replaceChildren($(`#zt-${scale.key}`).content.cloneNode(true));
    }
    level = next; slider.value = level;
    slider.setAttribute('aria-valuetext',scale.name);
    $('#zt-scale-name').textContent = scale.name;
    $('#zt-scale-hint').textContent = `Up to ${scale.limit} stories per ${scale.key}`;
    $('#zt-out').disabled = level === 0; $('#zt-in').disabled = level === 3;
    const target = [...periods.children].find(p => Number(p.dataset.time) <= Number(anchor));
    viewport.scrollTop = target ? target.offsetTop-periods.firstElementChild.offsetTop : 0;
    $('#timeline-status').textContent = '';
  } catch {slider.value=level;$('#timeline-status').textContent='Could not change timeline detail. Please try again.';}
}
$('#zt-out').onclick = () => zoom(level-1);
$('#zt-in').onclick = () => zoom(level+1);
slider.oninput = () => zoom(Number(slider.value));
$('#zt-year').onchange = e => {
  const target = periods.querySelector(`[data-year="${e.target.value}"]`);
  if (target) viewport.scrollTop = target.offsetTop-periods.firstElementChild.offsetTop;
};
$('#zt-reset').onclick = () => {zoom(1);viewport.scrollTop=0;};

// Small SVG chart, already rendered by the exporter. No visualization library.
function selectBar(event) {
  const bar = event.target.closest('[data-month]');
  if (bar) $('#chart-readout').textContent = bar.getAttribute('aria-label');
}
$('#activity-chart').onpointerover = selectBar;
$('#activity-chart').onfocusin = selectBar;
$('#activity-chart').onclick = selectBar;
document.querySelectorAll('[data-range]').forEach(button => button.onclick = () => {
  const count = Number(button.dataset.range), rows = count ? t.monthly.slice(-count) : t.monthly;
  const max = Math.max(1,...rows.map(r=>r.posts)), width = 900/Math.max(1,rows.length);
  $('#activity-chart').innerHTML = rows.map((r,i) => {
    const height=145*r.posts/max, label=`${r.month}: ${r.posts.toLocaleString()} posts`;
    return `<rect x="${50+i*width}" y="${170-height}" width="${Math.max(.5,width*.8)}" height="${height}" rx="2" tabindex="0" role="button" data-month="${r.month}" aria-label="${label}"><title>${label}</title></rect>`;
  }).join('');
  $('#chart-axis').innerHTML = rows.length ? `<span>${rows[0].month}</span><span>${rows.at(-1).month}</span>` : '';
  document.querySelectorAll('[data-range]').forEach(b=>{b.classList.toggle('on',b===button);b.setAttribute('aria-pressed',b===button);});
});

  if ($("#follow-form")) $("#follow-form").onsubmit = async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    const form = e.target, button = form.querySelector('button');
    if (button.disabled) return;
    button.disabled = true;
    const status = $("#follow-status"); status.textContent = "Saving…";
    try {
      const r = await fetch("/api/newsletter/subscribe", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({...data, topic:t.id})});
      const result = await r.json();
      if (!r.ok) throw Error(result.detail || "Could not subscribe");
      status.textContent = `You’ll receive weekly highlights from ${t.name}.`;
      form.reset();
    } catch (err) { status.textContent = "Could not save your email. Please try again. " + err.message; }
    finally { button.disabled = false; }
  };
// Feedback stays available across topic navigation; a native dialog handles focus and Escape.
const siteFooter = $('.site-footer');
const feedbackDialog = $('#feedback-dialog'), feedbackForm = $('#feedback-form');
const feedbackStatus = $('#feedback-status'), feedbackOpen = $('#feedback-open');
let feedbackId = null, feedbackPage = '#/', feedbackSent = false;
const feedbackAvailable = document.querySelector('meta[name="hn-feedback"]')?.content === 'cloudflare';
feedbackForm.querySelector('button[type="submit"]').disabled = !feedbackAvailable;
feedbackOpen.onclick = () => {
  if (feedbackSent) { feedbackForm.reset(); feedbackStatus.textContent = ''; feedbackForm.hidden = false; feedbackSent = false; }
  feedbackId ??= crypto.randomUUID();
  feedbackPage = location.pathname + location.search + location.hash;
  if (!feedbackAvailable) feedbackStatus.textContent = 'Feedback sending is available on the published site. This local preview does not send messages.';
  feedbackDialog.showModal();
};
$('#feedback-close').onclick = () => feedbackDialog.close();
feedbackForm.addEventListener('input', () => { feedbackId = crypto.randomUUID(); });
feedbackForm.onsubmit = async event => {
  event.preventDefault();
  if (!feedbackAvailable) return;
  const button = feedbackForm.querySelector('button[type="submit"]');
  if (button.disabled) return;
  button.disabled = true;
  feedbackStatus.textContent = 'Sending…';
  const fields = Object.fromEntries(new FormData(feedbackForm));
  const controls = [...feedbackForm.elements];
  controls.forEach(control => control.disabled = true);
  try {
    const response = await fetch('/api/feedback', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...fields, id:feedbackId, page:feedbackPage}),
      signal:AbortSignal.timeout(15000)
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || 'Please try again.');
    feedbackStatus.textContent = 'Thanks! Your feedback has been saved. — Andrea';
    feedbackForm.hidden = true;
    feedbackSent = true;
    feedbackId = null;
    $('#feedback-close').focus();
  } catch (error) {
    feedbackStatus.textContent = error.name === 'TimeoutError' ? 'The connection timed out. Please try again.' : 'Could not send. ' + error.message;
  } finally { controls.forEach(control => control.disabled = false); }
};
