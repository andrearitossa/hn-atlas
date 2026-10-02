const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hn = id => `https://news.ycombinator.com/item?id=${id}`;
const safeURL = p => { try { const u = new URL(p.url); if (['https:', 'http:'].includes(u.protocol)) return u.href; } catch {} return hn(p.id); };
const date = time => new Date(time * 1000).toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric', timeZone:'UTC'});
let profile, data, cursor = null, loadingMore = false, draft = new Set(), generation = 0, tracker;
let topicMatches = null, topicSearchTimer, topicSearchController, topicSearchVersion = 0;
let magicToken = new URLSearchParams(location.hash.slice(1)).get('token');
if (magicToken) history.replaceState(null, '', location.pathname); // Never send the token in requests or referrers.
async function api(path, method = 'GET', body) {
  const response = await fetch('/api/reader/' + path, {method, credentials:'same-origin',
    headers: body ? {'Content-Type':'application/json'} : {}, body: body ? JSON.stringify(body) : undefined,
    cache:'no-store', signal:AbortSignal.timeout(12000)});
  let result;
  try { result = await response.json(); }
  catch { throw Error('Your feed is temporarily unavailable. Please try again shortly.'); }
  if (!response.ok) { const error = Error(result.detail || 'Please try again.'); error.status = response.status; throw error; }
  return result;
}
function message(text) { $('feed-status').textContent = text; }
function screen(name) {
  for (const id of ['feed-login', 'feed-verify', 'feed-reading', 'feed-loading']) $(id).hidden = id !== name;
}
function stopTracking(clear = false) { tracker?.stop(clear); tracker = null; }
function signedOut() {
  stopTracking(true); profile = undefined; generation++;
  $('feed-account').hidden = true; $('feed-edit').hidden = true; screen('feed-login');
  $('feed-posts').replaceChildren();
}
function tracking(account) {
  const storageKey = 'atlas-reading:' + account, pending = new Map(), seen = new Set(), opened = new Set(), timers = new Map();
  let active = true, scheduled, inFlight;
  try {
    for (const e of JSON.parse(localStorage.getItem(storageKey) || '[]')) {
      if (/^[a-f0-9]{64}$/.test(e.article_key) && ['visible','article_opened'].includes(e.type)
        && e.stamp > Date.now() - 90 * 86400000) pending.set(e.article_key, e);
    }
  } catch {}
  function persist() { try { localStorage.setItem(storageKey, JSON.stringify([...pending.values()])); } catch {} }
  function schedule() { clearTimeout(scheduled); if (active && pending.size) scheduled = setTimeout(flush, 1500); }
  function emit(type, key) {
    if (!active || !key) return;
    const recorded = type === 'article_opened' ? opened : seen;
    if (recorded.has(key)) return;
    recorded.add(key);
    const previous = pending.get(key);
    if (previous?.type !== 'article_opened') pending.set(key, {article_key:key, type, stamp:Date.now()});
    if (pending.size > 500) pending.delete(pending.keys().next().value);
    persist();
    if (type === 'article_opened') void flush(); else schedule();
  }
  async function flush() {
    if (inFlight) return inFlight;
    clearTimeout(scheduled);
    inFlight = (async () => {
      while (pending.size) {
        const batch = [...pending.values()].slice(0, 20);
        try {
          const response = await fetch('/api/feed/events', {method:'POST', credentials:'same-origin', keepalive:true,
            headers:{'Content-Type':'application/json'}, body:JSON.stringify({events:batch.map(({article_key,type}) => ({article_key,type}))}),
            signal:AbortSignal.timeout(8000)});
          if (!response.ok) throw Error('Reading history will sync when your connection is back.');
          for (const e of batch) if (pending.get(e.article_key) === e) pending.delete(e.article_key);
          if (active) persist();
        } catch {
          if (active) { message('Reading history is waiting to sync.'); scheduled = setTimeout(flush, 10000); }
          return;
        }
      }
    })();
    try { await inFlight; } finally { inFlight = null; }
  }
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      const card = entry.target, key = card.dataset.key;
      clearTimeout(timers.get(key)); timers.delete(key);
      if (entry.intersectionRatio >= .5 && !document.hidden && !seen.has(key)) {
        timers.set(key, setTimeout(() => {
          if (document.hidden || !active) return;
          emit('visible', key); observer.unobserve(card);
        }, 1000));
      }
    }
  }, {threshold:[0,.5]});
  function observe() { if (!document.hidden) for (const card of $('feed-posts').children) if (!seen.has(card.dataset.key)) observer.observe(card); }
  function visibility() {
    for (const timer of timers.values()) clearTimeout(timer); timers.clear(); observer.disconnect();
    if (document.hidden) void flush(); else observe();
  }
  document.addEventListener('visibilitychange', visibility);
  return {emit, observe, flush, has:key => pending.has(key), stop(clear = false) {
    active = false; observer.disconnect(); clearTimeout(scheduled);
    for (const timer of timers.values()) clearTimeout(timer);
    document.removeEventListener('visibilitychange', visibility);
    if (clear) { pending.clear(); try { localStorage.removeItem(storageKey); } catch {} }
  }};
}
function card(p) {
  const topic = data.topics.find(t => p.topics.includes(t.id) && profile.topics.includes(t.id));
  const url = safeURL(p), source = new URL(url).hostname.replace(/^www\./, '');
  return `<article class="feed-card" data-story="${p.id}" data-key="${esc(p.article_key)}"><a class="feed-card-topic" href="/topic/${esc(topic?.slug || '')}/">${esc(topic?.name || '')}</a>
    <h2><a href="${esc(url)}" target="_blank" rel="noopener" data-read="true">${esc(p.title)}</a></h2>
    <div class="feed-card-meta"><span>${esc(source)}</span><span>${date(p.time)}</span><span>${p.hn_points} HN points</span>
    <a class="feed-hn" href="${hn(p.id)}" target="_blank" rel="noopener" >Discussion ↗</a></div></article>`;
}
async function feedPage(after) {
  const response = await fetch('/api/feed' + (after ? '?cursor=' + encodeURIComponent(after) : ''),
    {credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(12000)});
  let result;
  try { result = await response.json(); } catch { throw Error('Stories are temporarily unavailable.'); }
  if (!response.ok) { const error = Error(result.detail || 'Stories are temporarily unavailable.'); error.status = response.status; throw error; }
  return result;
}
function appendPage(page) {
  const items = page.items.filter(p => !tracker?.has(p.article_key));
  $('feed-posts').insertAdjacentHTML('beforeend', items.map(card).join(''));
  cursor = page.next_cursor;
  $('feed-more').hidden = !cursor;
  $('feed-end').hidden = !page.caught_up;
  $('feed-end-note').textContent = 'Fresh picks arrive with the next daily update. Follow your curiosity on Explore.';
  tracker?.observe();
}
async function more() {
  if (loadingMore || !cursor) return;
  loadingMore = true; const current = generation, button = $('feed-more'); button.disabled = true;
  try {
    await tracker?.flush();
    const page = await feedPage(cursor);
    if (current === generation) appendPage(page);
  } catch (error) {
    if (current !== generation) return;
    if (error.status === 409) await load();
    else if (error.status === 401) signedOut();
    else message(error.message);
  } finally { loadingMore = false; button.disabled = false; }
}
function render(page) {
  screen('feed-reading');
  $('feed-edit').hidden = false; $('feed-edit').onclick = openTopics;
  $('feed-posts').replaceChildren(); appendPage(page);
}
function renderTopics() {
  const query = $('feed-topic-search').value.trim().toLowerCase();
  const matches = topicMatches ?? data.topics.filter(t => t.name.toLowerCase().includes(query));
  const ordered = topicMatches ? matches : [...matches].sort((a,b) => (query ? 0 : Number(draft.has(b.id))-Number(draft.has(a.id))) || a.name.localeCompare(b.name));
  $('feed-topic-list').innerHTML = ordered.map(t => `<button class="feed-topic-option" type="button" data-topic="${t.id}" title="${esc(t.description || '')}" aria-pressed="${draft.has(t.id)}">${esc(t.name)}</button>`).join('') || '<p class="feed-note">No matching topics. Try another word or idea.</p>';
  $('feed-topic-count').textContent = `${draft.size} selected`;
  $('feed-topic-save').disabled = !draft.size;
}
function cancelTopicSearch() {
  clearTimeout(topicSearchTimer); topicSearchController?.abort(); topicSearchVersion++;
}
function searchTopics() {
  cancelTopicSearch(); topicMatches = null; renderTopics();
  $('feed-topic-status').textContent = '';
  const q = $('feed-topic-search').value.trim();
  if (q.length < 2) return;
  const version = topicSearchVersion;
  $('feed-topic-status').textContent = 'Finding related topics…';
  topicSearchTimer = setTimeout(async () => {
    topicSearchController = new AbortController();
    try {
      const response = await fetch('/api/search/topics', {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({q}),
        signal:AbortSignal.any([topicSearchController.signal,AbortSignal.timeout(20000)])});
      const result = await response.json();
      if (version !== topicSearchVersion || !$('feed-topic-dialog').open) return;
      if (!response.ok) throw Error(result.detail || 'Topic search is temporarily unavailable.');
      if (!Array.isArray(result.topics)) throw Error('Topic search is temporarily unavailable.');
      const available = new Set(data.topics.map(t=>t.id));
      topicMatches = result.topics.filter(t=>available.has(t.id));
      renderTopics(); $('feed-topic-status').textContent = result.notice || '';
    } catch (error) {
      if (version !== topicSearchVersion || !$('feed-topic-dialog').open) return;
      $('feed-topic-status').textContent = 'Related topics are temporarily unavailable. Showing name matches.';
    }
  }, 350);
}
function openTopics() {
  if (!data || !profile) return;
  cancelTopicSearch(); topicMatches = null;
  draft = new Set(profile.topics.filter(id => data.topics.some(t => t.id === id)));
  $('feed-topic-search').value = ''; $('feed-topic-status').textContent = ''; renderTopics(); $('feed-topic-dialog').showModal();
}
async function load() {
  const current = ++generation; screen('feed-loading'); message('');
  try {
    await tracker?.flush(); stopTracking();
    const user = await api('');
    const response = await fetch('/discovery-topics.json', {signal:AbortSignal.timeout(12000)});
    if (!response.ok) throw Error('Topics are temporarily unavailable.');
    const catalog = await response.json();
    if (current !== generation) return;
    profile = user; data = catalog;
    tracker = tracking(user.id);
    await tracker.flush();
    const page = await feedPage();
    if (current !== generation) return;
      $('feed-email').textContent = profile.email; $('feed-account').hidden = false;
    render(page);
    if (page.topics_required || !profile.topics.some(id => data.topics.some(t => t.id === id))) openTopics();
  } catch (error) {
    if (current !== generation) return;
    if (error.status === 401) { signedOut(); message(''); }
    else { screen('feed-loading'); $('feed-loading-copy').textContent = error.message || 'Your feed is temporarily unavailable.'; $('feed-retry').hidden = false; }
  }
}
$('feed-login-form').onsubmit = async event => {
  event.preventDefault(); const button = $('feed-login-submit'); button.disabled = true; message('Sending your sign-in link…');
  try {
    await api('login', 'POST', {email:$('feed-login-email').value, website:$('feed-website').value});
    message('Check your email. Your sign-in link is valid for 15 minutes.');
  } catch (error) { message(error.message || 'We could not send your email. Please try again.'); }
  finally { button.disabled = false; }
};
$('feed-verify-button').onclick = async () => {
  const button = $('feed-verify-button'); button.disabled = true; message('Signing you in…');
  try { await api('verify', 'POST', {token:magicToken}); magicToken = null; await load(); }
  catch (error) { signedOut(); message(error.message || 'This link could not be used. Request a new one.'); }
  finally { button.disabled = false; }
};
$('feed-more').onclick = more;
$('feed-retry').onclick = () => { $('feed-loading-copy').textContent = 'Finding your next good read…'; $('feed-retry').hidden = true; load(); };
$('feed-posts').onclick = event => {
  const link = event.target.closest('[data-read]');
  if (link) tracker?.emit('article_opened', link.closest('[data-story]').dataset.key);
};
$('feed-posts').addEventListener('auxclick', event => {
  const link = event.target.closest('[data-read]');
  if (link && event.button === 1) tracker?.emit('article_opened', link.closest('[data-story]').dataset.key);
});
$('feed-topic-search').oninput = searchTopics;
$('feed-topic-dialog').addEventListener('close', cancelTopicSearch);
$('feed-topic-list').onclick = event => {
  const button = event.target.closest('[data-topic]'); if (!button) return;
  const id = Number(button.dataset.topic);
  if (draft.has(id)) draft.delete(id);
  else draft.add(id);
  renderTopics();
  $('feed-topic-list').querySelector(`[data-topic="${id}"]`)?.focus({preventScroll:true});
};
$('feed-topic-close').onclick = () => $('feed-topic-dialog').close();
$('feed-topic-save').onclick = async () => {
  const button = $('feed-topic-save'); button.disabled = true;
  try {
    const saved = await api('', 'PUT', {topics:[...draft].sort((a, b) => a - b), version:profile.version});
    profile = saved; $('feed-topic-dialog').close(); await load();
  } catch (error) {
    $('feed-topic-status').textContent = error.message || 'Your interests could not be saved. Try again.';
    if (error.status === 409) { $('feed-topic-dialog').close(); await load(); message(error.message); }
    if (error.status === 401) { $('feed-topic-dialog').close(); signedOut(); message(error.message); }
  } finally { button.disabled = !draft.size; }
};
$('feed-logout').onclick = async () => {
  try { await tracker?.flush(); await api('logout', 'POST', {}); $('feed-account').open = false; signedOut(); message('Signed out.'); }
  catch (error) { message(error.message || 'Could not sign out. Try again.'); }
};
$('feed-delete').onclick = () => { $('feed-account').open = false; $('feed-delete-dialog').showModal(); };
$('feed-delete-cancel').onclick = () => $('feed-delete-dialog').close();
$('feed-delete-confirm').onclick = async () => {
  const button = $('feed-delete-confirm'); button.disabled = true;
  try { await api('', 'DELETE', {}); $('feed-delete-dialog').close(); signedOut(); message('Your feed profile and reading history have been deleted.'); }
  catch (error) { $('feed-delete-status').textContent = error.message || 'Could not delete your profile. Try again.'; }
  finally { button.disabled = false; }
};
addEventListener('hashchange', () => {
  const incoming = new URLSearchParams(location.hash.slice(1)).get('token');
  if (!incoming) return;
  magicToken = incoming; history.replaceState(null, '', location.pathname);
  generation++; stopTracking(); $('feed-topic-dialog').close(); $('feed-account').hidden = true; $('feed-edit').hidden = true;
  screen('feed-verify'); message('');
});
addEventListener('pagehide', () => { void tracker?.flush(); });
if (magicToken) { screen('feed-verify'); } else load();
