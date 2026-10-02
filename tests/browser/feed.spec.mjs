import {test, expect} from '@playwright/test';

async function mockReader(page, {signedIn=true, interests=[3], failProfile=false}={}) {
  let active=signedIn, profile={id:'reader-test',email:'reader@example.com',version:0,topics:interests}, events=[];
  const seen=new Set();
  const topics=[{id:3,name:'Rust',slug:'current'},{id:4,name:'Databases',slug:'databases'}];
  const posts=Array.from({length:65},(_,i)=>({id:1000+i,title:`Interesting Rust story ${i}`,
    url:'https://example.com/'+i,time:1800000000-i*100,hn_points:100+i,topics:[3],article_key:i.toString(16).padStart(64,'0')}));
  await page.route('**/discovery-topics.json',route=>route.fulfill({json:{topics}}));
  await page.route('**/api/reader/**', async route => {
    const req=route.request(), path=new URL(req.url()).pathname;
    if(path.endsWith('/login'))return route.fulfill({json:{ok:true}});
    if(path.endsWith('/verify')){active=true;return route.fulfill({json:{ok:true}});}
    if(path.endsWith('/logout')){active=false;return route.fulfill({json:{ok:true}});}
    if(failProfile)return route.fulfill({status:503,json:{detail:'Your feed is temporarily unavailable.'}});
    if(!active)return route.fulfill({status:401,json:{detail:'Sign in to see your feed.'}});
    if(req.method()==='PUT'){profile={...profile,version:profile.version+1,topics:req.postDataJSON().topics};}
    if(req.method()==='DELETE'){active=false;return route.fulfill({json:{ok:true}});}
    return route.fulfill({json:profile});
  });
  await page.route(/\/api\/feed(?:\?.*)?$/,route=>{
    const offset=Number(new URL(route.request().url()).searchParams.get('cursor')||0);
    const all=posts.slice(offset).filter(p=>p.topics.some(t=>profile.topics.includes(t))&&!seen.has(p.article_key));
    const items=all.slice(0,20);
    return route.fulfill({json:{items,topics,updated_at:1800000000,
      topics_required:!profile.topics.length,next_cursor:all.length>20?String(items.at(-1).id-1000+1):null,caught_up:profile.topics.length>0&&all.length<=20}});
  });
  await page.route('**/api/feed/events', route => {
    const body=route.request().postDataJSON();events.push(body);
    for(const e of body.events)seen.add(e.article_key);
    return route.fulfill({json:{ok:true}});
  });
  return {events,seen};
}

test('daily feed paginates through unseen candidates, records visibility, and links to Explore', async ({page}) => {
  const {events}=await mockReader(page);
  await page.goto('/for-you/');
  await expect(page.locator('.feed-card')).toHaveCount(20);
  await expect(page.getByRole('button',{name:'Interests',exact:true})).toBeVisible();
  await expect(page.locator('#feed-account')).toBeVisible();
  await expect(page.getByRole('link',{name:'Discussion ↗'}).first()).toHaveAttribute('href',/https:\/\/news.ycombinator.com\/item\?id=/);
  await expect(page.locator('#feed-end')).not.toBeVisible();
  await expect.poll(()=>events.flatMap(x=>x.events).some(e=>e.type==='visible')).toBe(true);
  expect(events[0].events[0].article_key).toMatch(/^[a-f0-9]{64}$/);
  await page.getByRole('button',{name:'Show more',exact:true}).click();await expect(page.locator('.feed-card')).toHaveCount(40);
  await page.getByRole('button',{name:'Show more',exact:true}).click();await expect(page.locator('.feed-card')).toHaveCount(60);
  await page.getByRole('button',{name:'Show more',exact:true}).click();await expect(page.locator('.feed-card')).toHaveCount(65);
  await expect(page.getByRole('heading',{name:"You're caught up"})).toBeVisible();
  await expect(page.locator('#feed-more')).not.toBeVisible();
  await expect(page.locator('#feed-end a')).toHaveAttribute('href','/');
  expect(await page.locator('body').evaluate(el=>el.scrollWidth)).toBeLessThanOrEqual(page.viewportSize().width);
  await page.locator('#feed-end a').click();await expect(page).toHaveURL(/\/$/);
});

test('first sign-in chooses persistent topics and editing can produce an empty caught-up feed', async ({page}) => {
  await mockReader(page,{interests:[]});await page.goto('/for-you/');
  await expect(page.locator('#feed-topic-dialog')).toBeVisible();
  await expect(page.locator('#feed-topic-save')).toBeDisabled();
  await page.getByRole('button',{name:'Rust',exact:true}).click();
  await page.getByRole('button',{name:'Show my feed'}).click();
  await expect(page.locator('.feed-card')).toHaveCount(20);
  await page.getByRole('button',{name:'Interests'}).click();
  await page.getByRole('button',{name:'Rust',exact:true}).click();
  await page.getByRole('button',{name:'Databases',exact:true}).click();
  await page.getByRole('button',{name:'Show my feed'}).click();
  await expect(page.locator('.feed-card')).toHaveCount(0);
  await expect(page.getByRole('heading',{name:"You're caught up"})).toBeVisible();
  await page.reload();await expect(page.getByRole('button',{name:'Interests',exact:true})).toBeVisible();
  await expect(page.locator('#feed-topic-dialog')).not.toBeVisible();
});

test('email sign-in requires an explicit link confirmation and removes the token from the URL', async ({page}) => {
  await mockReader(page,{signedIn:false});
  await page.goto('/for-you/');await expect(page.getByRole('heading',{name:'Make it yours.'})).toBeVisible();
  await expect(page.locator('#feed-reading')).not.toBeVisible();
  await page.getByRole('textbox',{name:'Email address'}).fill('reader@example.com');
  await page.getByRole('button',{name:'Send sign-in link'}).click();await expect(page.locator('#feed-status')).toContainText('Check your email');
  await page.goto('/for-you/#token='+'a'.repeat(64));
  await expect(page).toHaveURL(/\/for-you\/$/);
  await expect(page.getByRole('button',{name:'Continue to my feed'})).toBeVisible();
  await expect(page.locator('#feed-reading')).not.toBeVisible();
  await page.getByRole('button',{name:'Continue to my feed'}).click();await expect(page.locator('.feed-card')).toHaveCount(20);
  await page.getByText('Account',{exact:true}).click();await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Make it yours.'})).toBeVisible();
});

test('profile failure offers retry rather than an anonymous personalized feed', async ({page}) => {
  await mockReader(page,{failProfile:true});await page.goto('/for-you/');
  await expect(page.locator('#feed-loading-copy')).toContainText('temporarily unavailable');
  await expect(page.getByRole('button',{name:'Try again'})).toBeVisible();
  await expect(page.locator('#feed-reading')).not.toBeVisible();
});


test('headlines open the original in a new tab and record interest once', async ({page, context}) => {
  const {events}=await mockReader(page);
  await context.route('https://example.com/**',route=>route.fulfill({contentType:'text/html',body:'<h1>Publisher article</h1>'}));
  await page.goto('/for-you/');
  const headline=page.locator('.feed-card h2 a').first();
  const url=await headline.getAttribute('href');
  for (let i=0;i<2;i++) {
    const popupPromise=page.waitForEvent('popup');
    await headline.click();
    const popup=await popupPromise;
    await expect(popup).toHaveURL(url);
    await expect(popup.getByRole('heading',{name:'Publisher article'})).toBeVisible();
    await popup.close();
  }
  await expect(page).toHaveURL(/\/for-you\/$/);
  await expect(page.locator('.feed-reader')).toHaveCount(0);
  await expect.poll(()=>events.flatMap(x=>x.events).filter(e=>e.type==='article_opened').length).toBe(1);
});


test('visible stories stay gone after reload and opened stories sync immediately', async ({page,context}) => {
  const {events}=await mockReader(page);
  await context.route('https://example.com/**',r=>r.fulfill({body:'Story'}));
  await page.goto('/for-you/');
  const headline=page.locator('.feed-card h2 a').first();const title=await headline.innerText();
  const popupPromise=page.waitForEvent('popup');await headline.click();const popup=await popupPromise;
  await expect.poll(()=>events.flatMap(x=>x.events).some(e=>e.type==='article_opened')).toBe(true);await popup.close();
  await page.reload();await expect(page.locator('.feed-card')).not.toHaveCount(0);
  await expect(page.getByRole('link',{name:title,exact:true})).toHaveCount(0);
});

test('pending seen writes survive reload and retry before requesting the feed', async ({page}) => {
  const {events}=await mockReader(page);
  let failing=true;
  await page.route('**/api/feed/events',route=>failing?route.fulfill({status:503,json:{}}):route.fallback());
  await page.goto('/for-you/');
  const title=await page.locator('.feed-card h2 a').first().innerText();
  await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('atlas-reading:reader-test')||'[]').length)).toBeGreaterThan(0);
  failing=false;await page.reload();
  await expect.poll(()=>events.flatMap(x=>x.events).length).toBeGreaterThan(0);
  await expect(page.getByRole('link',{name:title,exact:true})).toHaveCount(0);
});

test('interests search finds related topic names and preserves selections', async ({page}) => {
  await mockReader(page);
  const queries=[];
  await page.route('**/api/search/topics',route=>{
    queries.push(route.request().postDataJSON().q);
    return route.fulfill({json:{topics:[{id:4,name:'Databases',slug:'databases'}],notice:''}});
  });
  await page.goto('/for-you/');await page.getByRole('button',{name:'Interests',exact:true}).click();
  await page.getByRole('searchbox',{name:'Find topics'}).fill('persistent storage');
  await expect(page.getByRole('button',{name:'Databases',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Databases',exact:true}).click();
  await expect(page.locator('#feed-topic-count')).toHaveText('2 selected');
  await page.getByRole('searchbox',{name:'Find topics'}).fill('');
  await expect(page.getByRole('button',{name:'Rust',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.getByRole('button',{name:'Databases',exact:true})).toHaveAttribute('aria-pressed','true');
  expect(queries).toEqual(['persistent storage']);
});

test('topic search falls back to names and ignores a late response after clearing', async ({page}) => {
  await mockReader(page);
  let delayed;
  await page.route('**/api/search/topics',async route=>{
    if(route.request().postDataJSON().q==='Rust')return route.fulfill({status:503,json:{}});
    await new Promise(resolve=>{delayed=resolve;});
    await route.fulfill({json:{topics:[{id:4,name:'Databases',slug:'databases'}],notice:''}}).catch(()=>{});
  });
  await page.goto('/for-you/');await page.getByRole('button',{name:'Interests',exact:true}).click();
  const search=page.getByRole('searchbox',{name:'Find topics'});
  await search.fill('Rust');await expect(page.locator('#feed-topic-status')).toContainText('Showing name matches');
  await expect(page.getByRole('button',{name:'Rust',exact:true})).toBeVisible();
  await search.fill('persistent storage');await expect.poll(()=>Boolean(delayed)).toBe(true);
  await search.fill('');delayed();
  await expect(page.getByRole('button',{name:'Rust',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Databases',exact:true})).toBeVisible();
  await expect(page.locator('#feed-topic-status')).toBeEmpty();
});
