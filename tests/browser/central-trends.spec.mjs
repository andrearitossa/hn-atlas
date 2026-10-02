import { test, expect } from '@playwright/test';

test('Topics opens the directory and Analytics opens a separate page', async ({ page }) => {
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('#browse-section')).toBeVisible();
  await expect(page.locator('#map-section')).toBeHidden();
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('link',{name:'Analytics',exact:true}).click();
  await expect(page).toHaveURL(/\/analytics\/$/);
  await expect(page.locator('#global-activity')).toBeVisible();
  await page.locator('#global-activity [data-range="12"]').click();
  await expect(page.locator('#activity-chart rect')).toHaveCount(12);
  await expect(page.locator('#topic-graph')).toHaveCount(1);
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('link',{name:'Topics',exact:true}).click();
  await expect(page).toHaveURL(/\/$/);
  expect(errors).toEqual([]);
});

test('embedded monthly treemap selects a topic and respects coverage', async ({ page }) => {
  await page.goto('/#/trends');
  const trends=page.locator('#trends-section');
  const tile=trends.locator('.tile[aria-hidden="false"]').first();
  await expect(tile).toBeVisible();
  const name=await tile.locator('strong').textContent();
  await tile.click();
  await expect(page).toHaveURL(/#\/trends$/);
  await expect(trends.locator('#tr-selection-title')).toHaveText(name);
  await expect(trends.locator('#tr-selection-history polyline')).toHaveCount(1);
  await trends.locator('#tr-close-selection').click();

  const expected=await trends.evaluate(section=>{
    const data=JSON.parse(section.querySelector('#tr-analytics-data').textContent);
    const index=Number(section.querySelector('#tr-week').value);
    return {total:data.topics.length,active:data.periods.month.counts[index].filter(Boolean).length};
  });
  for(const percent of [10,50,100]) {
    await trends.locator('#tr-coverage').selectOption(String(percent));
    const limit=Math.min(expected.active,Math.max(1,Math.ceil(expected.total*percent/100)));
    const shown=await trends.locator('.tile[aria-hidden="false"]').count();
    expect(shown).toBeGreaterThanOrEqual(Math.min(1,expected.active));
    expect(shown).toBe(limit);
    await expect(trends.locator('#tr-coverage-note')).toContainText(`${shown} of ${expected.total} topics`);
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('treemap fits the container without overlap at every coverage', async ({ page }) => {
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goto('/#/trends');
  for(const coverage of ['10','50','100']) {
    await page.locator('#tr-coverage').selectOption(coverage);
    const layout=await page.locator('#tr-map-shell').evaluate(shell=>{
      const css=getComputedStyle(shell), bounds=shell.getBoundingClientRect();
      const tiles=[...shell.querySelectorAll('.tile[aria-hidden="false"]')].map(tile=>{
        const r=tile.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,named:!tile.classList.contains('compact')};
      });
      return {tiles,width:parseFloat(css.getPropertyValue('--min-tile-width')),height:parseFloat(css.getPropertyValue('--min-tile-height')),bounds:{x:bounds.x,y:bounds.y,w:bounds.width,h:shell.clientHeight}};
    });
    const overlaps=[];
    for(const [i,a] of layout.tiles.entries()) {
      expect(a.w).toBeGreaterThan(0);
      expect(a.h).toBeGreaterThan(0);
      expect(a.x+a.w).toBeLessThanOrEqual(layout.bounds.x+layout.bounds.w+1);
      expect(a.y+a.h).toBeLessThanOrEqual(layout.bounds.y+layout.bounds.h+1);
      for(const b of layout.tiles.slice(i+1)) {
        const overlap=Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>.5 && Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>.5;
        if(overlap)overlaps.push([a,b]);
      }
    }
    expect(overlaps).toEqual([]);
  }
});


test('selected topic is beside the treemap on desktop and a bottom sheet on mobile', async ({ page }) => {
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goto('/#/trends');
  await page.locator('#tr-treemap .tile[aria-hidden="false"]').first().click();
  const panel=page.locator('#tr-selection-panel');
  await expect(panel).toBeVisible();
  const mobile=await page.evaluate(()=>innerWidth<=800);
  if(mobile){
    expect(await panel.evaluate(el=>getComputedStyle(el).position)).toBe('fixed');
    const bounds=await panel.boundingBox();
    expect(Math.abs(bounds.y+bounds.height-(await page.evaluate(()=>innerHeight)))).toBeLessThan(2);
  }else{
    await expect.poll(async()=>{
      const a=await panel.boundingBox(),b=await page.locator('#tr-map-shell').boundingBox();
      return a.x>=b.x+b.width-1 && a.y<b.y+b.height/2;
    }).toBe(true);
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('map scrollbars stay stable during animation and return after zoom reset', async ({ page }) => {
  await page.goto('/#/trends');
  await expect(page.locator('#tr-summary')).toContainText('topic memberships');
  const shell=page.locator('#tr-map-shell');
  await page.locator('#tr-coverage').selectOption('100');
  await page.locator('#tr-play').click();
  const states=await shell.evaluate(async element=>{
    const samples=[];
    const start=performance.now();
    while(performance.now()-start<3000){
      samples.push({width:element.clientWidth,height:element.clientHeight,
        horizontal:element.scrollWidth>element.clientWidth,vertical:element.scrollHeight>element.clientHeight});
      await new Promise(requestAnimationFrame);
    }
    return samples;
  });
  expect(states.length).toBeGreaterThan(20);
  expect(new Set(states.map(state=>JSON.stringify(state))).size).toBe(1);
  expect(states[0].horizontal).toBe(false);
  expect(states[0].vertical).toBe(false);
  await page.locator('#tr-play').click();
  await shell.getByRole('button',{name:'Zoom in',exact:true}).click();
  await expect(shell).toHaveClass(/is-zoomed/);
  await expect.poll(()=>shell.evaluate(el=>el.scrollWidth>el.clientWidth&&el.scrollHeight>el.clientHeight)).toBe(true);
  await shell.getByRole('button',{name:'Reset zoom',exact:true}).click();
  await expect(shell).not.toHaveClass(/is-zoomed/);
  await expect.poll(()=>shell.evaluate(el=>el.scrollWidth<=el.clientWidth&&el.scrollHeight<=el.clientHeight)).toBe(true);
});
