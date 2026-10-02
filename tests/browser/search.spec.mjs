import {test,expect} from '@playwright/test';
const story={id:123,title:'Rust compiler design',url:'https://example.com',time:1750000000,score:10,descendants:2,match:'Words + meaning'};
async function backend(page,notice=''){
 await page.route('**/api/search/',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(route.request().method()==='GET'?{count:92487,topics:[{id:3,name:'Current'}]}:{posts:[{...story,match:notice?'Text match':story.match}],notice})}));
}
test('Search uses one server endpoint and supports queries, topics, and shareable links',async({page})=>{
 await backend(page);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const assets=[];page.on('request',r=>assets.push(r.url()));
 await page.goto('/');await expect(page.locator('#browse-section')).toHaveAttribute('data-discovery-mounted','true');await expect(page.locator('#submit')).toBeEnabled();
 await page.locator('#query').fill('Rust compiler');await page.locator('#topic').selectOption('3');await page.locator('#submit').click();
 await expect(page.locator('.result')).toHaveCount(1);await expect(page).toHaveURL(/topic=3/);
 await page.locator('#sort').selectOption('points');await expect(page).toHaveURL(/sort=points/);
 await page.reload();await expect(page.locator('.result').first()).toBeVisible();
 expect(assets.some(u=>u.includes('search-data')||u.includes('search-worker'))).toBe(false);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
});
test('Smart displays the server fallback when meaning retrieval fails',async({page})=>{
 await backend(page,'Meaning search is unavailable right now. Showing text matches.');
 await page.goto('/');await expect(page.locator('#browse-section')).toHaveAttribute('data-discovery-mounted','true');await expect(page.locator('#submit')).toBeEnabled();
 await page.locator('#query').fill('Rust');await page.locator('#submit').click();
 await expect(page.locator('#notice')).toContainText('Showing text matches');await expect(page.locator('.match-tag').first()).toHaveText('Text match');
});

test('Hybrid is default and search mode is selectable and shareable',async({page})=>{
 await backend(page);await page.goto('/');await expect(page.locator('#browse-section')).toHaveAttribute('data-discovery-mounted','true');await expect(page.locator('#submit')).toBeEnabled();
 await expect(page.getByLabel('Hybrid',{exact:true})).toBeChecked();
 await page.locator('#query').fill('Rust');await page.getByLabel('Pattern matching',{exact:true}).check();
 await expect(page).toHaveURL(/mode=pattern/);await page.reload();
 await expect(page.getByLabel('Pattern matching',{exact:true})).toBeChecked();
 await page.getByLabel('Semantic',{exact:true}).check();await expect(page).toHaveURL(/mode=semantic/);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('One query explores topics, finds stories, and clears back to browsing',async({page})=>{
 await backend(page);let searches=0;page.on('request',r=>{if(r.url().endsWith('/api/search/')&&r.method()==='POST')searches++;});
 await page.goto('/');await expect(page.locator('#browse-section')).toHaveAttribute('data-discovery-mounted','true');await expect(page.locator('.topic-card').first()).toBeVisible();
 await page.getByRole('searchbox',{name:'Search topics and stories'}).fill('Rust');
 await expect(page.locator('.topic-card').first()).toBeVisible();expect(searches).toBe(0);
 await page.locator('#query').press('Enter');await expect(page.locator('.result')).toHaveCount(1);
 await expect(page.locator('.topic-card').first()).toBeVisible();expect(searches).toBe(1);
 await page.locator('#query').fill('compilers');await expect(page.locator('#stories-section')).toBeHidden();
 await page.getByRole('button',{name:'Clear search',exact:true}).click();await expect(page.locator('#query')).toBeFocused();
 await expect(page.locator('#query')).toHaveValue('');await expect(page.locator('.topic-card').first()).toBeVisible();
 await expect(page.locator('#search-options')).toBeHidden();await expect(page).not.toHaveURL(/q=/);
});
test('Typing during a slow search prevents stale results from replacing the new query',async({page})=>{
 await backend(page);await page.route('**/api/search/',async route=>{await new Promise(r=>setTimeout(r,500));await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({posts:[story],notice:''})});});
 await page.goto('/');await expect(page.locator('#browse-section')).toHaveAttribute('data-discovery-mounted','true');await page.locator('#query').fill('Rust');await page.locator('#submit').click();
 await page.locator('#query').fill('unrelated');await page.waitForTimeout(600);
 await expect(page.locator('.result')).toHaveCount(0);await expect(page.locator('#stories-section')).toBeHidden();
});

test('Topics landing page shares the same query and story controls',async({page})=>{
 await backend(page);await page.goto('/');await expect(page.locator('#query')).toBeVisible();
 await page.locator('#query').fill('Rust');await page.locator('#query').press('Enter');
 await expect(page.locator('.result')).toHaveCount(1);await expect(page).toHaveURL(/\/?q=Rust/);
 await expect(page.getByLabel('Hybrid',{exact:true})).toBeChecked();
 await page.getByLabel('Semantic',{exact:true}).check();await expect(page).toHaveURL(/mode=semantic/);
 await page.reload();await expect(page.locator('.result')).toHaveCount(1);
 await page.locator('#query').press('Escape');await expect(page.locator('#stories-section')).toBeHidden();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('A failed request keeps topic discovery available and offers retry',async({page})=>{
 await backend(page);let failed=true;
 await page.route('**/api/search/',route=>route.fulfill({status:failed?503:200,contentType:'application/json',body:JSON.stringify(failed?{detail:'Search is unavailable. Try again shortly.'}:{posts:[story],notice:''})}));
 await page.goto('/');await expect(page.locator('#browse-section')).toHaveAttribute('data-discovery-mounted','true');await page.locator('#query').fill('Rust');await page.locator('#query').press('Enter');
 await expect(page.locator('#retry-search')).toBeVisible();await expect(page.locator('.topic-card').first()).toBeVisible();
 failed=false;await page.locator('#retry-search').click();await expect(page.locator('.result')).toHaveCount(1);
});

test('Retrieved story memberships surface relevant topics beyond literal words',async({page})=>{
 await backend(page);
 await page.route('**/api/search/',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({posts:[{...story,topics:[3]}],notice:''})}));
 await page.goto('/');await expect(page.locator('#browse-section')).toHaveAttribute('data-discovery-mounted','true');await page.locator('#query').fill('a durable idea');
 await expect(page.locator('.topic-card')).toHaveCount(0);await page.locator('#query').press('Enter');
 await expect(page.locator('.topic-card').first()).toBeVisible();await expect(page.locator('.topic-card')).toContainText('Current');
});

test('A query typed while the shared module loads survives page initialization',async({page})=>{
 await backend(page);
 await page.route('**/search.js',async route=>{await new Promise(r=>setTimeout(r,700));await route.continue();});
 await page.goto('/',{waitUntil:'domcontentloaded'});
 await page.locator('#query').fill('Rust');
 await page.waitForTimeout(850);
 await expect(page.locator('#query')).toHaveValue('Rust');
 await expect(page.locator('#search-options')).toBeVisible();await page.locator('#query').press('Enter');
 await expect(page.locator('.result')).toHaveCount(1);
});
