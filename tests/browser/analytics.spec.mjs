import { test, expect } from '@playwright/test';

async function openFinder(page) {
  const finder=page.locator('.topic-finder');
  if (!await finder.evaluate(element=>element.open)) await finder.locator('summary').click();
}

test('treemap controls and topic navigation', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/analytics/');
  await expect(page.locator('#play')).toBeEnabled();
  await expect(page.locator('#timeframe')).toHaveValue('month');
  await expect(page.locator('#summary')).toContainText('topic memberships');
  await page.locator('#latest').click();
  await expect(page.locator('#next')).toBeDisabled();
  await expect(page.locator('#summary')).toContainText('Partial month');
  const label = await page.locator('#week-label').textContent();
  await page.locator('#previous').click();
  await expect(page.locator('#week-label')).not.toHaveText(label);
  await page.locator('#week').focus();
  await page.keyboard.press('Home');
  await expect(page.locator('#previous')).toBeDisabled();
  await page.locator('#play').click();
  await expect(page.locator('#play')).toHaveText('Pause');
  await page.locator('#play').click();
  await expect(page.locator('#play')).toHaveText('Play');
  await expect(page.locator('.tile:visible').first()).toHaveAttribute('href', /\/topic\//);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('self-contained HTML works offline and interpolates period changes', async ({ page, request }) => {
  const response = await request.get('/analytics/');
  let html = await response.text();
  expect(html).toContain('id="analytics-data"');
  expect(html).not.toMatch(/<script[^>]+src=|<link[^>]+rel="stylesheet"/);
  // The CI archive has one topic, whose tile always fills the map. Give this
  // animation check two leading topics with changing shares so geometry moves.
  const payload = /(<script id="analytics-data" type="application\/json">)([\s\S]*?)(<\/script>)/;
  const match = html.match(payload);
  expect(match).not.toBeNull();
  const data = JSON.parse(match[2]);
  data.topics = [
    {id:1,name:'Topic alpha',slug:'topic-alpha'},
    {id:2,name:'Topic beta',slug:'topic-beta'},
    {id:3,name:'Topic gamma',slug:'topic-gamma'},
    {id:4,name:'Topic delta',slug:'topic-delta'},
  ];
  data.as_of = Date.parse('2026-04-15T00:00:00Z') / 1000;
  data.periods.month = {
    starts:['2026-01-01','2026-02-01','2026-03-01','2026-04-01'].map(d=>Date.parse(`${d}T00:00:00Z`)/1000),
    ends:['2026-02-01','2026-03-01','2026-04-01','2026-05-01'].map(d=>Date.parse(`${d}T00:00:00Z`)/1000),
    counts:[[8,7,0,0],[3,9,0,0],[6,4,0,0],[2,8,0,0]],
    picks:Array.from({length:4},()=>[null,null,null,null]),
  };
  html = html.replace(payload, (_,open,_json,close)=>open+JSON.stringify(data)+close);
  await page.route('**/*', route => route.abort());
  await page.setContent(html);
  await expect(page.locator('#play')).toBeEnabled();
  await page.locator('#coverage').selectOption('100');
  await expect(page.locator('.tile[aria-hidden="false"]')).toHaveCount(2);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const positions = () => page.locator('.tile').evaluateAll(tiles => tiles.map(t => t.style.transform + t.style.width));
  const before = await positions();
  await page.locator('#week').evaluate(input => {
    input.value = 0;
    input.dispatchEvent(new Event('input'));
  });
  await page.waitForTimeout(200);
  const during = await positions();
  await page.waitForTimeout(950);
  const after = await positions();
  expect(during).not.toEqual(before);
  expect(after).not.toEqual(during);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.locator('#latest').click();
  const immediate = await positions();
  await page.waitForTimeout(150);
  expect(await positions()).toEqual(immediate);
});

test('timeframe switches aggregation and playback steps', async ({ page }) => {
  const now = new Date('2026-09-29T00:00:00Z');
  await page.clock.install({ time: now });
  await page.clock.pauseAt(now);
  await page.goto('/analytics/');
  await expect(page.locator('#speed')).toHaveCount(0);
  for (const timeframe of ['month', 'year', 'week']) {
    await page.locator('#timeframe').selectOption(timeframe);
    await page.locator('#latest').click();
    const expected = await page.evaluate(timeframe => {
      const series = JSON.parse(document.getElementById('analytics-data').textContent).periods[timeframe];
      return {total: series.counts.at(-1).reduce((a,b)=>a+b,0).toLocaleString(), max: String(series.starts.length-1)};
    }, timeframe);
    await expect(page.locator('#summary')).toContainText(`${expected.total} topic memberships`);
    await expect(page.locator('#summary')).toContainText(`Partial ${timeframe}`);
    await expect(page.locator('#week')).toHaveAttribute('max', expected.max);
    await page.locator('#week').evaluate(input => {
      input.value = 0;
      input.dispatchEvent(new Event('input'));
    });
    await page.locator('#play').click();
    await page.clock.runFor(1250);
    await expect(page.locator('#week')).toHaveValue(String(Math.min(1,Number(expected.max))));
    if (await page.locator('#play').textContent() === 'Pause') await page.locator('#play').click();
  }
});

test('full topic name is visible on hover and keyboard focus', async ({ page }) => {
  await page.goto('/analytics/');
  const tile = page.locator('.tile[aria-hidden="false"]').first();
  const label = await tile.getAttribute('title');
  await tile.scrollIntoViewIfNeeded();
  await page.waitForTimeout(100);
  await tile.hover();
  await expect(page.locator('#tile-tooltip')).toBeVisible();
  await expect(page.locator('#tile-tooltip')).toHaveText(label);
  await page.keyboard.press('Escape');
  await expect(page.locator('#tile-tooltip')).toBeHidden();
  const compact = page.locator('.tile.compact[aria-hidden="false"]').first();
  const focusTile = await compact.count() ? compact : tile;
  await focusTile.scrollIntoViewIfNeeded();
  await page.waitForTimeout(100);
  await focusTile.focus();
  await expect(page.locator('#tile-tooltip')).toHaveText(await focusTile.getAttribute('title'));
  await expect(page.locator('#tile-tooltip')).toBeVisible();
  expect(await page.locator('#tile-tooltip').evaluate(el => {
    const r = el.getBoundingClientRect();
    return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight;
  })).toBe(true);
});

test('coverage controls show the leading active topics', async ({ page }) => {
  await page.goto('/analytics/');
  await expect(page.locator('#coverage')).toHaveValue('10');
  const expected = await page.evaluate(() => {
    const data=JSON.parse(document.getElementById('analytics-data').textContent);
    const index=Number(document.getElementById('week').value);
    return {total:data.topics.length,active:data.periods.month.counts[index].filter(Boolean).length};
  });
  for (const percent of [10,50,100]) {
    await page.locator('#coverage').selectOption(String(percent));
    const limit=Math.min(expected.active,Math.max(1,Math.ceil(expected.total*percent/100)));
    const shown=await page.locator('.tile[aria-hidden="false"]').count();
    expect(shown).toBeGreaterThanOrEqual(Math.min(1,expected.active));
    expect(shown).toBe(limit);
    await expect(page.locator('#coverage-note')).toContainText(`${shown} of ${expected.total} topics`);
  }
});

test('select, compare, search, zoom, and read a topic without losing the chart', async ({ page }) => {
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/analytics/');
  await expect(page.getByRole('navigation', {name:'Explore'})).toContainText('Connections');
  await expect(page.getByRole('navigation', {name:'Explore'})).toContainText('Trends');
  const shell=page.locator('#map-shell');
  expect((await shell.boundingBox()).y).toBeLessThan(450);
  const tile=page.locator('.tile[aria-hidden="false"]').first();
  const name=await tile.locator('strong').textContent();
  await tile.click();
  await expect(page).toHaveURL(/\/analytics\/$/);
  await expect(page.locator('#selection-title')).toHaveText(name);
  await expect(page.locator('#selection-history polyline')).toHaveCount(1);
  const change=await page.locator('#selection-change').textContent();
  expect(change).toMatch(/vs previous month|First recorded period|Period in progress/);
  await expect(page.locator('#selection-story a')).toHaveAttribute('href',/news.ycombinator.com/);
  const before=await page.locator('#selection-period').textContent();
  const step=await page.locator('#previous').isEnabled() ? '#previous' : '#next';
  const mobile=page.viewportSize().width<=600;
  if (mobile) await page.locator('#close-selection').click();
  await page.locator(step).click();
  if (mobile) {
    await openFinder(page);
    await page.locator('#topic-search').fill(name);
    await page.locator('.ranked-topic:visible').first().click();
  }
  await expect(page.locator('#selection-period')).not.toHaveText(before);
  await expect(page.locator('#selection-title')).toHaveText(name);
  await page.locator('#close-selection').click();
  await openFinder(page);
  await page.locator('#topic-search').fill(name);
  const match=page.locator('.ranked-topic:visible').first();
  await expect(match.locator('strong')).toHaveText(name);
  await match.click();
  await expect(page.locator('#selection-title')).toHaveText(name);
  await page.locator('#close-selection').click();
  await page.locator('#topic-search').fill('zzzz_no_topic');
  await expect(page.locator('#result-count')).toHaveText('0 topics found');
  await page.locator('#topic-search').press('Escape');
  await expect(page.locator('.ranked-topic:visible')).toHaveCount(await page.locator('.tile').count());
  const size=await page.locator('#treemap').evaluate(el=>el.style.width);
  await page.locator('#zoom-in').click();
  expect(await page.locator('#treemap').evaluate(el=>el.style.width)).not.toBe(size);
  await page.locator('#zoom-reset').click();
  await expect(page.locator('#zoom-out')).toBeDisabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.locator('#topic-search').fill(name);
  await page.locator('.ranked-topic:visible').first().click();
  await page.locator('#selection-open').click();
  await expect(page.getByRole('heading',{level:1})).toHaveText(name);
  expect(errors).toEqual([]);
});

test('zoom reveals labels without clipping their text', async ({ page }) => {
  await page.goto('/analytics/');
  const labelled=()=>page.locator('.tile[aria-hidden="false"]:not(.compact)').count();
  const before=await labelled();
  await page.locator('#zoom-in').click();
  await page.locator('#zoom-in').click();
  expect(await labelled()).toBeGreaterThanOrEqual(before);
  const clipped=await page.locator('.tile[aria-hidden="false"]:not(.compact) strong').evaluateAll(labels=>labels.filter(label=>{
    const tile=label.parentElement.getBoundingClientRect(), rect=label.getBoundingClientRect();
    return label.scrollHeight>label.clientHeight+1 || rect.bottom>tile.bottom+1;
  }).map(label=>label.textContent));
  expect(clipped).toEqual([]);
});
